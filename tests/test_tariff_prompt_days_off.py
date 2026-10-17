"""AI tariff context: weekends and public holidays are one-price days (no peak)."""
from __future__ import annotations

import importlib
import sys
import types
from datetime import datetime
from pathlib import Path

_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "smartinghome"
_pkg = types.ModuleType("smartinghome_pure")
_pkg.__path__ = [str(_DIR)]
sys.modules.setdefault("smartinghome_pure", _pkg)
tp = importlib.import_module("smartinghome_pure.tariff_prompt")


def test_saturday_is_one_price_day():
    ctx = tp.build_tariff_prompt_context("g13", "tauron", datetime(2026, 10, 17, 11, 0))  # noqa: DTZ001
    assert ctx.is_weekend
    assert "dzień wolny" in ctx.precomputed_advisory_facts
    assert "bez szczytów" in ctx.precomputed_advisory_facts
    assert "0.63 PLN" not in ctx.precomputed_advisory_facts or f"{ctx.cheapest_price:.2f}" == "0.63"


def test_independence_day_on_a_wednesday_has_no_peak():
    ctx = tp.build_tariff_prompt_context("g13", "tauron", datetime(2026, 11, 11, 17, 0))  # noqa: DTZ001
    assert ctx.is_weekend
    assert ctx.current_price == ctx.cheapest_price


def test_working_day_afternoon_is_peak():
    ctx = tp.build_tariff_prompt_context("g13", "tauron", datetime(2026, 10, 19, 17, 0))  # noqa: DTZ001
    assert not ctx.is_weekend
    assert ctx.current_price > ctx.cheapest_price
