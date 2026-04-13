"""Dynamic tariff prompt renderer for AI prompts.

Generates tariff schedule/pricing sections for AI prompts based on the user's
configured tariff type, energy provider, and current season. Avoids hardcoding
G13-specific data into prompts, preventing AI hallucination on zone times.
"""
from __future__ import annotations

from datetime import datetime
from typing import Final

from .const import (
    TariffType,
    EnergyProvider,
    G13Zone,
    G13_PRICES,
    G13_WINTER_SCHEDULE,
    G13_SUMMER_SCHEDULE,
    TAURON_G11_PRICE,
    TAURON_G12_PRICES,
    TAURON_G12W_PRICES,
    PGE_G11_PRICE,
    PGE_G12_PRICES,
    PGE_G12W_PRICES,
    PGE_G12N_PRICES,
    PGE_G12_SUMMER,
    PGE_G12_WINTER,
    PGE_G12N_SCHEDULE,
    WINTER_MONTHS,
    DEFAULT_ENERGY_PROVIDER,
)


# ── Zone labels for Polish AI prompts ──
_ZONE_LABELS: Final = {
    "off_peak": "Off-peak (najtaniej)",
    "peak": "Szczyt (najdrożej)",
    "morning_peak": "Szczyt poranny",
    "afternoon_peak": "Szczyt popołudniowy (najdrożej)",
    "flat": "Stała cena",
}

# ── Tauron G12/G12W schedule (same for both) ──
# SUMMER (Apr-Sep): peak 06:00-13:00 + 15:00-22:00, off-peak rest
# WINTER (Oct-Mar): peak 06:00-13:00 + 15:00-22:00, off-peak rest
_TAURON_G12_SCHEDULE: Final = {
    "summer": {
        (6, 13): "peak",
        (13, 15): "off_peak",
        (15, 22): "peak",
        (22, 6): "off_peak",
    },
    "winter": {
        (6, 13): "peak",
        (13, 15): "off_peak",
        (15, 22): "peak",
        (22, 6): "off_peak",
    },
}


def _is_summer(month: int) -> bool:
    """Check if current month is in summer season (April-September)."""
    return month not in WINTER_MONTHS


def _season_label(month: int) -> str:
    """Return Polish season label for display."""
    return "Lato (kwi-wrz)" if _is_summer(month) else "Zima (paź-mar)"


def _get_current_g13_zone(hour: int, month: int, weekday: int) -> tuple[str, float]:
    """Get current G13 zone name and price."""
    if weekday >= 5:
        return G13Zone.OFF_PEAK, G13_PRICES[G13Zone.OFF_PEAK]

    schedule = G13_SUMMER_SCHEDULE if _is_summer(month) else G13_WINTER_SCHEDULE
    for (start, end), zone in schedule.items():
        if start < end:
            if start <= hour < end:
                return zone, G13_PRICES[zone]
        else:
            if hour >= start or hour < end:
                return zone, G13_PRICES[zone]
    return G13Zone.OFF_PEAK, G13_PRICES[G13Zone.OFF_PEAK]


def _get_current_g12_zone(hour: int, month: int, weekday: int, provider: str) -> tuple[str, float]:
    """Get current G12/G12W zone name and price."""
    if weekday >= 5:
        prices = TAURON_G12_PRICES if provider == EnergyProvider.TAURON else PGE_G12_PRICES
        return "off_peak", prices["off_peak"]

    if provider == EnergyProvider.PGE:
        sched = PGE_G12_SUMMER if _is_summer(month) else PGE_G12_WINTER
        prices = PGE_G12_PRICES
        for start, end in sched["off_peak_ranges"]:
            if start < end:
                if start <= hour < end:
                    return "off_peak", prices["off_peak"]
            else:
                if hour >= start or hour < end:
                    return "off_peak", prices["off_peak"]
        return "peak", prices["peak"]

    # Tauron G12
    prices = TAURON_G12_PRICES
    schedule = _TAURON_G12_SCHEDULE["summer" if _is_summer(month) else "winter"]
    for (start, end), zone in schedule.items():
        if start < end:
            if start <= hour < end:
                return zone, prices[zone]
        else:
            if hour >= start or hour < end:
                return zone, prices[zone]
    return "off_peak", prices["off_peak"]


