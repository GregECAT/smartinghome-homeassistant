"""AI Energy Advisor for Smarting HOME."""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant

from .ai_providers import (
    AI_TASKS,
    PROVIDERS,
    PROVIDER_ANTHROPIC,
    PROVIDER_GEMINI,
    PROVIDER_LABELS,
    PROVIDER_OPENROUTER,
    SECRET_KEYS,
    AISecrets,
    Completion,
    ModelCatalog,
    async_complete,
    mask_key,
)
from .const import (
    AI_GEMINI_MODEL,
    AI_CLAUDE_MODEL,
    AI_MAX_TOKENS,
    AI_TEMPERATURE,
    AI_RATE_LIMIT_CALLS,
    AI_RATE_LIMIT_CONTROLLER,
    AI_RATE_LIMIT_WINDOW,
    G13_PRICES,
    G13Zone,
    DEFAULT_BATTERY_CAPACITY,
    PROVIDER_TARIFF_PRICES,
    ENERGY_PROVIDER_LABELS,
    DEFAULT_ENERGY_PROVIDER,
    CONF_ENERGY_PROVIDER,
)

_LOGGER = logging.getLogger(__name__)

AI_CONFIG_KEY = "ai_config"  # settings.json: {"default": {provider, model}, "tasks": {task: {...}}}
AI_ERROR_PREFIX = "AI error"

_FORMAT_INSTRUCTIONS = """
IMPORTANT — FORMAT YOUR RESPONSE as structured markdown for rich display:
- Use ## for main sections (e.g. ## 📊 Analiza bieżącej sytuacji, ## 🔋 Bateria, ## ⚡ Sieć, ## 🎯 Rekomendacje)
- Use ### for subsections
- Use numbered lists (1. 2. 3.) for step-by-step recommendations
- Use bullet points (- ) for details within sections
- Use **bold** for key values and emphasis
- Use > for important callout tips (prefix with ✅ for positive, ⚠️ for warning, ❌ for critical)
- Use **Rekomendacja:** prefix for main action items
- Use --- between major sections for visual separation
- Use tables | header | header | for comparative data when useful
- Keep each section focused and concise
"""


def is_ai_error(text: str) -> bool:
    """True for error strings returned instead of AI content."""
    return not text or text.startswith((
        AI_ERROR_PREFIX, "Gemini error", "Anthropic error", "No response",
        "No AI provider", "Rate limit reached",
    ))


