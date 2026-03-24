"""AI Energy Advisor for Smarting HOME."""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant

from .const import (
    AI_GEMINI_MODEL,
    AI_GEMINI_MODELS,
    AI_CLAUDE_MODEL,
    AI_CLAUDE_MODELS,
    AI_MAX_TOKENS,
    AI_TEMPERATURE,
    AI_RATE_LIMIT_CALLS,
    AI_RATE_LIMIT_CONTROLLER,
    AI_RATE_LIMIT_WINDOW,
    G13_PRICES,
    G13Zone,
    DEFAULT_BATTERY_CAPACITY,
)

_LOGGER = logging.getLogger(__name__)


class AIAdvisor:
    """AI-powered energy optimization advisor.

    Supports Google Gemini and Anthropic Claude for:
    - Energy usage pattern analysis
    - Optimal charge/discharge scheduling
    - Weather-aware PV forecast adjustments
    - Natural language energy reports
    - Complex tariff arbitrage recommendations
    - Anomaly detection
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
        self._gemini_key = gemini_api_key
        self._anthropic_key = anthropic_api_key
        self._gemini_model = gemini_model or AI_GEMINI_MODEL
        self._anthropic_model = anthropic_model or AI_CLAUDE_MODEL
        self._call_timestamps: list[float] = []  # advisory calls
        self._controller_timestamps: list[float] = []  # controller/strategist calls
        self._gemini_client: Any = None
        self._anthropic_client: Any = None

    def refresh_keys(self) -> None:
        """Reload API keys from settings.json and config entry data."""
        import json
        from pathlib import Path
        from .const import CONF_GEMINI_API_KEY, CONF_ANTHROPIC_API_KEY

        # Try settings.json first
        settings_path = Path(self.hass.config.path("custom_components/smartinghome/settings.json"))
        stored = {}
        if settings_path.exists():
            try:
                stored = json.loads(settings_path.read_text())
            except Exception:
                pass

        # Try config entry data
        entry_data = {}
        entries = self.hass.config_entries.async_entries("smartinghome")
        if entries:
            entry_data = entries[0].data

        gk = (
            stored.get("gemini_api_key", "")
            or entry_data.get(CONF_GEMINI_API_KEY, "")
        )
        ak = (
            stored.get("anthropic_api_key", "")
            or entry_data.get(CONF_ANTHROPIC_API_KEY, "")
        )
        if gk:
            self._gemini_key = gk
        if ak:
            self._anthropic_key = ak

        # Also refresh models
        gm = stored.get("gemini_model", "")
        am = stored.get("anthropic_model", "")
        if gm:
            self._gemini_model = gm
        if am:
            import re
            am = re.sub(r"claude-sonnet-4[\.\-]6.*", "claude-sonnet-4-6", am)
            am = re.sub(r"claude-opus-4[\.\-]6.*", "claude-opus-4-6", am)
            am = re.sub(r"claude-haiku-[34][\.\-]5.*", "claude-3-5-haiku", am)
            self._anthropic_model = am

        _LOGGER.debug(
            "Keys refreshed: gemini=%s, anthropic=%s",
            "yes" if self._gemini_key else "no",
            "yes" if self._anthropic_key else "no",
        )

    @property
    def gemini_available(self) -> bool:
        """Return True if Gemini is configured."""
        if not self._gemini_key:
            self.refresh_keys()
        return bool(self._gemini_key)

    @property
    def anthropic_available(self) -> bool:
        """Return True if Anthropic is configured."""
        if not self._anthropic_key:
            self.refresh_keys()
        return bool(self._anthropic_key)

    @property
    def any_available(self) -> bool:
        """Return True if any AI provider is available."""
        return self.gemini_available or self.anthropic_available

    async def test_gemini_key(self) -> bool:
        """Test if the Gemini API key is valid via REST API."""
        try:
            import aiohttp
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{self._gemini_model}:generateContent?key={self._gemini_key}"
            )
            payload = {"contents": [{"parts": [{"text": "Reply with OK"}]}],
                       "generationConfig": {"maxOutputTokens": 10}}
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return bool(data.get("candidates"))
                    _LOGGER.error("Gemini test HTTP %s: %s", resp.status, await resp.text())
                    return False
        except Exception as err:
            _LOGGER.error("Gemini key test failed: %s", err)
            return False

    async def test_anthropic_key(self) -> bool:
        """Test if the Anthropic API key is valid via REST API."""
        try:
            import aiohttp
            url = "https://api.anthropic.com/v1/messages"
            headers = {
                "x-api-key": self._anthropic_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
            payload = {
                "model": self._anthropic_model,
                "max_tokens": 10,
                "messages": [{"role": "user", "content": "Reply with OK"}],
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers,
                                        timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return bool(data.get("content"))
                    _LOGGER.error("Anthropic test HTTP %s: %s", resp.status, await resp.text())
                    return False
        except Exception as err:
            _LOGGER.error("Anthropic key test failed: %s", err)
            return False

    def _check_rate_limit(self) -> bool:
        """Check if we're within rate limits (advisory calls)."""
        now = time.time()
        self._call_timestamps = [
            t for t in self._call_timestamps
            if now - t < AI_RATE_LIMIT_WINDOW
        ]
        if len(self._call_timestamps) >= AI_RATE_LIMIT_CALLS:
            _LOGGER.warning(
                "AI advisory rate limit reached (%d/%d calls in window)",
                len(self._call_timestamps),
                AI_RATE_LIMIT_CALLS,
            )
            return False
        _LOGGER.debug(
            "AI advisory rate budget: %d/%d used",
            len(self._call_timestamps), AI_RATE_LIMIT_CALLS,
        )
        return True

    def _check_controller_rate_limit(self) -> bool:
        """Check if we're within rate limits (controller/strategist calls)."""
        now = time.time()
        self._controller_timestamps = [
            t for t in self._controller_timestamps
            if now - t < AI_RATE_LIMIT_WINDOW
        ]
        if len(self._controller_timestamps) >= AI_RATE_LIMIT_CONTROLLER:
            _LOGGER.warning(
                "AI controller rate limit reached (%d/%d calls in window)",
                len(self._controller_timestamps),
                AI_RATE_LIMIT_CONTROLLER,
            )
            return False
        _LOGGER.debug(
            "AI controller rate budget: %d/%d used",
            len(self._controller_timestamps), AI_RATE_LIMIT_CONTROLLER,
        )
        return True

    def _build_context(self, data: dict[str, Any]) -> str:
        """Build context string from current energy data."""
        now = datetime.now()
        day_names_pl = ['Poniedziałek', 'Wtorek', 'Środa', 'Czwartek', 'Piątek', 'Sobota', 'Niedziela']
        month_names_pl = ['', 'styczeń', 'luty', 'marzec', 'kwiecień', 'maj', 'czerwiec',
                          'lipiec', 'sierpień', 'wrzesień', 'październik', 'listopad', 'grudzień']
        season = 'zima' if now.month in (12, 1, 2) else 'wiosna' if now.month in (3, 4, 5) else 'lato' if now.month in (6, 7, 8) else 'jesień'
        # GoodWe convention: negative=import, positive=export
        # Negate to match our AI description: positive=import, negative=export
        raw_grid = data.get('grid_power', 0)
        try:
            grid_for_ai = -float(raw_grid) if raw_grid is not None else 0
        except (ValueError, TypeError):
            grid_for_ai = 0
        # GoodWe convention: positive=discharge, negative=charge
        # Negate to match our AI description: positive=charge, negative=discharge
        raw_battery = data.get('battery_power', 0)
        try:
            raw_battery_f = float(raw_battery) if raw_battery is not None else 0
        except (ValueError, TypeError):
            raw_battery_f = 0
        battery_for_ai = -raw_battery_f  # flip sign for AI
        if raw_battery_f > 50:
            battery_state = "DISCHARGING (battery powers the home)"
        elif raw_battery_f < -50:
            battery_state = "CHARGING (battery absorbs power)"
        else:
            battery_state = "IDLE"
        lines = [
            "=== SMARTING HOME — ENERGY SYSTEM STATUS ===",
            "",
            "## Date & Time",
            f"- Date: {now.strftime('%Y-%m-%d')} ({day_names_pl[now.weekday()]})",
            f"- Time: {now.strftime('%H:%M')}",
            f"- Month: {month_names_pl[now.month]} {now.year}",
            f"- Season: {season}",
            "",
            "## Current State",
            f"- PV Power: {data.get('pv_power', 0)} W",
            f"- Grid Power: {grid_for_ai} W (positive=import from grid, negative=export to grid)",
            f"- Battery SOC: {data.get('battery_soc', 0)}%",
            f"- Battery Power: {battery_for_ai} W (positive=charging, negative=discharging)",
            f"- Battery State: {battery_state}",
            f"- Home Load: {data.get('load', 0)} W",
            f"- PV Surplus: {data.get('pv_surplus', 0)} W",
            "",
            "## Weather",
            f"- Temperature: {data.get('weather_temp', 'N/A')}°C",
            f"- RealFeel: {data.get('weather_realfeel', 'N/A')}°C",
            f"- Cloud Coverage: {data.get('weather_clouds', 'N/A')}%",
            f"- Conditions: {data.get('weather_condition', 'N/A')}",
            f"- Sun Hours Today: {data.get('sun_hours_today', 'N/A')} h",
            f"- UV Index: {data.get('uv_index', 'N/A')}",
            "",
            "## Tariff (G13 Tauron 2026)",
            f"- Current Zone: {data.get('g13_zone', 'unknown')}",
            f"- Current Price: {data.get('g13_price', 0)} PLN/kWh",
            f"- Off-peak: {G13_PRICES[G13Zone.OFF_PEAK]} PLN/kWh",
            f"- Morning peak: {G13_PRICES[G13Zone.MORNING_PEAK]} PLN/kWh",
            f"- Afternoon peak: {G13_PRICES[G13Zone.AFTERNOON_PEAK]} PLN/kWh",
            "",
            "## RCE Dynamic Pricing",
            f"- Current RCE: {data.get('rce_price', 0)} PLN/MWh",
            f"- RCE Sell: {data.get('rce_sell', 0)} PLN/kWh",
            f"- Trend: {data.get('rce_trend', 'stable')}",
            f"- Level: {data.get('rce_level', 'normal')}",
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
            f"- Score breakdown: autarky(30%), self-consumption(25%), battery(15%), tariff(15%), PV yield(15%)",
        ]
        return "\n".join(lines)

    async def ask_gemini(
        self,
        question: str,
        data: dict[str, Any],
    ) -> str:
        """Ask Google Gemini for energy advice via REST API."""
        if not self.gemini_available:
            return "Google Gemini is not configured. Add your API key in integration settings."

        if not self._check_rate_limit():
            return "Rate limit reached. Please try again later."

        try:
            import aiohttp

            context = self._build_context(data)
            format_instructions = """
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

Example structure:
## 📊 Analiza systemu
- **Moc PV:** 0 W
- **SOC baterii:** 10%
---
## 🎯 Rekomendacje na najbliższe 4 godziny
1. **Rekomendacja:** Zatrzymaj rozładowywanie baterii
> ✅ Bateria powinna ładować się z sieci w taniej taryfie
2. **Rekomendacja:** Nie eksportuj do sieci
> ⚠️ Brak produkcji PV — eksport jest niemożliwy
"""
            prompt = f"""You are an expert energy management advisor for a home solar+battery system in Poland.
Analyze the following system data and provide recommendations.
Use Polish energy market knowledge (G13 tariff, RCE pricing, net-billing rules).
Provide complete, actionable recommendations. Do NOT truncate your response.
Respond in Polish.
{format_instructions}
{context}

User question: {question}"""

            self._call_timestamps.append(time.time())

            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{self._gemini_model}:generateContent?key={self._gemini_key}"
            )
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "maxOutputTokens": AI_MAX_TOKENS,
                    "temperature": AI_TEMPERATURE,
                },
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload,
                                        timeout=aiohttp.ClientTimeout(total=120)) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        candidates = result.get("candidates", [])
                        if candidates:
                            finish_reason = candidates[0].get("finishReason", "UNKNOWN")
                            _LOGGER.debug("Gemini finish_reason: %s", finish_reason)
                            if finish_reason == "MAX_TOKENS":
                                _LOGGER.warning("Gemini response truncated (MAX_TOKENS). Consider increasing AI_MAX_TOKENS.")
                            parts = candidates[0].get("content", {}).get("parts", [])
                            if parts:
                                text = parts[0].get("text", "No response text.")
                                _LOGGER.debug("Gemini response length: %d chars", len(text))
                                return text
                        return "No response from Gemini."
                    err_text = await resp.text()
                    _LOGGER.error("Gemini API HTTP %s: %s", resp.status, err_text)
                    return f"Gemini error (HTTP {resp.status})"

        except Exception as err:
            _LOGGER.error(
                "Gemini API error [%s]: %s (key_len=%d, model=%s)",
                type(err).__name__, err,
                len(self._gemini_key), self._gemini_model,
                exc_info=True,
            )
            return f"Gemini error: {type(err).__name__}: {err}"

    async def ask_anthropic(
        self,
        question: str,
        data: dict[str, Any],
    ) -> str:
        """Ask Anthropic Claude for energy advice via REST API."""
        if not self.anthropic_available:
            return "Anthropic Claude is not configured. Add your API key in integration settings."

        if not self._check_rate_limit():
            return "Rate limit reached. Please try again later."

        try:
            import aiohttp

            context = self._build_context(data)
            format_instructions = """
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
            prompt = f"""You are an expert energy management advisor for a home solar+battery system in Poland.