def _get_current_g12n_zone(hour: int, weekday: int) -> tuple[str, float]:
    """Get current PGE G12N zone name and price."""
    prices = PGE_G12N_PRICES
    # Weekend = off-peak all day
    if weekday >= 6:  # Sunday
        return "off_peak", prices["off_peak"]
    # Saturday check
    if weekday == 5:
        return "off_peak", prices["off_peak"]

    # Weekday: peak 05:00-01:00, off-peak 01:00-05:00
    peak_start, peak_end = PGE_G12N_SCHEDULE["weekday_peak"]
    if peak_start < peak_end:
        if peak_start <= hour < peak_end:
            return "peak", prices["peak"]
    else:
        if hour >= peak_start or hour < peak_end:
            return "peak", prices["peak"]
    return "off_peak", prices["off_peak"]


# ═══════════════════════════════════════════════════════════════════════════
# Main renderer
# ═══════════════════════════════════════════════════════════════════════════

class TariffPromptContext:
    """Holds all tariff data needed by AI prompts."""

    __slots__ = (
        "tariff_type", "provider", "season", "is_weekend",
        "current_zone", "current_price",
        "schedule_text", "prices_text", "prices_summary",
        "cheapest_price", "most_expensive_price",
        "arbitrage_margin", "arbitrage_text",
        "key_rules_text", "warning_text",
        "next_expensive_zone_text",
        "precomputed_advisory_facts",
    )

    def __init__(self) -> None:
        self.tariff_type: str = ""
        self.provider: str = ""
        self.season: str = ""
        self.is_weekend: bool = False
        self.current_zone: str = ""
        self.current_price: float = 0.0
        self.schedule_text: str = ""
        self.prices_text: str = ""
        self.prices_summary: str = ""
        self.cheapest_price: float = 0.0
        self.most_expensive_price: float = 0.0
        self.arbitrage_margin: float = 0.0
        self.arbitrage_text: str = ""
        self.key_rules_text: str = ""
        self.warning_text: str = ""
        self.next_expensive_zone_text: str = ""
        self.precomputed_advisory_facts: str = ""


def build_tariff_prompt_context(
    tariff_type: str = TariffType.G13,
    provider: str = DEFAULT_ENERGY_PROVIDER,
    now: datetime | None = None,
) -> TariffPromptContext:
    """Build complete tariff context for AI prompts.

    Args:
        tariff_type: User's configured tariff (g11, g12, g12w, g12n, g13, dynamic).
        provider: Energy provider (tauron, pge).
        now: Current datetime (defaults to datetime.now()).

    Returns:
        TariffPromptContext with all fields filled.
    """
    if now is None:
        now = datetime.now()

    ctx = TariffPromptContext()
    ctx.tariff_type = tariff_type
    ctx.provider = provider
    ctx.season = _season_label(now.month)
    ctx.is_weekend = now.weekday() >= 5

    hour = now.hour
    month = now.month
    weekday = now.weekday()

    if tariff_type == TariffType.G13:
        ctx = _build_g13_context(ctx, hour, month, weekday)
    elif tariff_type == TariffType.G11:
        ctx = _build_g11_context(ctx, provider)
    elif tariff_type in (TariffType.G12, TariffType.G12W):
        ctx = _build_g12_context(ctx, tariff_type, provider, hour, month, weekday)
    elif tariff_type == TariffType.G12N:
        ctx = _build_g12n_context(ctx, hour, weekday)
    elif tariff_type == TariffType.DYNAMIC:
        ctx = _build_dynamic_context(ctx)
    else:
        # Fallback to G13
        ctx = _build_g13_context(ctx, hour, month, weekday)

    return ctx


