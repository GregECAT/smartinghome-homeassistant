"""Regression: grid import/export sign (GoodWe ET, home install, 2026-10-01).

Hourly means (kW) of sensor.active_power_total (+ export / − import), confirmed
by TAURON eLicznik: import 03–06 h (3.24 / 4.38 / 3.26 / 1.54 kWh), export
07–08 h (2.16 / 2.87 kWh). Before v1.61.0 grid_import_power showed the export
and grid_export_power the import.

Run: python3 -m pytest tests/  (no Home Assistant needed)
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "custom_components" / "smartinghome" / "grid_sign.py"
_spec = importlib.util.spec_from_file_location("grid_sign", _PATH)
grid_sign = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(grid_sign)

# hour → (meter kW, eLicznik import kWh, eLicznik export kWh)
HOME_2026_10_01 = {
    3: (-3.26, 3.24, 0.0),
    4: (-4.28, 4.38, 0.0),
    5: (-3.23, 3.26, 0.0),
    7: (2.23, 0.0, 2.16),
    8: (2.87, 0.0, 2.87),
}


@pytest.mark.parametrize("hour", sorted(HOME_2026_10_01))
def test_goodwe_meter_split_matches_elicznik(hour):
    meter_kw, tauron_imp, tauron_exp = HOME_2026_10_01[hour]
    imp_w, exp_w = grid_sign.split_grid_power(meter_kw * 1000)
    # Direction must match the utility meter; magnitude within 10 % (mean vs energy)
    assert (imp_w > 0) == (tauron_imp > 0)
    assert (exp_w > 0) == (tauron_exp > 0)
    assert imp_w / 1000 == pytest.approx(tauron_imp, rel=0.1, abs=0.01)
    assert exp_w / 1000 == pytest.approx(tauron_exp, rel=0.1, abs=0.01)


def test_mixed_hour_06_is_mostly_import():
    # 06 h: meter mean −1.53 kW; the integration's sensors averaged ≈2.2 kW import
    # and ≈0.7 kW export over the hour. Per sample, only one side is non-zero.
    samples_kw = [-2.4, -2.1, -2.3, 0.8, 0.6, -2.0]
    imp = sum(grid_sign.split_grid_power(s * 1000)[0] for s in samples_kw) / len(samples_kw)
    exp = sum(grid_sign.split_grid_power(s * 1000)[1] for s in samples_kw) / len(samples_kw)
    assert imp > exp
    assert (exp - imp) / 1000 == pytest.approx(sum(samples_kw) / len(samples_kw))


def test_split_never_negative_and_exclusive():
    for w in (-5000.0, -1.0, 0.0, 1.0, 5000.0):
        imp, exp = grid_sign.split_grid_power(w)
        assert imp >= 0 and exp >= 0
        assert imp == 0 or exp == 0
        assert exp - imp == w


def test_sofar_pcc_is_negated_other_brands_kept():
    # Sofar PCC: + current = import → canonical meter must be negative
    assert grid_sign.pcc_to_meter(3000.0, is_sofar=True) == -3000.0
    assert grid_sign.split_grid_power(grid_sign.pcc_to_meter(3000.0, True)) == (3000.0, 0.0)
    # Deye/Growatt/GoodWe phase sums are already + export / − import
    assert grid_sign.pcc_to_meter(3000.0, is_sofar=False) == 3000.0
