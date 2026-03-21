"""AI Energy Advisor for Smarting HOME."""
from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.core import HomeAssistant

from .const import (
    AI_GEMINI_MODEL,
    AI_CLAUDE_MODEL,
    AI_MAX_TOKENS,
    AI_TEMPERATURE,
    AI_RATE_LIMIT_CALLS,
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
    ) -> None:
        """Initialize the AI Advisor."""
        self.hass = hass
        self._gemini_key = gemini_api_key
        self._anthropic_key = anthropic_api_key
        self._call_timestamps: list[float] = []
        self._gemini_client: Any = None
        self._anthropic_client: Any = None

    @property
    def gemini_available(self) -> bool:
        """Return True if Gemini is configured."""
        return bool(self._gemini_key)

    @property
    def anthropic_available(self) -> bool:
        """Return True if Anthropic is configured."""
        return bool(self._anthropic_key)

    @property
    def any_available(self) -> bool:
        """Return True if any AI provider is available."""
        return self.gemini_available or self.anthropic_available

    def _check_rate_limit(self) -> bool:
        """Check if we're within rate limits."""
        now = time.time()
        self._call_timestamps = [
            t for t in self._call_timestamps
            if now - t < AI_RATE_LIMIT_WINDOW
        ]
        if len(self._call_timestamps) >= AI_RATE_LIMIT_CALLS:
            _LOGGER.warning(
                "AI rate limit reached (%d/%d calls in window)",
                len(self._call_timestamps),
                AI_RATE_LIMIT_CALLS,
            )
            return False
        return True

    def _build_context(self, data: dict[str, Any]) -> str:
        """Build context string from current energy data."""
        lines = [
            "=== SMARTING HOME — ENERGY SYSTEM STATUS ===",
            "",
            "## Current State",
            f"- PV Power: {data.get('pv_power', 0)} W",
            f"- Grid Power: {data.get('grid_power', 0)} W (positive=import, negative=export)",
            f"- Battery SOC: {data.get('battery_soc', 0)}%",
            f"- Battery Power: {data.get('battery_power', 0)} W (+charge, -discharge)",
            f"- Home Load: {data.get('load', 0)} W",
            f"- PV Surplus: {data.get('pv_surplus', 0)} W",
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
        ]
        return "\n".join(lines)

    async def ask_gemini(
        self,
        question: str,
        data: dict[str, Any],
    ) -> str:
        """Ask Google Gemini for energy advice."""
        if not self.gemini_available:
            return "Google Gemini is not configured. Add your API key in integration settings."

        if not self._check_rate_limit():
            return "Rate limit reached. Please try again later."

        try:
            import google.generativeai as genai

            if not self._gemini_client:
                genai.configure(api_key=self._gemini_key)
                self._gemini_client = genai.GenerativeModel(AI_GEMINI_MODEL)

            context = self._build_context(data)
            prompt = f"""You are an expert energy management advisor for a home solar+battery system in Poland.
Analyze the following system data and answer the user's question.
Provide specific, actionable recommendations.
Use Polish energy market knowledge (G13 tariff, RCE pricing, net-billing rules).
Keep your response concise (max 200 words).
Respond in the same language as the question.

{context}

User question: {question}"""

            self._call_timestamps.append(time.time())

            response = await self.hass.async_add_executor_job(
                lambda: self._gemini_client.generate_content(
                    prompt,
                    generation_config=genai.types.GenerationConfig(
                        max_output_tokens=AI_MAX_TOKENS,
                        temperature=AI_TEMPERATURE,
                    ),
                )
            )
            return response.text

        except ImportError:
            return "Google Generative AI library not installed. Run: pip install google-generativeai"
        except Exception as err:
            _LOGGER.error("Gemini API error: %s", err)
            return f"Gemini error: {err}"

    async def ask_anthropic(
        self,
        question: str,
        data: dict[str, Any],
    ) -> str:
        """Ask Anthropic Claude for energy advice."""
        if not self.anthropic_available:
            return "Anthropic Claude is not configured. Add your API key in integration settings."

        if not self._check_rate_limit():
            return "Rate limit reached. Please try again later."

        try:
            import anthropic

            if not self._anthropic_client:
                self._anthropic_client = anthropic.Anthropic(
                    api_key=self._anthropic_key
                )

            context = self._build_context(data)
            prompt = f"""You are an expert energy management advisor for a home solar+battery system in Poland.
Analyze the following system data and answer the user's question.
Provide specific, actionable recommendations.
Use Polish energy market knowledge (G13 tariff, RCE pricing, net-billing rules).
Keep your response concise (max 200 words).
Respond in the same language as the question.

{context}

User question: {question}"""

            self._call_timestamps.append(time.time())

            response = await self.hass.async_add_executor_job(
                lambda: self._anthropic_client.messages.create(
                    model=AI_CLAUDE_MODEL,
                    max_tokens=AI_MAX_TOKENS,
                    temperature=AI_TEMPERATURE,
                    messages=[{"role": "user", "content": prompt}],
                )
            )
            return response.content[0].text

        except ImportError:
            return "Anthropic library not installed. Run: pip install anthropic"
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