def _build_g13_context(
    ctx: TariffPromptContext, hour: int, month: int, weekday: int,
) -> TariffPromptContext:
    """Build G13 three-zone tariff context."""
    zone, price = _get_current_g13_zone(hour, month, weekday)
    ctx.current_zone = zone
    ctx.current_price = price
    ctx.cheapest_price = G13_PRICES[G13Zone.OFF_PEAK]
    ctx.most_expensive_price = G13_PRICES[G13Zone.AFTERNOON_PEAK]
    ctx.arbitrage_margin = ctx.most_expensive_price - ctx.cheapest_price

    schedule = G13_SUMMER_SCHEDULE if _is_summer(month) else G13_WINTER_SCHEDULE
    is_summer = _is_summer(month)

    # Compute next zone transitions — find the next expensive zone start
    if weekday >= 5:
        ctx.next_expensive_zone_text = "Weekend — cały dzień off-peak, brak drogich stref"
    else:
        # Find next afternoon_peak start from the schedule
        afternoon_start = None
        morning_start = None
        for (start, end), z in schedule.items():
            if z == G13Zone.AFTERNOON_PEAK:
                afternoon_start = start
            elif z == G13Zone.MORNING_PEAK:
                morning_start = start

        if afternoon_start is not None and hour < afternoon_start:
            ctx.next_expensive_zone_text = (
                f"⚡ NASTĘPNA DROGA STREFA: szczyt popołudniowy zaczyna się o {afternoon_start:02d}:00 "
                f"(za {afternoon_start - hour}h) → cena {G13_PRICES[G13Zone.AFTERNOON_PEAK]:.2f} PLN/kWh"
            )
        elif morning_start is not None and hour < morning_start:
            ctx.next_expensive_zone_text = (
                f"⚡ NASTĘPNA DROGA STREFA: szczyt poranny zaczyna się o {morning_start:02d}:00 "
                f"(za {morning_start - hour}h) → cena {G13_PRICES[G13Zone.MORNING_PEAK]:.2f} PLN/kWh"
            )
        elif afternoon_start is not None and hour >= afternoon_start:
            # We're already in or past afternoon peak
            ctx.next_expensive_zone_text = (
                f"⚡ JESTEŚ W SZCZYCIE POPOŁUDNIOWYM ({afternoon_start:02d}:00-) → "
                f"cena {G13_PRICES[G13Zone.AFTERNOON_PEAK]:.2f} PLN/kWh → ROZŁADOWUJ BATERIĘ!"
            )
        else:
            ctx.next_expensive_zone_text = (
                f"Następny szczyt poranny jutro o {morning_start:02d}:00"
                if morning_start else "Brak informacji o strefach"
            )

    # Schedule text — with EXPLICIT hours for this season
    lines = []
    if weekday >= 5:
        lines.append("  Weekend — cały dzień off-peak (0.63 PLN/kWh)")
    else:
        for (start, end), z in schedule.items():
            p = G13_PRICES[z]
            marker = " ← JESTEŚ TUTAJ" if z == zone else ""
            zone_label = _ZONE_LABELS.get(z, z)
            lines.append(f"  {start:02d}:00-{end:02d}:00 → {zone_label} ({p:.2f} PLN/kWh){marker}")

    ctx.schedule_text = chr(10).join(lines)

    # Prices text
    ctx.prices_text = (
        f"  Off-Peak: {G13_PRICES[G13Zone.OFF_PEAK]:.2f} PLN/kWh (najtaniej)\n"
        f"  Szczyt poranny (morning_peak): {G13_PRICES[G13Zone.MORNING_PEAK]:.2f} PLN/kWh\n"
        f"  Szczyt popołudniowy (afternoon_peak): {G13_PRICES[G13Zone.AFTERNOON_PEAK]:.2f} PLN/kWh (najdrożej)"
    )

    ctx.prices_summary = (
        f"off_peak={G13_PRICES[G13Zone.OFF_PEAK]:.2f}, "
        f"morning_peak={G13_PRICES[G13Zone.MORNING_PEAK]:.2f}, "
        f"afternoon_peak={G13_PRICES[G13Zone.AFTERNOON_PEAK]:.2f} PLN/kWh"
    )

    # Arbitrage text
    ctx.arbitrage_text = (
        f"Ładuj w off-peak ({ctx.cheapest_price:.2f}) → rozładuj w afternoon_peak ({ctx.most_expensive_price:.2f}) "
        f"= {ctx.arbitrage_margin:.2f} PLN/kWh marży"
    )

    # Season-specific key rules — EXTREMELY explicit about times
    if is_summer:
        ctx.key_rules_text = (
            "██████████████████████████████████████████████████████████\n"
            "██  SEZON LETNI (kwiecień-wrzesień) — HARMONOGRAM G13  ██\n"
            "██████████████████████████████████████████████████████████\n"
            "  07:00-13:00 → SZCZYT PORANNY (0.91 PLN/kWh)\n"
            "  13:00-19:00 → OFF-PEAK TANI (0.63 PLN/kWh) ← okno na ładowanie!\n"
            "  19:00-22:00 → SZCZYT POPOŁUDNIOWY (1.50 PLN/kWh) ← NAJDROŻEJ!\n"
            "  22:00-07:00 → OFF-PEAK TANI (0.63 PLN/kWh)\n"
            "\n"
            "  ╔══════════════════════════════════════════════════════╗\n"
            "  ║  LATEM szczyt popołudniowy = 19:00-22:00            ║\n"
            "  ║  NIE 15:00! NIE 16:00! NIE 13:00!                  ║\n"
            "  ║  Dokładnie: DZIEWIETNASTA (19:00) do 22:00          ║\n"
            "  ╚══════════════════════════════════════════════════════╝\n"
            "\n"
            f"  {ctx.next_expensive_zone_text}"
        )
    else:
        ctx.key_rules_text = (
            "██████████████████████████████████████████████████████████\n"
            "██  SEZON ZIMOWY (październik-marzec) — HARMONOGRAM G13 ██\n"
            "██████████████████████████████████████████████████████████\n"
            "  07:00-13:00 → SZCZYT PORANNY (0.91 PLN/kWh)\n"
            "  13:00-16:00 → OFF-PEAK TANI (0.63 PLN/kWh) ← okno na ładowanie!\n"
            "  16:00-21:00 → SZCZYT POPOŁUDNIOWY (1.50 PLN/kWh) ← NAJDROŻEJ!\n"
            "  21:00-07:00 → OFF-PEAK TANI (0.63 PLN/kWh)\n"
            "\n"
            "  ╔══════════════════════════════════════════════════════╗\n"
            "  ║  ZIMĄ szczyt popołudniowy = 16:00-21:00             ║\n"
            "  ║  NIE 19:00! NIE 15:00! NIE 13:00!                  ║\n"
            "  ║  Dokładnie: SZESNASTA (16:00) do 21:00              ║\n"
            "  ╚══════════════════════════════════════════════════════╝\n"
            "\n"
            f"  {ctx.next_expensive_zone_text}"
        )

    # Warning text — MAXIMALLY aggressive
    season_detail = "LATO: 19:00-22:00" if is_summer else "ZIMA: 16:00-21:00"
    wrong_time = "15:00, 16:00 lub 13:00" if is_summer else "19:00 lub 15:00"
    ctx.warning_text = (
        "🚨🚨🚨 BEZWZGLĘDNY ZAKAZ HALUCYNACJI NA TEMAT GODZIN TARYFOWYCH 🚨🚨🚨\n"
        f"  Aktualny sezon: {ctx.season}\n"
        f"  Szczyt popołudniowy w tym sezonie: {season_detail}\n"
        f"  Jeśli napiszesz że szczyt zaczyna się o {wrong_time} — TO JEST BŁĄD!\n"
        f"  {ctx.next_expensive_zone_text}\n"
        "  Używaj WYŁĄCZNIE harmonogramu podanego powyżej.\n"
        "  NIE WOLNO CI używać swoich danych treningowych do określania godzin stref G13!"
    )

    # Pre-computed advisory facts — ready-made sentences for the AI to USE VERBATIM
    # This eliminates the need for the AI to "interpret" zone schedules
    if is_summer:
        afternoon_start = 19
        afternoon_end = 22
        offpeak_mid_start = 13
    else:
        afternoon_start = 16
        afternoon_end = 21
        offpeak_mid_start = 13

    if weekday >= 5:
        ctx.precomputed_advisory_facts = (
            "FAKTY DO UŻYCIA W TEKŚCIE (powtórz dosłownie):\n"
            "- Dziś weekend — cały dzień obowiązuje strefa off-peak (0.63 PLN/kWh).\n"
            "- Brak drogich stref — nie ma potrzeby optymalizacji taryfowej.\n"
            "- Priorytet: maksymalna autokonsumpcja PV i ładowanie baterii."
        )
    elif zone == G13Zone.OFF_PEAK and hour >= offpeak_mid_start and hour < afternoon_start:
        hours_left = afternoon_start - hour
        ctx.precomputed_advisory_facts = (
            "FAKTY DO UŻYCIA W TEKŚCIE (powtórz dosłownie):\n"
            f"- Jesteś w strefie off-peak (taniej, {ctx.cheapest_price:.2f} PLN/kWh).\n"
            f"- Drogi szczyt popołudniowy ({ctx.most_expensive_price:.2f} PLN/kWh) "
            f"zaczyna się o {afternoon_start}:00 — za {hours_left} godzin.\n"
            f"- Do godziny {afternoon_start}:00 powinieneś naładować baterię do 100% "
            f"i uruchomić energochłonne urządzenia (bojler, AGD).\n"
            f"- NIE pisz że szczyt zaczyna się o 15:00 ani 16:00 — LATEM zaczyna się o {afternoon_start}:00!"
        )
    elif zone == G13Zone.AFTERNOON_PEAK:
        ctx.precomputed_advisory_facts = (
            "FAKTY DO UŻYCIA W TEKŚCIE (powtórz dosłownie):\n"
            f"- JESTEŚ w najdroższej strefie — szczyt popołudniowy "
            f"({afternoon_start}:00-{afternoon_end}:00, {ctx.most_expensive_price:.2f} PLN/kWh).\n"
            f"- Rozładowuj baterię aby zasilić dom. Cel: ZEROWY import z sieci.\n"
            f"- Każda kWh z baterii oszczędza {ctx.most_expensive_price:.2f} PLN."
        )
    elif zone == G13Zone.MORNING_PEAK:
        ctx.precomputed_advisory_facts = (
            "FAKTY DO UŻYCIA W TEKŚCIE (powtórz dosłownie):\n"
            f"- Jesteś w szczycie porannym (07:00-13:00, {G13_PRICES[G13Zone.MORNING_PEAK]:.2f} PLN/kWh).\n"
            f"- Po 13:00 wejdziesz w off-peak ({ctx.cheapest_price:.2f} PLN/kWh) do {afternoon_start}:00.\n"
            f"- Następny drogi szczyt popołudniowy dopiero o {afternoon_start}:00 "
            f"({ctx.most_expensive_price:.2f} PLN/kWh)."
        )
    else:
        ctx.precomputed_advisory_facts = (
            "FAKTY DO UŻYCIA W TEKŚCIE (powtórz dosłownie):\n"
            f"- Jesteś w strefie off-peak ({ctx.cheapest_price:.2f} PLN/kWh).\n"
            f"- Kolejny szczyt poranny o 07:00 ({G13_PRICES[G13Zone.MORNING_PEAK]:.2f} PLN/kWh).\n"
            f"- Szczyt popołudniowy o {afternoon_start}:00 "
            f"({ctx.most_expensive_price:.2f} PLN/kWh)."
        )

    return ctx