class AIAdvisor:
    """AI-powered energy optimization advisor.

    Providers: Google Gemini, Anthropic Claude, OpenRouter (any model).
    Each task (HEMS advice, reports, autopilot, …) can use its own provider and
    model; on failure (quota, outage, retired model, bad key) the next
    configured provider is tried automatically.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        gemini_api_key: str = "",
        anthropic_api_key: str = "",
        gemini_model: str = "",
        anthropic_model: str = "",
    ) -> None:
        """Initialize the AI Advisor."""
        self.hass = hass
        self.secrets = AISecrets(hass)
        self.catalog = ModelCatalog(hass)
        # Legacy keys from the config entry (fallback when .storage has none)
        self._legacy_keys: dict[str, str] = {
            PROVIDER_GEMINI: gemini_api_key or "",
            PROVIDER_ANTHROPIC: anthropic_api_key or "",
        }
        # Default model per provider (legacy settings: gemini_model / anthropic_model)
        self._provider_models: dict[str, str] = {
            PROVIDER_GEMINI: gemini_model or AI_GEMINI_MODEL,
            PROVIDER_ANTHROPIC: anthropic_model or AI_CLAUDE_MODEL,
            PROVIDER_OPENROUTER: "",
        }
        self._ai_config: dict[str, Any] = {}
        self._default_provider: str = ""
        self._call_timestamps: list[float] = []  # advisory calls
        self._controller_timestamps: list[float] = []  # controller/strategist calls
        self.last_completion: Completion | None = None

    # ── Setup / config ────────────────────────────────────────────────

    async def async_setup(self) -> None:
        """Load secrets, migrate keys out of www/settings.json, load config."""
        await self.secrets.async_load()
        from .settings_io import read_async, write_async

        settings = await read_async(self.hass)
        legacy_file: dict[str, Any] = {}
        try:
            legacy_file = await self.hass.async_add_executor_job(self._read_legacy_settings)
        except Exception:  # noqa: BLE001
            pass
        imported = await self.secrets.async_merge_missing({
            SECRET_KEYS[PROVIDER_GEMINI]: settings.get("gemini_api_key")
            or legacy_file.get("gemini_api_key")
            or self._legacy_keys[PROVIDER_GEMINI],
            SECRET_KEYS[PROVIDER_ANTHROPIC]: settings.get("anthropic_api_key")
            or legacy_file.get("anthropic_api_key")
            or self._legacy_keys[PROVIDER_ANTHROPIC],
        })
        # www/ is served without authentication — never keep secrets there
        leaked = [k for k in ("gemini_api_key", "anthropic_api_key", "openrouter_api_key") if settings.get(k)]
        if leaked:
            await write_async(self.hass, {k: "" for k in leaked})
            _LOGGER.warning("Moved AI API keys out of www/smartinghome/settings.json: %s", leaked)
        if imported:
            _LOGGER.info("AI keys imported into private storage")
        self.apply_settings(settings)

    def _read_legacy_settings(self) -> dict[str, Any]:
        import json
        from pathlib import Path

        path = Path(self.hass.config.path("custom_components/smartinghome/settings.json"))
        return json.loads(path.read_text()) if path.exists() else {}

    def apply_settings(self, settings: dict[str, Any]) -> None:
        """Apply non-secret AI settings (models, per-task config) from settings.json."""
        if settings.get("gemini_model"):
            self._provider_models[PROVIDER_GEMINI] = settings["gemini_model"]
        if settings.get("anthropic_model"):
            self._provider_models[PROVIDER_ANTHROPIC] = settings["anthropic_model"]
        if settings.get("openrouter_model"):
            self._provider_models[PROVIDER_OPENROUTER] = settings["openrouter_model"]
        self._default_provider = settings.get("default_ai_provider", "") or ""
        cfg = settings.get(AI_CONFIG_KEY)
        self._ai_config = cfg if isinstance(cfg, dict) else {}

    def get_config(self) -> dict[str, Any]:
        """Effective AI config for the panel (no secrets)."""
        return {
            "providers": {
                p: {
                    "label": PROVIDER_LABELS[p],
                    "configured": bool(self.key(p)),
                    "masked": mask_key(self.key(p)),
                    "default_model": self._provider_models.get(p, ""),
                }
                for p in PROVIDERS
            },
            "tasks": AI_TASKS,
            "default": self._task_entry("default"),
            "assignments": {t: self._ai_config.get("tasks", {}).get(t, {}) for t in AI_TASKS if t != "default"},
            "last": {
                "provider": self.last_completion.provider,
                "model": self.last_completion.model,
                "error": self.last_completion.error,
            } if self.last_completion else None,
        }

    def key(self, provider: str) -> str:
        return self.secrets.get(provider) or self._legacy_keys.get(provider, "")

    @property
    def gemini_available(self) -> bool:
        return bool(self.key(PROVIDER_GEMINI))

    @property
    def anthropic_available(self) -> bool:
        return bool(self.key(PROVIDER_ANTHROPIC))

    @property
    def openrouter_available(self) -> bool:
        return bool(self.key(PROVIDER_OPENROUTER))

    @property
    def any_available(self) -> bool:
        return any(self.key(p) for p in PROVIDERS)

    def _task_entry(self, task: str) -> dict[str, str]:
        if task == "default":
            entry = self._ai_config.get("default") or {}
            provider = entry.get("provider") or self._default_provider
            if provider not in PROVIDERS:
                provider = next((p for p in PROVIDERS if self.key(p)), PROVIDER_GEMINI)
            return {"provider": provider, "model": entry.get("model") or self._provider_models.get(provider, "")}
        entry = (self._ai_config.get("tasks") or {}).get(task) or {}
        if entry.get("provider") in PROVIDERS:
            return {
                "provider": entry["provider"],
                "model": entry.get("model") or self._provider_models.get(entry["provider"], ""),
            }
        return self._task_entry("default")

    def _resolve_chain(self, task: str, provider: str | None = None) -> list[tuple[str, str]]:
        """Ordered (provider, model) candidates: explicit → task → default → others."""
        chain: list[tuple[str, str]] = []

        def add(p: str, m: str) -> None:
            if p in PROVIDERS and m and self.key(p) and (p, m) not in chain:
                chain.append((p, m))

        if provider in PROVIDERS:
            entry = self._task_entry(task)
            add(provider, entry["model"] if entry["provider"] == provider else self._provider_models.get(provider, ""))
        t = self._task_entry(task)
        add(t["provider"], t["model"])
        d = self._task_entry("default")
        add(d["provider"], d["model"])
        for p in PROVIDERS:  # automatic fallback to any other working provider
            add(p, self._provider_models.get(p, ""))
        return chain

    # ── Core call ─────────────────────────────────────────────────────

    async def complete(
        self,
        task: str,
        prompt: str,
        *,
        max_tokens: int = AI_MAX_TOKENS,
        temperature: float = AI_TEMPERATURE,
        json_mode: bool = False,
        timeout: int = 120,
        provider: str | None = None,
    ) -> Completion:
        chain = self._resolve_chain(task, None if provider in (None, "", "auto") else provider)
        if not chain:
            result = Completion(error="Brak skonfigurowanego dostawcy AI (klucz API i model)")
            self.last_completion = result
            return result
        result = Completion()
        failures: list[str] = []
        for prov, model in chain:
            result = await async_complete(
                self.hass, prov, self.key(prov), model, prompt,
                max_tokens=max_tokens, temperature=temperature,
                json_mode=json_mode, timeout=timeout,
            )
            if result.ok:
                if result.finish_reason in ("MAX_TOKENS", "max_tokens", "length"):
                    _LOGGER.warning("AI %s/%s response truncated (max tokens)", prov, model)
                break
            _LOGGER.warning("AI %s via %s/%s failed: %s", task, prov, model, result.error)
            failures.append(f"{PROVIDER_LABELS.get(prov, prov)} {model}: {result.error}")
            if not result.retryable_elsewhere:
                break
        if not result.ok and len(failures) > 1:
            result.error = " | ".join(failures)
        self.last_completion = result
        return result

    def _as_text(self, result: Completion) -> str:
        if result.ok:
            return result.text
        if " | " in result.error or not result.provider:
            return f"{AI_ERROR_PREFIX}: {result.error}"
        where = f"{PROVIDER_LABELS.get(result.provider, result.provider)} / {result.model}"
        return f"{AI_ERROR_PREFIX} ({where}): {result.error}"

    async def test_provider(
        self, provider: str, api_key: str | None = None, model: str | None = None
    ) -> dict[str, Any]:
        """Check a key: list models, then a tiny completion with the chosen model."""
        key = api_key if api_key else self.key(provider)
        models = await self.catalog.async_get(provider, key, refresh=True) if key else None
        model = model or self._provider_models.get(provider) or (
            models.models[0]["id"] if models and models.models else ""
        )
        if not key:
            return {"ok": False, "message": "Brak klucza API", "models": 0}
        result = await async_complete(
            self.hass, provider, key, model, "Reply with OK", max_tokens=16, temperature=0, timeout=30,
        )
        return {
            "ok": result.ok,
            "model": model,
            "models": len(models.models) if models else 0,
            "message": "OK" if result.ok else result.error,
            "models_error": models.error if models else "",
        }

    # Backwards-compatible key checks (services.test_api_key)
    async def test_gemini_key(self) -> bool:
        return (await self.test_provider(PROVIDER_GEMINI))["ok"]

    async def test_anthropic_key(self) -> bool:
        return (await self.test_provider(PROVIDER_ANTHROPIC))["ok"]

    def _check_rate_limit(self) -> bool:
        """Check if we're within rate limits (advisory calls)."""
        now = time.time()
        self._call_timestamps = [t for t in self._call_timestamps if now - t < AI_RATE_LIMIT_WINDOW]
        if len(self._call_timestamps) >= AI_RATE_LIMIT_CALLS:
            _LOGGER.warning("AI advisory rate limit reached (%d/%d calls in window)",
                            len(self._call_timestamps), AI_RATE_LIMIT_CALLS)
            return False
        return True

    def _check_controller_rate_limit(self) -> bool:
        """Check if we're within rate limits (controller/strategist calls)."""
        now = time.time()
        self._controller_timestamps = [t for t in self._controller_timestamps if now - t < AI_RATE_LIMIT_WINDOW]
        if len(self._controller_timestamps) >= AI_RATE_LIMIT_CONTROLLER:
            _LOGGER.warning("AI controller rate limit reached (%d/%d calls in window)",
                            len(self._controller_timestamps), AI_RATE_LIMIT_CONTROLLER)
            return False
        return True

    def _build_context(self, data: dict[str, Any]) -> str:
        """Build context string from current energy data."""
        now = datetime.now()
        day_names_pl = ['Poniedziałek', 'Wtorek', 'Środa', 'Czwartek', 'Piątek', 'Sobota', 'Niedziela']
        month_names_pl = ['', 'styczeń', 'luty', 'marzec', 'kwiecień', 'maj', 'czerwiec',
                          'lipiec', 'sierpień', 'wrzesień', 'październik', 'listopad', 'grudzień']
        season = 'zima' if now.month in (12, 1, 2) else 'wiosna' if now.month in (3, 4, 5) else 'lato' if now.month in (6, 7, 8) else 'jesień'
        # GoodWe convention: positive=EXPORT, negative=IMPORT (raw sensor)
        # GoodWe convention: NEGATIVE=charging (into battery), POSITIVE=discharging (from battery)
        # grid_power after inversion: POSITIVE=import, NEGATIVE=export (AI convention)
        raw_grid = data.get('grid_power', 0)
        try:
            grid_for_ai = float(raw_grid) if raw_grid is not None else 0
        except (ValueError, TypeError):
            grid_for_ai = 0
        raw_battery = data.get('battery_power', 0)
        try:
            raw_battery_f = float(raw_battery) if raw_battery is not None else 0
        except (ValueError, TypeError):
            raw_battery_f = 0
        # raw_battery_f used directly below for state classification
        if raw_battery_f < -50:
            battery_state = f"ŁADOWANIE {abs(raw_battery_f):.0f}W (bateria się ładuje z sieci/PV)"
        elif raw_battery_f > 50:
            battery_state = f"ROZŁADOWYWANIE {abs(raw_battery_f):.0f}W (bateria zasila dom)"
        else:
            battery_state = "BEZCZYNNA (idle)"
        
        if grid_for_ai > 50:
            grid_state = f"IMPORT {abs(grid_for_ai):.0f}W (pobór z sieci)"
        elif grid_for_ai < -50:
            grid_state = f"EKSPORT {abs(grid_for_ai):.0f}W (sprzedaż do sieci)"
        else:
            grid_state = "ZERO (brak przepływu)"
        lines = [
            "=== SMARTING HOME — ENERGY SYSTEM STATUS ===",
            "",
            "## Date & Time",
            f"- Date: {now.strftime('%Y-%m-%d')} ({day_names_pl[now.weekday()]})",
            f"- Time: {now.strftime('%H:%M')}",
            f"- Month: {month_names_pl[now.month]} {now.year}",
            f"- Season: {season}",
            "",
            "## Stan aktualny",
            f"- Produkcja PV: {data.get('pv_power', 0)} W",
            f"- Sieć: {grid_state}",
            f"- Bateria SOC: {data.get('battery_soc', 0)}%",
            f"- Bateria: {battery_state}",
            f"- Zużycie domu (Load): {data.get('load', 0)} W",
            f"- Nadwyżka PV: {data.get('pv_surplus', 0)} W",
            "",
            "## Weather",
            f"- Temperature: {data.get('weather_temp', 'N/A')}°C",
            f"- RealFeel: {data.get('weather_realfeel', 'N/A')}°C",
            f"- Cloud Coverage: {data.get('weather_clouds', 'N/A')}%",
            f"- Conditions: {data.get('weather_condition', 'N/A')}",
            f"- Sun Hours Today: {data.get('sun_hours_today', 'N/A')} h",
            f"- UV Index: {data.get('uv_index', 'N/A')}",
            "",
        ]

        # Tariff section — dynamic or tariff plan
        # Read provider from settings or config entry
        provider_key = DEFAULT_ENERGY_PROVIDER
        try:
            import json
            from pathlib import Path
            sp = Path(self.hass.config.path("custom_components/smartinghome/settings.json"))
            if sp.exists():
                s = json.loads(sp.read_text())
                provider_key = s.get("energy_provider", DEFAULT_ENERGY_PROVIDER)
        except Exception:
            pass
        # Also check config entry
        if provider_key == DEFAULT_ENERGY_PROVIDER:
            entries = self.hass.config_entries.async_entries("smartinghome")
            if entries:
                provider_key = entries[0].data.get(CONF_ENERGY_PROVIDER, DEFAULT_ENERGY_PROVIDER)

        prov_label = ENERGY_PROVIDER_LABELS.get(provider_key, str(provider_key).upper())

        if data.get("dynamic_zone"):
            lines.extend([
                f"## Tariff (Dynamic — {prov_label} Cena Dynamiczna)",
                "- Type: HOURLY DYNAMIC (price changes every hour based on ENTSO-E market)",
                f"- Current all-in price: {data.get('dynamic_buy_price', 0)} PLN/kWh",
                f"- Next hour price: {data.get('dynamic_next_price', 0)} PLN/kWh",
                f"- Today's range: {data.get('dynamic_min_today', 0)} – {data.get('dynamic_max_today', 0)} PLN/kWh",
                f"- Today's average: {data.get('dynamic_avg_today', 0)} PLN/kWh",
                f"- Current rank: {data.get('dynamic_rank', '?')}/24 (percentile: {data.get('dynamic_percentile', '?')}%)",
                f"- Zone: {data.get('dynamic_zone', 'unknown')}",
                "- STRATEGY: Buy when price < avg, sell/discharge battery when price > avg",
            ])
        elif data.get('g13_zone'):
            # G13 (Tauron)
            lines.extend([
                f"## Tariff ({prov_label} G13 2026)",
                f"- Current Zone: {data.get('g13_zone', 'unknown')}",
                f"- Current Price: {data.get('g13_price', 0)} PLN/kWh",
                f"- Off-peak: {G13_PRICES[G13Zone.OFF_PEAK]} PLN/kWh",
                f"- Morning peak: {G13_PRICES[G13Zone.MORNING_PEAK]} PLN/kWh",
                f"- Afternoon peak: {G13_PRICES[G13Zone.AFTERNOON_PEAK]} PLN/kWh",
            ])
        else:
            # Non-G13 tariff (G11, G12, G12w, G12n)
            tariff_name = data.get('tariff_name', 'unknown')
            tariff_prices = PROVIDER_TARIFF_PRICES.get(provider_key, {})
            tariff_data = tariff_prices.get(tariff_name, {})
            price_lines = [f"  - {k}: {v} PLN/kWh" for k, v in tariff_data.items()]
            lines.extend([
                f"## Tariff ({prov_label} {tariff_name} 2026)",
                f"- Provider: {prov_label}",
                f"- Current Price: {data.get('g13_price', data.get('tariff_price', 0))} PLN/kWh",
                *price_lines,
            ])

        lines.extend([
            "",
            "## RCE Dynamic Pricing",
            f"- Current RCE: {data.get('rce_price', 0)} PLN/MWh",
            f"- RCE Sell: {data.get('rce_sell', 0)} PLN/kWh",
            f"- Trend: {data.get('rce_trend', 'stable')}",
            f"- Level: {data.get('rce_level', 'normal')}",
            f"- Median today: {data.get('rce_median_today', 'N/A')} PLN/kWh",
            f"- Energy Compass (PDGSZ): {data.get('rce_compass', 'N/A')}",
            f"- Next period: {data.get('rce_next_period', 'N/A')} PLN/kWh",
            f"- Tomorrow avg: {data.get('rce_avg_tomorrow', 'N/A')} PLN/kWh",
            f"- Tomorrow vs today: {data.get('rce_tomorrow_vs_today_pct', 'N/A')}%",
            f"- Cheap window avg: {data.get('rce_cheap_window_avg', 'N/A')} PLN/kWh",
            f"- Expensive window avg: {data.get('rce_expensive_window_avg', 'N/A')} PLN/kWh",
            f"- Arbitrage margin: {data.get('rce_window_arbitrage_margin', 'N/A')} PLN/kWh",
            "",
            "## Battery",
            f"- Capacity: {DEFAULT_BATTERY_CAPACITY / 1000} kWh",
            f"- Available Energy: {data.get('battery_available', 0)} kWh",
            f"- Runtime: {data.get('battery_runtime', 0)} hours",
            "",
            "## Forecast",
            f"- PV Today Total: {data.get('forecast_today', 0)} kWh",
            f"- PV Remaining: {data.get('forecast_remaining', 0)} kWh",
            f"- PV Tomorrow: {data.get('forecast_tomorrow', 0)} kWh",
            "",
            "## Today's Economics",
            f"- Import Cost: {data.get('import_cost', 0)} PLN",
            f"- Export Revenue: {data.get('export_revenue', 0)} PLN",
            f"- Self-consumption Savings: {data.get('savings', 0)} PLN",
            f"- Autarky: {data.get('autarky', 0)}%",
            f"- Self-consumption: {data.get('self_consumption', 0)}%",
            "",
            "## HEMS Efficiency Score",
            f"- Current Score: {data.get('hems_score', 'N/A')} / 100",
            "- Score breakdown: autarky(30%), self-consumption(25%), battery(15%), tariff(15%), PV yield(15%)",
        ])
        return "\n".join(lines)


    # ── Public API (used by services, cron, autopilot) ────────────────

    def _advice_prompt(self, question: str, data: dict[str, Any]) -> str:
        return f"""You are an expert energy management advisor for a home solar+battery system in Poland.
Analyze the following system data and provide recommendations.
Use Polish energy market knowledge (G13 tariff, RCE pricing, net-billing rules).
Provide complete, actionable recommendations. Do NOT truncate your response.
Respond in Polish.
{_FORMAT_INSTRUCTIONS}
{self._build_context(data)}

User question: {question}"""

    async def ask(
        self, question: str, data: dict[str, Any], task: str = "ask", provider: str | None = None
    ) -> str:
        """Question + system context → markdown answer (or 'AI error …')."""
        if not self._check_rate_limit():
            return "Rate limit reached. Please try again later."
        self._call_timestamps.append(time.time())
        result = await self.complete(task, self._advice_prompt(question, data), provider=provider)
        return self._as_text(result)

    async def direct_ask(self, prompt: str, task: str = "ask", provider: str | None = None) -> str:
        """Prompt as-is (no system context overlay)."""
        if not self._check_rate_limit():
            return "Rate limit reached. Please try again later."
        self._call_timestamps.append(time.time())
        return self._as_text(await self.complete(task, prompt, provider=provider))

    # Legacy names
    async def ask_gemini(self, question: str, data: dict[str, Any]) -> str:
        return await self.ask(question, data, provider=PROVIDER_GEMINI)

    async def ask_anthropic(self, question: str, data: dict[str, Any]) -> str:
        return await self.ask(question, data, provider=PROVIDER_ANTHROPIC)

    async def _direct_ask_gemini(self, prompt: str) -> str:
        return await self.direct_ask(prompt, provider=PROVIDER_GEMINI)

    async def _direct_ask_anthropic(self, prompt: str) -> str:
        return await self.direct_ask(prompt, provider=PROVIDER_ANTHROPIC)

    async def get_optimization_advice(self, data: dict[str, Any]) -> str:
        """Get automated optimization advice using the configured AI."""
        question = (
            "Based on the current system state, PV forecast, and energy prices, "
            "what should I optimize in the next 4 hours? Consider: "
            "1) Should I charge or discharge the battery? "
            "2) Is it a good time to export to grid? "
            "3) Should I run high-power loads (boiler, AC)? "
            "4) Any arbitrage opportunities?"
        )
        return await self.ask(question, data, task="hems_advice")

    async def generate_daily_report(self, data: dict[str, Any]) -> str:
        """Generate a daily energy report using AI."""
        question = (
            "Generate a brief daily energy report for today. Include: "
            "1) Energy production summary "
            "2) Self-consumption rate and autarky "
            "3) Financial summary (costs, revenue, savings) "
            "4) Battery utilization assessment "
            "5) Tomorrow's recommendations based on forecast "
            "Format as a clean, readable report."
        )
        return await self.ask(question, data, task="daily_report")

    async def detect_anomalies(self, data: dict[str, Any]) -> str:
        """Detect anomalies in energy patterns."""
        question = (
            "Analyze the current system state for any anomalies or unusual patterns. "
            "Check for: "
            "1) Unusual power consumption "
            "2) Battery behavior issues "
            "3) Grid import/export imbalances "
            "4) Sensor reading inconsistencies "
            "Report only actual concerns, not normal operation."
        )
        return await self.ask(question, data, task="anomaly")

    async def ask_autopilot(
        self, prompt: str, data: dict[str, Any], provider: str = "auto"
    ) -> str:
        """Autopilot strategy analysis (prompt from autopilot_engine), long answer."""
        if not self._check_rate_limit():
            return "Rate limit reached. Please try again later."
        self._call_timestamps.append(time.time())
        result = await self.complete(
            "autopilot", prompt, max_tokens=16384, timeout=180, provider=provider,
        )
        return self._as_text(result)

    # ------------------------------------------------------------------
    #  AI Controller — JSON toolcalling for real-time inverter control
    # ------------------------------------------------------------------

    _CONTROLLER_MAX_TOKENS = 1024
    _CONTROLLER_NO_ACTION = {
        "reasoning": "AI unavailable — fallback to no_action",
        "commands": [{"tool": "no_action", "params": {"reason": "AI response error"}}],
        "next_check_minutes": 5,
    }
    _CONTROLLER_TOOLS = {
        "force_charge", "force_discharge", "stop_force_charge", "stop_force_discharge",
        "emergency_stop", "set_dod", "set_export_limit", "switch_on", "switch_off",
        "no_action", "charge_pv_only", "charge_from_grid", "set_general",
        "battery_to_home", "battery_hold",
    }

    async def ask_controller(
        self,
        prompt: str,
        provider: str = "auto",
        raw_json: bool = False,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Ask AI to return structured JSON commands for inverter control.

        Returns a parsed dict with keys: reasoning, commands, next_check_minutes.
        On ANY error, returns a safe no_action fallback.
        If raw_json=True, returns raw parsed JSON without controller validation.
        """
        import json as _json

        if not self._check_controller_rate_limit():
            return dict(self._CONTROLLER_NO_ACTION, reasoning="Rate limit reached")
        self._controller_timestamps.append(time.time())

        result = await self.complete(
            "autopilot", prompt,
            max_tokens=max_tokens or self._CONTROLLER_MAX_TOKENS,
            temperature=0.2, json_mode=True, timeout=90, provider=provider,
        )
        if not result.ok:
            return dict(self._CONTROLLER_NO_ACTION, reasoning=f"{AI_ERROR_PREFIX}: {result.error}")
        raw_text = result.text

        # Parse and validate JSON response
        if not raw_text.strip():
            return dict(self._CONTROLLER_NO_ACTION, reasoning="Empty AI response")

        # Raw JSON mode: skip controller validation (for Strategist)
        if raw_json:
            try:
                import json as _json2
                text2 = raw_text.strip()
                if text2.startswith("```"):
                    text2 = text2.split("\n", 1)[1] if "\n" in text2 else text2[3:]
                    if text2.endswith("```"):
                        text2 = text2[:-3]
                    text2 = text2.strip()
                parsed2 = _json2.loads(text2)
                if isinstance(parsed2, dict):
                    return parsed2
            except Exception as err2:
                _LOGGER.warning("AI Strategist: JSON parse error: %s | raw: %s", err2, raw_text[:200])
                # Attempt to repair truncated strategist JSON
                repaired = self._repair_truncated_strategist(raw_text)
                if repaired:
                    return repaired
            return {}

        try:
            # Strip markdown code fences if present
            text = raw_text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()

            # Normalize non-compliant key names BEFORE parsing
            # AI sometimes uses "analysis" or "explanation" instead of "reasoning"
            import re as _re
            text = _re.sub(r'"analysis"', '"reasoning"', text)
            text = _re.sub(r'"explanation"', '"reasoning"', text)
            text = _re.sub(r'"plan"', '"reasoning"', text)

            parsed = None
            try:
                parsed = _json.loads(text)
            except _json.JSONDecodeError:
                # Attempt JSON repair for truncated responses
                parsed = self._repair_truncated_json(text)

            if parsed is None:
                raise ValueError("Could not parse JSON")

            # Validate structure
            if not isinstance(parsed, dict):
                raise ValueError("Response is not a JSON object")
            if "commands" not in parsed:
                raise ValueError("Missing 'commands' key")
            if not isinstance(parsed["commands"], list):
                raise ValueError("'commands' is not a list")

            # Validate each command — supports both {"action":"id"} and {"tool":"name"}
            valid_tools = self._CONTROLLER_TOOLS
            validated_commands = []
            for cmd in parsed["commands"][:3]:  # Max 3 commands
                # Action-based command: pass through for controller to resolve
                action_id = cmd.get("action")
                if action_id:
                    validated_commands.append({"action": action_id})
                    continue

                # Raw tool command: validate tool name
                tool = cmd.get("tool", "")
                if tool not in valid_tools:
                    _LOGGER.warning("AI Controller: unknown tool '%s', skipping", tool)
                    continue
                validated_commands.append({
                    "tool": tool,
                    "params": cmd.get("params", {}),
                })

            if not validated_commands:
                validated_commands = [{"tool": "no_action", "params": {"reason": "No valid commands"}}]

            return {
                "reasoning": parsed.get("reasoning", "No reasoning provided"),
                "commands": validated_commands,
                "next_check_minutes": min(max(int(parsed.get("next_check_minutes", 5)), 2), 30),
            }

        except (ValueError, KeyError, _json.JSONDecodeError) as err:
            _LOGGER.warning("AI Controller: invalid JSON response: %s | raw: %s", err, raw_text[:200])
            return dict(self._CONTROLLER_NO_ACTION, reasoning=f"Invalid JSON: {err}")

    @staticmethod
    def _repair_truncated_json(text: str) -> dict | None:
        """Attempt to repair truncated JSON from AI response.

        Works by progressively adding closing brackets/braces.
        """
        import re

        # Try to find action IDs even in truncated text
        action_match = re.search(r'"action"\s*:\s*"([\w_]+)"', text)
        if action_match:
            action_id = action_match.group(1)
            _LOGGER.info("AI Controller: repaired truncated JSON, extracted action: %s", action_id)
            return {
                "reasoning": "(repaired from truncated response)",
                "commands": [{"action": action_id}],
                "next_check_minutes": 5,
            }

        # Try to find tool name
        tool_match = re.search(r'"tool"\s*:\s*"([\w_]+)"', text)
        if tool_match:
            tool_name = tool_match.group(1)
            _LOGGER.info("AI Controller: repaired truncated JSON, extracted tool: %s", tool_name)
            return {
                "reasoning": "(repaired from truncated response)",
                "commands": [{"tool": tool_name, "params": {}}],
                "next_check_minutes": 5,
            }

        # Fallback: if there's a "reasoning" or "analysis" key, return no_action
        # This handles ultra-short truncations like: { "reasoning": "Szczyt,
        reasoning_match = re.search(r'"(?:reasoning|analysis)"\s*:\s*"([^"]*)', text)
        if reasoning_match:
            snippet = reasoning_match.group(1)[:80]
            _LOGGER.info("AI Controller: repaired truncated JSON (no commands), reasoning: %s", snippet)
            return {
                "reasoning": f"(truncated) {snippet}",
                "commands": [{"tool": "no_action", "params": {"reason": "truncated response"}}],
                "next_check_minutes": 5,
            }

        # Last resort: if text starts with { but is too short, return no_action
        if text.strip().startswith("{") and len(text.strip()) < 100:
            _LOGGER.info("AI Controller: repaired ultra-short truncated JSON (%d chars)", len(text.strip()))
            return {
                "reasoning": "(ultra-short truncated response)",
                "commands": [{"tool": "no_action", "params": {"reason": "truncated"}}],
                "next_check_minutes": 5,
            }

        return None

    @staticmethod
    def _repair_truncated_strategist(raw_text: str) -> dict | None:
        """Attempt to repair truncated Strategist JSON.

        Extracts whatever time_blocks we can parse from the truncated text.
        Uses brace-balanced extraction to handle nested arrays/objects.
        """
        import re
        import json as _json

        # Find all brace-balanced objects that start with "start":
        blocks = []
        i = 0
        while i < len(raw_text):
            # Find next block start
            idx = raw_text.find('"start"', i)
            if idx < 0:
                break
            # Walk back to find the opening brace
            brace_start = raw_text.rfind('{', max(0, idx - 20), idx)
            if brace_start < 0:
                i = idx + 1
                continue
            # Walk forward counting braces to find matching close
            depth = 0
            j = brace_start
            while j < len(raw_text):
                if raw_text[j] == '{':
                    depth += 1
                elif raw_text[j] == '}':
                    depth -= 1
                    if depth == 0:
                        break
                j += 1

            if depth == 0 and j < len(raw_text):
                block_text = raw_text[brace_start:j + 1]
                try:
                    block = _json.loads(block_text)
                    if "start" in block and "end" in block:
                        blocks.append(block)
                except Exception:
                    pass
                i = j + 1
            else:
                # Truncated block — try a minimal regex extract
                m = re.search(
                    r'"start"\s*:\s*"(\d{2}:\d{2})".*?"end"\s*:\s*"(\d{2}:\d{2})"'
                    r'.*?"strategy"\s*:\s*"([^"]*)"',
                    raw_text[brace_start:], re.DOTALL,
                )
                if m:
                    blocks.append({
                        "start": m.group(1),
                        "end": m.group(2),
                        "strategy": m.group(3),
                        "commands": [],
                        "reasoning": "(naprawiony z przyciętej odpowiedzi)",
                    })
                break  # truncated, no more valid blocks

        if blocks:
            _LOGGER.info(
                "AI Strategist: repaired truncated JSON, extracted %d time_blocks",
                len(blocks),
            )
            # Extract analysis if present
            analysis_match = re.search(r'"analysis"\s*:\s*"([^"]*)', raw_text)
            analysis = analysis_match.group(1)[:200] if analysis_match else "(przycięte)"
            return {
                "time_blocks": blocks,
                "analysis": f"(naprawione) {analysis}",
                "next_analysis_minutes": 15,
            }

        return None