Analyze the following system data and provide recommendations.
Use Polish energy market knowledge (G13 tariff, RCE pricing, net-billing rules).
Provide complete, actionable recommendations. Do NOT truncate your response.
Respond in Polish.
{format_instructions}
{context}

User question: {question}"""

            self._call_timestamps.append(time.time())

            url = "https://api.anthropic.com/v1/messages"
            headers = {
                "x-api-key": self._anthropic_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
            payload = {
                "model": self._anthropic_model,
                "max_tokens": AI_MAX_TOKENS,
                "temperature": AI_TEMPERATURE,
                "messages": [{"role": "user", "content": prompt}],
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers,
                                        timeout=aiohttp.ClientTimeout(total=120)) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        content = result.get("content", [])
                        if content:
                            return content[0].get("text", "No response text.")
                        return "No response from Anthropic."
                    err_text = await resp.text()
                    _LOGGER.error("Anthropic API HTTP %s: %s", resp.status, err_text)
                    return f"Anthropic error (HTTP {resp.status})"

        except Exception as err:
            _LOGGER.error("Anthropic API error: %s", err)
            return f"Anthropic error: {err}"

    async def get_optimization_advice(
        self, data: dict[str, Any]
    ) -> str:
        """Get automated optimization advice using the best available AI."""
        question = (
            "Based on the current system state, PV forecast, and energy prices, "
            "what should I optimize in the next 4 hours? Consider: "
            "1) Should I charge or discharge the battery? "
            "2) Is it a good time to export to grid? "
            "3) Should I run high-power loads (boiler, AC)? "
            "4) Any arbitrage opportunities?"
        )

        if self.gemini_available:
            return await self.ask_gemini(question, data)
        elif self.anthropic_available:
            return await self.ask_anthropic(question, data)
        else:
            return "No AI provider configured. Add Google Gemini or Anthropic Claude API key in settings."

    async def generate_daily_report(
        self, data: dict[str, Any]
    ) -> str:
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

        if self.gemini_available:
            return await self.ask_gemini(question, data)
        elif self.anthropic_available:
            return await self.ask_anthropic(question, data)
        else:
            return "No AI provider configured."

    async def detect_anomalies(
        self, data: dict[str, Any]
    ) -> str:
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

        if self.gemini_available:
            return await self.ask_gemini(question, data)
        elif self.anthropic_available:
            return await self.ask_anthropic(question, data)
        else:
            return "No AI provider configured."

    async def ask_autopilot(
        self,
        prompt: str,
        data: dict[str, Any],
        provider: str = "auto",
    ) -> str:
        """Ask AI to analyze and optimize an autopilot strategy.

        Uses a specialized prompt from autopilot_engine.build_autopilot_ai_prompt().
        Makes direct API calls to avoid adding redundant _build_context().
        Uses higher token limit for comprehensive autopilot analysis.
        """
        AUTOPILOT_MAX_TOKENS = 16384  # Autopilot needs more room than regular queries

        if not self._check_rate_limit():
            return "Rate limit reached. Please try again later."

        self._call_timestamps.append(time.time())

        # Resolve provider
        use_gemini = False
        use_anthropic = False
        if provider == "gemini" and self.gemini_available:
            use_gemini = True
        elif provider == "anthropic" and self.anthropic_available:
            use_anthropic = True
        elif provider == "auto":
            if self.gemini_available:
                use_gemini = True
            elif self.anthropic_available:
                use_anthropic = True

        if not use_gemini and not use_anthropic:
            return "No AI provider configured. Add Google Gemini or Anthropic Claude API key in settings."

        try:
            import aiohttp

            if use_gemini:
                url = (
                    f"https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{self._gemini_model}:generateContent?key={self._gemini_key}"
                )
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "maxOutputTokens": AUTOPILOT_MAX_TOKENS,
                        "temperature": AI_TEMPERATURE,
                    },
                }
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=payload,
                                            timeout=aiohttp.ClientTimeout(total=180)) as resp:
                        if resp.status == 200:
                            result = await resp.json()
                            candidates = result.get("candidates", [])
                            if candidates:
                                finish_reason = candidates[0].get("finishReason", "UNKNOWN")
                                if finish_reason == "MAX_TOKENS":
                                    _LOGGER.warning("Autopilot Gemini response truncated (MAX_TOKENS)")
                                parts = candidates[0].get("content", {}).get("parts", [])
                                if parts:
                                    return parts[0].get("text", "No response text.")
                            return "No response from Gemini."
                        err_text = await resp.text()
                        _LOGGER.error("Autopilot Gemini HTTP %s: %s", resp.status, err_text)
                        return f"Gemini error (HTTP {resp.status})"

            else:  # use_anthropic
                url = "https://api.anthropic.com/v1/messages"
                headers = {
                    "x-api-key": self._anthropic_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                }
                payload = {
                    "model": self._anthropic_model,
                    "max_tokens": AUTOPILOT_MAX_TOKENS,
                    "temperature": AI_TEMPERATURE,
                    "messages": [{"role": "user", "content": prompt}],
                }
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=payload, headers=headers,
                                            timeout=aiohttp.ClientTimeout(total=180)) as resp:
                        if resp.status == 200:
                            result = await resp.json()
                            content = result.get("content", [])
                            if content:
                                return content[0].get("text", "No response text.")
                            return "No response from Anthropic."
                        err_text = await resp.text()
                        _LOGGER.error("Autopilot Anthropic HTTP %s: %s", resp.status, err_text)
                        return f"Anthropic error (HTTP {resp.status})"

        except Exception as err:
            _LOGGER.error("Autopilot AI error [%s]: %s", type(err).__name__, err, exc_info=True)
            return f"Autopilot AI error: {type(err).__name__}: {err}"

    # ------------------------------------------------------------------
    #  AI Controller — JSON toolcalling for real-time inverter control
    # ------------------------------------------------------------------

    _CONTROLLER_MAX_TOKENS = 512
    _CONTROLLER_NO_ACTION = {
        "reasoning": "AI unavailable — fallback to no_action",
        "commands": [{"tool": "no_action", "params": {"reason": "AI response error"}}],
        "next_check_minutes": 5,
    }

    async def ask_controller(
        self,
        prompt: str,
        provider: str = "auto",
        raw_json: bool = False,
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

        # Resolve provider
        use_gemini = False
        use_anthropic = False
        if provider == "gemini" and self.gemini_available:
            use_gemini = True
        elif provider == "anthropic" and self.anthropic_available:
            use_anthropic = True
        elif provider == "auto":
            if self.gemini_available:
                use_gemini = True
            elif self.anthropic_available:
                use_anthropic = True

        if not use_gemini and not use_anthropic:
            return dict(self._CONTROLLER_NO_ACTION, reasoning="No AI provider configured")

        raw_text = ""
        try:
            import aiohttp

            if use_gemini:
                url = (
                    f"https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{self._gemini_model}:generateContent?key={self._gemini_key}"
                )
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "maxOutputTokens": self._CONTROLLER_MAX_TOKENS,
                        "temperature": 0.2,  # Lower temp for deterministic control
                        "responseMimeType": "application/json",
                    },
                }
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        url, json=payload,
                        timeout=aiohttp.ClientTimeout(total=45),  # Shorter timeout for control loop
                    ) as resp:
                        if resp.status == 200:
                            result = await resp.json()
                            candidates = result.get("candidates", [])
                            if candidates:
                                parts = candidates[0].get("content", {}).get("parts", [])
                                if parts:
                                    raw_text = parts[0].get("text", "")
                        else:
                            err_text = await resp.text()
                            _LOGGER.error("AI Controller Gemini HTTP %s: %s", resp.status, err_text)
                            return dict(self._CONTROLLER_NO_ACTION, reasoning=f"Gemini HTTP {resp.status}")

            else:  # use_anthropic
                url = "https://api.anthropic.com/v1/messages"
                headers = {
                    "x-api-key": self._anthropic_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                }
                payload = {
                    "model": self._anthropic_model,
                    "max_tokens": self._CONTROLLER_MAX_TOKENS,
                    "temperature": 0.2,
                    "messages": [{"role": "user", "content": prompt}],
                }
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        url, json=payload, headers=headers,
                        timeout=aiohttp.ClientTimeout(total=45),
                    ) as resp:
                        if resp.status == 200:
                            result = await resp.json()
                            content = result.get("content", [])
                            if content:
                                raw_text = content[0].get("text", "")
                        else:
                            err_text = await resp.text()
                            _LOGGER.error("AI Controller Anthropic HTTP %s: %s", resp.status, err_text)
                            return dict(self._CONTROLLER_NO_ACTION, reasoning=f"Anthropic HTTP {resp.status}")

        except Exception as err:
            _LOGGER.error("AI Controller error [%s]: %s", type(err).__name__, err)
            return dict(self._CONTROLLER_NO_ACTION, reasoning=f"Error: {err}")

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
                    if text2.endswith("```"): text2 = text2[:-3]
                    text2 = text2.strip()
                parsed2 = _json2.loads(text2)
                if isinstance(parsed2, dict):
                    return parsed2
            except Exception as err2:
                _LOGGER.warning("AI Strategist: JSON parse error: %s | raw: %s", err2, raw_text[:200])
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
            valid_tools = {"force_charge", "force_discharge", "set_dod",
                           "set_export_limit", "switch_on", "switch_off", "no_action"}
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
        import json as _json
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