def _build_g11_context(ctx: TariffPromptContext, provider: str) -> TariffPromptContext:
    """Build G11 flat-rate tariff context."""
    price = TAURON_G11_PRICE if provider == EnergyProvider.TAURON else PGE_G11_PRICE
    ctx.current_zone = "flat"
    ctx.current_price = price
    ctx.cheapest_price = price
    ctx.most_expensive_price = price
    ctx.arbitrage_margin = 0.0

    ctx.schedule_text = f"  Cały dzień → stała cena ({price:.2f} PLN/kWh) — brak stref czasowych"
    ctx.prices_text = f"  Stała cena: {price:.2f} PLN/kWh (całą dobę)"
    ctx.prices_summary = f"flat={price:.2f} PLN/kWh"
    ctx.arbitrage_text = (
        "Taryfa G11 nie ma arbitrażu cenowego (stała cena). "
        "Skup się na maksymalnej autokonsumpcji PV i minimalizacji importu z sieci."
    )
    ctx.key_rules_text = (
        f"TARYFA G11 — stała cena {price:.2f} PLN/kWh 24/7:\n"
        "  - Brak stref czasowych — cena jest taka sama dzień i noc\n"
        "  - Nocne ładowanie z sieci się NIE opłaca (ta sama cena!)\n"
        "  - Priorytet: maksymalna autokonsumpcja PV + magazynowanie nadwyżek w baterii\n"
        "  - Bateria powinna być ładowana WYŁĄCZNIE z PV, nie z sieci"
    )
    ctx.warning_text = "Taryfa G11: stała cena — arbitraż taryfowy nie jest możliwy."

    return ctx


def _build_g12_context(
    ctx: TariffPromptContext, tariff_type: str, provider: str,
    hour: int, month: int, weekday: int,
) -> TariffPromptContext:
    """Build G12/G12W two-zone tariff context."""
    zone, price = _get_current_g12_zone(hour, month, weekday, provider)
    ctx.current_zone = zone
    ctx.current_price = price

    if provider == EnergyProvider.PGE:
        prices = PGE_G12_PRICES if tariff_type == TariffType.G12 else PGE_G12W_PRICES
    else:
        prices = TAURON_G12_PRICES if tariff_type == TariffType.G12 else TAURON_G12W_PRICES

    ctx.cheapest_price = prices["off_peak"]
    ctx.most_expensive_price = prices["peak"]
    ctx.arbitrage_margin = prices["peak"] - prices["off_peak"]

    # Build schedule based on provider
    is_summer = _is_summer(month)
    if provider == EnergyProvider.PGE:
        sched = PGE_G12_SUMMER if is_summer else PGE_G12_WINTER
        lines = []
        if weekday >= 5 and tariff_type == TariffType.G12W:
            lines.append(f"  Weekend — cały dzień off-peak ({prices['off_peak']:.2f} PLN/kWh)")
        else:
            for start, end in sched["peak_ranges"]:
                lines.append(f"  {start:02d}:00-{end:02d}:00 → Szczyt ({prices['peak']:.2f} PLN/kWh)")
            for start, end in sched["off_peak_ranges"]:
                lines.append(f"  {start:02d}:00-{end:02d}:00 → Off-peak ({prices['off_peak']:.2f} PLN/kWh)")
        ctx.schedule_text = chr(10).join(lines)
    else:
        # Tauron G12
        schedule = _TAURON_G12_SCHEDULE["summer" if is_summer else "winter"]
        lines = []
        if weekday >= 5 and tariff_type == TariffType.G12W:
            lines.append(f"  Weekend — cały dzień off-peak ({prices['off_peak']:.2f} PLN/kWh)")
        else:
            for (start, end), z in schedule.items():
                p = prices[z]
                zone_label = _ZONE_LABELS.get(z, z)
                marker = " ← TERAZ" if z == zone else ""
                lines.append(f"  {start:02d}:00-{end:02d}:00 → {zone_label} ({p:.2f} PLN/kWh){marker}")
        ctx.schedule_text = chr(10).join(lines)

    ctx.prices_text = (
        f"  Off-Peak: {prices['off_peak']:.2f} PLN/kWh (najtaniej)\n"
        f"  Szczyt (peak): {prices['peak']:.2f} PLN/kWh (najdrożej)"
    )
    ctx.prices_summary = f"off_peak={prices['off_peak']:.2f}, peak={prices['peak']:.2f} PLN/kWh"

    ctx.arbitrage_text = (
        f"Ładuj w off-peak ({ctx.cheapest_price:.2f}) → rozładuj w szczycie ({ctx.most_expensive_price:.2f}) "
        f"= {ctx.arbitrage_margin:.2f} PLN/kWh marży"
    )

    tariff_label = "G12" if tariff_type == TariffType.G12 else "G12w"
    weekend_note = " (weekendy = off-peak)" if tariff_type == TariffType.G12W else ""
    ctx.key_rules_text = (
        f"TARYFA {tariff_label} — dwustrefowa{weekend_note}:\n"
        f"  - Off-peak ({prices['off_peak']:.2f} PLN/kWh): ładuj baterię, uruchom AGD\n"
        f"  - Szczyt ({prices['peak']:.2f} PLN/kWh): rozładowuj baterię, unikaj importu\n"
        f"  - Arbitraż: {ctx.arbitrage_margin:.2f} PLN/kWh marży na cykl"
    )

    ctx.warning_text = (
        f"⚠️ Taryfa {tariff_label} ({provider.upper()}) — sprawdź harmonogram powyżej.\n"
        f"  Aktualny sezon: {ctx.season}"
    )

    return ctx


def _build_g12n_context(
    ctx: TariffPromptContext, hour: int, weekday: int,
) -> TariffPromptContext:
    """Build PGE G12N Niedzielna tariff context."""
    zone, price = _get_current_g12n_zone(hour, weekday)
    ctx.current_zone = zone
    ctx.current_price = price
    ctx.cheapest_price = PGE_G12N_PRICES["off_peak"]
    ctx.most_expensive_price = PGE_G12N_PRICES["peak"]
    ctx.arbitrage_margin = PGE_G12N_PRICES["peak"] - PGE_G12N_PRICES["off_peak"]

    lines = []
    if weekday >= 5:
        lines.append(f"  Weekend (sobota/niedziela) — cały dzień off-peak ({PGE_G12N_PRICES['off_peak']:.2f} PLN/kWh)")
    else:
        lines.append(f"  01:00-05:00 → Off-peak ({PGE_G12N_PRICES['off_peak']:.2f} PLN/kWh)")
        lines.append(f"  05:00-01:00 → Szczyt ({PGE_G12N_PRICES['peak']:.2f} PLN/kWh)")
    ctx.schedule_text = chr(10).join(lines)

    ctx.prices_text = (
        f"  Off-Peak: {PGE_G12N_PRICES['off_peak']:.2f} PLN/kWh (noc 01-05 + niedziele)\n"
        f"  Szczyt (peak): {PGE_G12N_PRICES['peak']:.2f} PLN/kWh"
    )
    ctx.prices_summary = f"off_peak={PGE_G12N_PRICES['off_peak']:.2f}, peak={PGE_G12N_PRICES['peak']:.2f} PLN/kWh"

    ctx.arbitrage_text = (
        f"Ładuj w off-peak nocny ({ctx.cheapest_price:.2f}) → rozładuj w dzień ({ctx.most_expensive_price:.2f}) "
        f"= {ctx.arbitrage_margin:.2f} PLN/kWh marży"
    )
    ctx.key_rules_text = (
        "TARYFA G12n (PGE Niedzielna):\n"
        f"  - Off-peak ({PGE_G12N_PRICES['off_peak']:.2f} PLN/kWh): noce 01:00-05:00 + niedziele/święta\n"
        f"  - Szczyt ({PGE_G12N_PRICES['peak']:.2f} PLN/kWh): reszta czasu\n"
        "  - Okno nocnego ładowania jest KRÓTKIE (4h) — ładuj agresywnie!\n"
        "  - Niedziele = cały dzień off-peak — idealny na duże obciążenia"
    )
    ctx.warning_text = "Taryfa G12n (PGE): uwaga na krótkie okno nocne (01:00-05:00)!"

    return ctx


def _build_dynamic_context(ctx: TariffPromptContext) -> TariffPromptContext:
    """Build dynamic (ENTSO-E) tariff context — prices come from sensors."""
    ctx.current_zone = "dynamic"
    ctx.current_price = 0.0  # Will be filled from sensor data
    ctx.cheapest_price = 0.0
    ctx.most_expensive_price = 0.0
    ctx.arbitrage_margin = 0.0

    ctx.schedule_text = (
        "  Taryfa dynamiczna — ceny zmieniają się CO GODZINĘ na podstawie rynku ENTSO-E.\n"
        "  Brak stałego harmonogramu stref. Analizuj dane RCE i ENTSO-E z sekcji PRICING."
    )
    ctx.prices_text = "  Ceny dynamiczne — patrz sekcja RCE/ENTSO-E PRICES poniżej"
    ctx.prices_summary = "dynamic — hourly prices from ENTSO-E"

    ctx.arbitrage_text = (
        "Taryfa dynamiczna: kupuj gdy cena ENTSO-E < średnia dzienna, "
        "sprzedawaj gdy cena > średnia dzienna. "
        "Analizuj trend cenowy godzina po godzinie."
    )
    ctx.key_rules_text = (
        "TARYFA DYNAMICZNA (ENTSO-E):\n"
        "  - Ceny zmieniają się CO GODZINĘ\n"
        "  - Brak stałych stref — analizuj bieżące i przyszłe ceny\n"
        "  - Ładuj baterię gdy cena < 0.30 PLN/kWh\n"
        "  - Rozładowuj/sprzedawaj gdy cena > 0.90 PLN/kWh\n"
        "  - Arbitraż: kup tanio rano/nocą → sprzedaj drogo wieczorem"
    )
    ctx.warning_text = "Taryfa dynamiczna: ceny zmieniają się co godzinę — reaguj szybko!"

    return ctx


# ═══════════════════════════════════════════════════════════════════════════
# Prompt section formatters — drop-in replacements for AI prompts
# ═══════════════════════════════════════════════════════════════════════════

def render_tariff_schedule_section(ctx: TariffPromptContext) -> str:
    """Render the TARIFF SCHEDULE section for AI prompts.

    Returns a complete section ready to embed in any prompt.
    """
    tariff_label = ctx.tariff_type.upper()
    return f"""═══ TARYFA {tariff_label} — HARMONOGRAM ({ctx.season}) ═══
{ctx.schedule_text}
Aktualna strefa: {ctx.current_zone} ({ctx.current_price:.2f} PLN/kWh)

{ctx.warning_text}"""


def render_tariff_pricing_section(
    ctx: TariffPromptContext,
    rce_mwh: float = 0.0,
    rce_sell: float = 0.0,
    bat_cap_kwh: float = 10.0,
) -> str:
    """Render the PRICING section for AI prompts.

    Returns a complete section ready to embed in any prompt.
    """
    lines = [
        "═══ PRICING ═══",
        f"Tariff buy prices: {ctx.prices_summary}",
        f"RCE Price: {rce_mwh:.1f} PLN/MWh ({rce_mwh/1000:.4f} PLN/kWh)",
        f"RCE Sell Price: {rce_sell:.4f} PLN/kWh (prosumer coefficient applied)",
        f"IMPORTANT: At current RCE, selling to grid earns {rce_sell:.4f} PLN/kWh "
        f"vs buying at {ctx.current_price:.2f} PLN/kWh",
    ]

    if ctx.arbitrage_margin > 0:
        lines.append(
            f"Arbitrage: {ctx.arbitrage_text}"
        )
        lines.append(
            f"Battery arbitrage per cycle: {bat_cap_kwh:.1f} kWh × "
            f"{ctx.arbitrage_margin:.2f} PLN/kWh = ~{bat_cap_kwh * ctx.arbitrage_margin:.2f} PLN"
        )

    return chr(10).join(lines)


def render_tariff_key_rules(
    ctx: TariffPromptContext,
    rce_sell: float = 0.0,
    bat_cap_kwh: float = 10.0,
) -> str:
    """Render tariff-specific KEY RULES for AI prompts."""
    lines = ["═══ TARIFF RULES ═══", ctx.key_rules_text]

    if ctx.arbitrage_margin > 0:
        lines.extend([
            "",
            f"ARBITRAGE IS KING: charge at {ctx.cheapest_price:.2f} → "
            f"discharge at {ctx.most_expensive_price:.2f} = {ctx.arbitrage_margin:.2f} PLN/kWh margin",
            f"Battery: {bat_cap_kwh:.1f} kWh × {ctx.arbitrage_margin:.2f} PLN/kWh "
            f"= ~{bat_cap_kwh * ctx.arbitrage_margin:.2f} PLN per full cycle",
        ])

    if rce_sell > 0:
        lines.append(
            f"SELL PROFITABILITY: sprzedawaj TYLKO gdy RCE sell ({rce_sell:.4f}) > "
            f"cheapest buy ({ctx.cheapest_price:.2f} PLN/kWh)"
        )

    return chr(10).join(lines)


def render_next_zones(
    tariff_type: str = TariffType.G13,
    provider: str = DEFAULT_ENERGY_PROVIDER,
    now: datetime | None = None,
    hours_ahead: int = 3,
) -> str:
    """Render upcoming zone transitions for the next N hours."""
    if now is None:
        now = datetime.now()

    hour = now.hour
    month = now.month
    weekday = now.weekday()
    lines = []

    for offset in range(1, hours_ahead + 1):
        fh = (hour + offset) % 24
        # Next day might change weekday
        fw = weekday if (hour + offset) < 24 else (weekday + 1) % 7

        if tariff_type == TariffType.G13:
            zone, price = _get_current_g13_zone(fh, month, fw)
        elif tariff_type in (TariffType.G12, TariffType.G12W):
            zone, price = _get_current_g12_zone(fh, month, fw, provider)
        elif tariff_type == TariffType.G12N:
            zone, price = _get_current_g12n_zone(fh, fw)
        elif tariff_type == TariffType.G11:
            price = TAURON_G11_PRICE if provider == EnergyProvider.TAURON else PGE_G11_PRICE
            zone = "flat"
        else:
            zone = "dynamic"
            price = 0.0

        lines.append(f"+{offset}h ({fh:02d}:00): {zone} ({price:.2f} PLN/kWh)")

    return chr(10).join(lines)
