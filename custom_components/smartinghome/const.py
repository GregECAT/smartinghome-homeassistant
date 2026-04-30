"""Constants for Smarting HOME Energy Management integration."""
from __future__ import annotations

from enum import StrEnum
from typing import Final

# =============================================================================
# Integration
# =============================================================================
DOMAIN: Final = "smartinghome"
MANUFACTURER: Final = "Smarting HOME"
INTEGRATION_NAME: Final = "Smarting HOME — Energy Management"
VERSION: Final = "1.42.1"

# =============================================================================
# Config Entry Keys
# =============================================================================
CONF_LICENSE_KEY: Final = "license_key"
CONF_LICENSE_MODE: Final = "license_mode"
CONF_DEVICE_ID: Final = "device_id"
CONF_TARIFF: Final = "tariff"
CONF_TARIFF_PRICES: Final = "tariff_prices"
CONF_ENERGY_PROVIDER: Final = "energy_provider"
CONF_RCE_ENABLED: Final = "rce_enabled"
CONF_GEMINI_API_KEY: Final = "gemini_api_key"
CONF_ANTHROPIC_API_KEY: Final = "anthropic_api_key"
CONF_AI_ENABLED: Final = "ai_enabled"
CONF_MODBUS_ENABLED: Final = "modbus_enabled"
CONF_MODBUS_PORT: Final = "modbus_port"
CONF_MODBUS_SLAVE: Final = "modbus_slave"
CONF_UPDATE_INTERVAL: Final = "update_interval"
CONF_SENSOR_MAP: Final = "sensor_map"
CONF_INVERTER_BRAND: Final = "inverter_brand"
CONF_ECOWITT_ENABLED: Final = "ecowitt_enabled"

# License mode values
LICENSE_MODE_FREE: Final = "free"
LICENSE_MODE_PRO: Final = "pro"

# Inverter brands
INVERTER_BRAND_GOODWE: Final = "goodwe"
INVERTER_BRAND_DEYE: Final = "deye"
INVERTER_BRAND_GROWATT: Final = "growatt"
INVERTER_BRAND_SOFAR: Final = "sofar"
INVERTER_BRAND_OTHER: Final = "other"

# =============================================================================
# Sensor Mapping — universal entity configuration (35 sensors)
# =============================================================================

# ── Core sensors (required for power flow) ──
SENSOR_MAP_CORE: Final = {
    "pv_power": "Total PV power (W)",
    "load_power": "House load / consumption (W)",
    "grid_power": "Grid power (W, raw: + export / − import, inverted for AI: + import / − export)",
    "battery_power": "Battery power (W, + charge / − discharge)",
    "battery_soc": "Battery state of charge (%)",
}

# ── PV Strings ──
SENSOR_MAP_PV: Final = {
    "pv1_power": "PV String 1 — power (W)",
    "pv1_voltage": "PV String 1 — voltage (V)",
    "pv1_current": "PV String 1 — current (A)",
    "pv2_power": "PV String 2 — power (W)",
    "pv2_voltage": "PV String 2 — voltage (V)",
    "pv2_current": "PV String 2 — current (A)",
    "pv3_power": "PV String 3 — power (W)",
    "pv4_power": "PV String 4 — power (W)",
}

# ── Battery extended ──
SENSOR_MAP_BATTERY: Final = {
    "battery_voltage": "Battery voltage (V)",
    "battery_current": "Battery current (A)",
    "battery_temp": "Battery temperature (°C)",
    "battery_capacity_kwh": "Battery capacity (kWh)",
    "battery_soh": "Battery state of health (%)",
    "battery_charge_limit": "Battery max charge current (A)",
    "battery_discharge_limit": "Battery max discharge current (A)",
}

# ── Grid extended ──
SENSOR_MAP_GRID: Final = {
    "voltage_l1": "Grid voltage L1 (V)",
    "voltage_l2": "Grid voltage L2 (V)",
    "voltage_l3": "Grid voltage L3 (V)",
    "current_l1": "Grid current L1 (A)",
    "current_l2": "Grid current L2 (A)",
    "current_l3": "Grid current L3 (A)",
    "power_l1": "Grid power L1 (W)",
    "power_l2": "Grid power L2 (W)",
    "power_l3": "Grid power L3 (W)",
    "grid_frequency": "Grid frequency (Hz)",
}

# ── Daily totals ──
SENSOR_MAP_DAILY: Final = {
    "pv_today": "PV generation today (kWh)",
    "grid_import_today": "Grid import today (kWh)",
    "grid_export_today": "Grid export today (kWh)",
    "battery_charge_today": "Battery charge today (kWh)",
    "battery_discharge_today": "Battery discharge today (kWh)",
}

# ── Inverter ──
SENSOR_MAP_INVERTER: Final = {
    "inverter_power": "Inverter output power (W)",
    "inverter_temp": "Inverter temperature — air (°C)",
    "inverter_temp_radiator": "Inverter temperature — radiator (°C)",
}

# ── Diagnostics & Backup ──
SENSOR_MAP_DIAGNOSTICS: Final = {
    "diag_status_code": "Diagnostic status bitmask",
    "meter_power_factor": "Grid meter power factor (cos φ)",
    "backup_load": "Backup / UPS total load (W)",
    "ups_load_pct": "UPS load percentage (%)",
    "ems_mode": "EMS operating mode",
}

# ── Weather — Cloud API (AccuWeather, OpenWeatherMap, Forecast.Solar) ──
SENSOR_MAP_WEATHER_CLOUD: Final = {
    "weather_temp": "Outside temperature — cloud API (°C)",
    "weather_humidity": "Outside humidity — cloud API (%)",
    "weather_cloud_cover": "Cloud cover — cloud API (%)",
    "weather_wind_speed": "Wind speed — cloud API (km/h)",
    "weather_pressure": "Atmospheric pressure — cloud API (hPa)",
    "weather_uv_index": "UV index — cloud API",
}

# ── Weather — Local Station (EcoWitt, WS2910, etc.) ──
SENSOR_MAP_WEATHER_LOCAL: Final = {
    "local_temp": "Outside temperature — local station (°C)",
    "local_humidity": "Outside humidity — local station (%)",
    "local_wind_speed": "Wind speed — local station (km/h)",
    "local_rain_rate": "Rain rate — local station (mm/h)",
    "local_solar_radiation": "Solar radiation — local station (W/m²)",
    "local_dewpoint": "Dewpoint temperature — local station (°C)",
    "local_uv_index": "UV index — local station",
    "local_solar_lux": "Solar illuminance — local station (lx)",
    "local_pressure": "Atmospheric pressure — local station (hPa)",
    "local_daily_rain": "Daily rain — local station (mm)",
    "local_wind_direction": "Wind direction — local station (°)",
    "local_wind_gust": "Wind gust — local station (km/h)",
    "local_feels_like": "Feels like temperature — local station (°C)",
}

# Combined map (all keys)
SENSOR_MAP_KEYS: Final = {
    **SENSOR_MAP_CORE,
    **SENSOR_MAP_PV,
    **SENSOR_MAP_BATTERY,
    **SENSOR_MAP_GRID,
    **SENSOR_MAP_DAILY,
    **SENSOR_MAP_INVERTER,
    **SENSOR_MAP_DIAGNOSTICS,
    **SENSOR_MAP_WEATHER_CLOUD,
    **SENSOR_MAP_WEATHER_LOCAL,
}

# ── GoodWe defaults ──
# Entity IDs match the official GoodWe HA integration (goodwe_ prefix)
DEFAULT_SENSOR_MAP: Final = {
    # Core
    "pv_power": "sensor.goodwe_total_power",
    "load_power": "sensor.goodwe_house_consumption",
    "grid_power": "sensor.goodwe_meter_active_power_total",
    "battery_power": "sensor.goodwe_battery_power",
    "battery_soc": "sensor.goodwe_battery_state_of_charge",
    # PV Strings
    "pv1_power": "sensor.goodwe_pv1_power",
    "pv1_voltage": "sensor.goodwe_pv1_voltage",
    "pv1_current": "sensor.goodwe_pv1_current",
    "pv2_power": "sensor.goodwe_pv2_power",
    "pv2_voltage": "sensor.goodwe_pv2_voltage",
    "pv2_current": "sensor.goodwe_pv2_current",
    "pv3_power": "",
    "pv4_power": "",
    # Battery extended
    "battery_voltage": "sensor.goodwe_battery_voltage",
    "battery_current": "sensor.goodwe_battery_current",
    "battery_temp": "sensor.goodwe_battery_temperature",
    "battery_capacity_kwh": "",
    "battery_soh": "sensor.goodwe_battery_state_of_health",
    "battery_charge_limit": "sensor.goodwe_battery_charge_limit",
    "battery_discharge_limit": "sensor.goodwe_battery_discharge_limit",
    # Grid extended
    "voltage_l1": "sensor.goodwe_on_grid_l1_voltage",
    "voltage_l2": "sensor.goodwe_on_grid_l2_voltage",
    "voltage_l3": "sensor.goodwe_on_grid_l3_voltage",
    "current_l1": "sensor.goodwe_on_grid_l1_current",
    "current_l2": "sensor.goodwe_on_grid_l2_current",
    "current_l3": "sensor.goodwe_on_grid_l3_current",
    "power_l1": "sensor.goodwe_active_power_l1",
    "power_l2": "sensor.goodwe_active_power_l2",
    "power_l3": "sensor.goodwe_active_power_l3",
    "grid_frequency": "sensor.goodwe_meter_frequency",
    # Daily totals
    "pv_today": "sensor.goodwe_today_s_pv_generation",
    "grid_import_today": "sensor.goodwe_today_energy_import",
    "grid_export_today": "sensor.goodwe_today_energy_export",
    "battery_charge_today": "sensor.goodwe_today_battery_charge",
    "battery_discharge_today": "sensor.goodwe_today_battery_discharge",
    # Inverter
    "inverter_power": "sensor.goodwe_active_power",
    "inverter_temp": "sensor.goodwe_inverter_temperature_air",
    "inverter_temp_radiator": "sensor.goodwe_inverter_temperature_radiator",
    # Diagnostics & Backup
    "diag_status_code": "sensor.goodwe_diag_status_code",
    "meter_power_factor": "sensor.goodwe_meter_power_factor",
    "backup_load": "sensor.goodwe_back_up_load",
    "ups_load_pct": "sensor.goodwe_ups_load",
    "ems_mode": "sensor.goodwe_ems_mode",
    # Weather — cloud (user fills in)
    "weather_temp": "",
    "weather_humidity": "",
    "weather_cloud_cover": "",
    "weather_wind_speed": "",
    "weather_pressure": "",
    "weather_uv_index": "",
    # Weather — local station (user fills in)
    "local_temp": "",
    "local_humidity": "",
    "local_wind_speed": "",
    "local_rain_rate": "",
    "local_solar_radiation": "",
    "local_dewpoint": "",
    "local_uv_index": "",
    "local_solar_lux": "",
    "local_pressure": "",
    "local_daily_rain": "",
    "local_wind_direction": "",
    "local_wind_gust": "",
    "local_feels_like": "",
}

# ── Deye defaults ──
DEFAULT_SENSOR_MAP_DEYE: Final = {
    # Core
    "pv_power": "sensor.deye_pv_power",
    "load_power": "sensor.deye_load_power",
    "grid_power": "sensor.deye_total_grid_power",
    "battery_power": "sensor.deye_battery_power",
    "battery_soc": "sensor.deye_battery_soc",
    # PV Strings
    "pv1_power": "sensor.deye_pv1_power",
    "pv1_voltage": "sensor.deye_pv1_voltage",
    "pv1_current": "sensor.deye_pv1_current",
    "pv2_power": "sensor.deye_pv2_power",
    "pv2_voltage": "sensor.deye_pv2_voltage",
    "pv2_current": "sensor.deye_pv2_current",
    "pv3_power": "sensor.deye_pv3_power",
    "pv4_power": "sensor.deye_pv4_power",
    # Battery extended
    "battery_voltage": "sensor.deye_battery_voltage",
    "battery_current": "sensor.deye_battery_current",
    "battery_temp": "sensor.deye_battery_temperature",
    "battery_capacity_kwh": "",
    # Grid extended
    "voltage_l1": "sensor.deye_grid_l1_voltage",
    "voltage_l2": "sensor.deye_grid_l2_voltage",
    "voltage_l3": "sensor.deye_grid_l3_voltage",
    "current_l1": "sensor.deye_grid_l1_current",
    "current_l2": "sensor.deye_grid_l2_current",
    "current_l3": "sensor.deye_grid_l3_current",
    "power_l1": "sensor.deye_grid_l1_power",
    "power_l2": "sensor.deye_grid_l2_power",
    "power_l3": "sensor.deye_grid_l3_power",
    "grid_frequency": "sensor.deye_grid_frequency",
    # Daily totals
    "pv_today": "sensor.deye_day_pv_energy",
    "grid_import_today": "sensor.deye_day_grid_import",
    "grid_export_today": "sensor.deye_day_grid_export",
    "battery_charge_today": "sensor.deye_day_battery_charge",
    "battery_discharge_today": "sensor.deye_day_battery_discharge",
    # Inverter
    "inverter_power": "sensor.deye_active_power",
    "inverter_temp": "sensor.deye_dc_temperature",
    # Weather — cloud (user fills in)
    "weather_temp": "",
    "weather_humidity": "",
    "weather_cloud_cover": "",
    "weather_wind_speed": "",
    "weather_pressure": "",
    "weather_uv_index": "",
    # Weather — local station (user fills in)
    "local_temp": "",
    "local_humidity": "",
    "local_wind_speed": "",
    "local_rain_rate": "",
    "local_solar_radiation": "",
    "local_dewpoint": "",
    "local_uv_index": "",
    "local_solar_lux": "",
    "local_pressure": "",
    "local_daily_rain": "",
    "local_wind_direction": "",
    "local_wind_gust": "",
    "local_feels_like": "",
}

# ── Growatt defaults ──
DEFAULT_SENSOR_MAP_GROWATT: Final = {
    # Core
    "pv_power": "sensor.growatt_pv_power",
    "load_power": "sensor.growatt_load_power",
    "grid_power": "sensor.growatt_grid_power",
    "battery_power": "sensor.growatt_battery_power",
    "battery_soc": "sensor.growatt_battery_soc",
    # PV Strings
    "pv1_power": "sensor.growatt_pv1_power",
    "pv1_voltage": "sensor.growatt_pv1_voltage",
    "pv1_current": "sensor.growatt_pv1_current",
    "pv2_power": "sensor.growatt_pv2_power",
    "pv2_voltage": "sensor.growatt_pv2_voltage",
    "pv2_current": "sensor.growatt_pv2_current",
    "pv3_power": "",
    "pv4_power": "",
    # Battery extended
    "battery_voltage": "sensor.growatt_battery_voltage",
    "battery_current": "sensor.growatt_battery_current",
    "battery_temp": "sensor.growatt_battery_temperature",
    "battery_capacity_kwh": "",
    # Grid extended
    "voltage_l1": "sensor.growatt_grid_l1_voltage",
    "voltage_l2": "sensor.growatt_grid_l2_voltage",
    "voltage_l3": "sensor.growatt_grid_l3_voltage",
    "current_l1": "sensor.growatt_grid_l1_current",
    "current_l2": "sensor.growatt_grid_l2_current",
    "current_l3": "sensor.growatt_grid_l3_current",
    "power_l1": "sensor.growatt_grid_l1_power",
    "power_l2": "sensor.growatt_grid_l2_power",
    "power_l3": "sensor.growatt_grid_l3_power",
    "grid_frequency": "sensor.growatt_grid_frequency",
    # Daily totals
    "pv_today": "sensor.growatt_day_pv_energy",
    "grid_import_today": "sensor.growatt_day_grid_import",
    "grid_export_today": "sensor.growatt_day_grid_export",
    "battery_charge_today": "sensor.growatt_day_battery_charge",
    "battery_discharge_today": "sensor.growatt_day_battery_discharge",
    # Inverter
    "inverter_power": "sensor.growatt_active_power",
    "inverter_temp": "sensor.growatt_inverter_temperature",
    # Weather — cloud (user fills in)
    "weather_temp": "",
    "weather_humidity": "",
    "weather_cloud_cover": "",
    "weather_wind_speed": "",
    "weather_pressure": "",
    "weather_uv_index": "",
    # Weather — local station (user fills in)
    "local_temp": "",
    "local_humidity": "",
    "local_wind_speed": "",
    "local_rain_rate": "",
    "local_solar_radiation": "",
    "local_dewpoint": "",
    "local_uv_index": "",
    "local_solar_lux": "",
    "local_pressure": "",
    "local_daily_rain": "",
    "local_wind_direction": "",
    "local_wind_gust": "",
    "local_feels_like": "",
}

# ── Sofar defaults ──
DEFAULT_SENSOR_MAP_SOFAR: Final = {
    # Core
    "pv_power": "sensor.sofar_pv_power",
    "load_power": "sensor.sofar_load_power",
    "grid_power": "sensor.sofar_grid_power",
    "battery_power": "sensor.sofar_battery_power",
    "battery_soc": "sensor.sofar_battery_soc",
    # PV Strings
    "pv1_power": "sensor.sofar_pv1_power",
    "pv1_voltage": "sensor.sofar_pv1_voltage",
    "pv1_current": "sensor.sofar_pv1_current",
    "pv2_power": "sensor.sofar_pv2_power",
    "pv2_voltage": "sensor.sofar_pv2_voltage",
    "pv2_current": "sensor.sofar_pv2_current",
    "pv3_power": "",
    "pv4_power": "",
    # Battery extended
    "battery_voltage": "sensor.sofar_battery_voltage",
    "battery_current": "sensor.sofar_battery_current",
    "battery_temp": "sensor.sofar_battery_temperature",
    "battery_capacity_kwh": "",
    # Grid extended
    "voltage_l1": "sensor.sofar_grid_voltage_r",
    "voltage_l2": "sensor.sofar_grid_voltage_s",
    "voltage_l3": "sensor.sofar_grid_voltage_t",
    "current_l1": "sensor.sofar_grid_current_r",
    "current_l2": "sensor.sofar_grid_current_s",
    "current_l3": "sensor.sofar_grid_current_t",
    "power_l1": "",
    "power_l2": "",
    "power_l3": "",
    "grid_frequency": "sensor.sofar_grid_frequency",
    # Daily totals
    "pv_today": "sensor.sofar_today_generation",
    "grid_import_today": "sensor.sofar_today_import",
    "grid_export_today": "sensor.sofar_today_export",
    "battery_charge_today": "sensor.sofar_today_battery_charge",
    "battery_discharge_today": "sensor.sofar_today_battery_discharge",
    # Inverter
    "inverter_power": "sensor.sofar_inverter_power",
    "inverter_temp": "sensor.sofar_inverter_temperature",
    # Weather — cloud (user fills in)
    "weather_temp": "",
    "weather_humidity": "",
    "weather_cloud_cover": "",
    "weather_wind_speed": "",
    "weather_pressure": "",
    "weather_uv_index": "",
    # Weather — local station (user fills in)
    "local_temp": "",
    "local_humidity": "",
    "local_wind_speed": "",
    "local_rain_rate": "",
    "local_solar_radiation": "",
    "local_dewpoint": "",
    "local_uv_index": "",
    "local_solar_lux": "",
    "local_pressure": "",
    "local_daily_rain": "",
    "local_wind_direction": "",
    "local_wind_gust": "",
    "local_feels_like": "",
}

# ── Ecowitt WH90 defaults ──
DEFAULT_ECOWITT_SENSOR_MAP: Final = {
    "local_temp": "sensor.ecowitt_outdoor_temp_9747",
    "local_humidity": "sensor.ecowitt_outdoor_humidity_9747",
    "local_wind_speed": "sensor.ecowitt_wind_speed_9747",
    "local_rain_rate": "sensor.ecowitt_rain_rate_9747",
    "local_solar_radiation": "sensor.ecowitt_solar_radiation_9747",
    "local_dewpoint": "sensor.ecowitt_dewpoint_9747",
    "local_uv_index": "sensor.ecowitt_uv_index_9747",
    "local_solar_lux": "sensor.ecowitt_solar_lux_9747",
    "local_pressure": "sensor.ecowitt_pressure_relative",
    "local_daily_rain": "sensor.ecowitt_daily_rain_9747",
    "local_wind_direction": "sensor.ecowitt_wind_direction_9747",
    "local_wind_gust": "sensor.ecowitt_wind_gust_9747",
    "local_feels_like": "sensor.ecowitt_feels_like_temp_ch3",
}

# =============================================================================
# Defaults
# =============================================================================
DEFAULT_UPDATE_INTERVAL: Final = 30  # seconds
DEFAULT_MODBUS_PORT: Final = "/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_BG02YUXJ-if00-port0"
DEFAULT_MODBUS_SLAVE: Final = 247
DEFAULT_MODBUS_BAUDRATE: Final = 9600
DEFAULT_EXPORT_LIMIT: Final = 16000  # Watts
DEFAULT_DOD_ON_GRID: Final = 95  # %
DEFAULT_BATTERY_CAPACITY: Final = 10200  # Wh (Lynx Home U 10.2 kWh)
DEFAULT_BATTERY_MIN_SOC: Final = 5  # %
DEFAULT_BATTERY_CHARGE_CURRENT_MAX: Final = "18.5"
DEFAULT_BATTERY_CHARGE_CURRENT_BLOCK: Final = "0"

# =============================================================================
# License
# =============================================================================
LICENSE_API_URL: Final = "https://mslvyiimjevhvujojfax.supabase.co/functions/v1/sh-license-api"
LICENSE_VALIDATE_ENDPOINT: Final = "/validate"
LICENSE_STATUS_ENDPOINT: Final = "/status"
LICENSE_REGISTER_FREE_ENDPOINT: Final = "/register-free"
LICENSE_CHECK_INTERVAL: Final = 86400  # 24 hours in seconds
LICENSE_GRACE_PERIOD: Final = 259200  # 72 hours in seconds

ATTR_LICENSE_VALID: Final = "license_valid"
ATTR_LICENSE_TIER: Final = "license_tier"
ATTR_LICENSE_EXPIRES: Final = "license_expires"
ATTR_LICENSE_LAST_CHECK: Final = "license_last_check"


class LicenseTier(StrEnum):
    """License tier levels."""

    FREE = "free"
    DEMO = "demo"
    PRO = "pro"
    ENTERPRISE = "enterprise"


# ── Feature Gating ──────────────────────────────────────────────────────────
# FREE tier limits
FREE_SENSORS_ENABLED: Final = True        # All 33 sensors available
FREE_BINARY_SENSORS_ENABLED: Final = True  # G13 peak, off-peak, license
FREE_G13_ENABLED: Final = True             # G13 zone + buy price
FREE_RCE_READ_ENABLED: Final = True        # RCE prices read-only
FREE_HEMS_AUTO_MODE: Final = True          # Basic auto mode switch
FREE_NIGHT_ARBITRAGE: Final = False        # ❌ PRO only
FREE_VOLTAGE_CASCADE: Final = False        # ❌ PRO only
FREE_PV_SURPLUS_CASCADE: Final = False     # ❌ PRO only
FREE_AI_ENABLED: Final = False             # ❌ PRO only
FREE_EXPORT_LIMIT_CONTROL: Final = False   # ❌ PRO only
FREE_BLUEPRINTS_COUNT: Final = 2           # Morning sell + midday charge only
FREE_DASHBOARDS_COUNT: Final = 1           # HEMS control only

# DEMO tier limits (fallback from expired PRO)
DEMO_MAX_SENSORS: Final = 10
DEMO_AI_ENABLED: Final = False
DEMO_BLUEPRINTS_ENABLED: Final = False

# =============================================================================
# Platforms
# =============================================================================
PLATFORMS: Final = [
    "sensor",
    "binary_sensor",
    "switch",
    "select",
    "number",
]

# =============================================================================
# Energy Providers
# =============================================================================

class EnergyProvider(StrEnum):
    """Supported energy providers."""

    TAURON = "tauron"
    PGE = "pge"


ENERGY_PROVIDER_LABELS: Final = {
    EnergyProvider.TAURON: "Tauron",
    EnergyProvider.PGE: "PGE (Polska Grupa Energetyczna)",
}

DEFAULT_ENERGY_PROVIDER: Final = EnergyProvider.TAURON

# =============================================================================
# Tariffs
# =============================================================================

class TariffType(StrEnum):
    """Supported tariff types."""

    G11 = "g11"
    G12 = "g12"
    G12W = "g12w"
    G12N = "g12n"   # PGE "niedzielna" — only PGE
    G13 = "g13"     # Tauron "trzystrefowa" — only Tauron
    DYNAMIC = "dynamic"


# Tariffs available per provider
PROVIDER_TARIFFS: Final = {
    EnergyProvider.TAURON: [TariffType.G11, TariffType.G12, TariffType.G12W, TariffType.G13, TariffType.DYNAMIC],
    EnergyProvider.PGE: [TariffType.G11, TariffType.G12, TariffType.G12W, TariffType.G12N, TariffType.DYNAMIC],
}

# Provider-specific tariff labels
PROVIDER_TARIFF_LABELS: Final = {
    EnergyProvider.TAURON: {
        TariffType.G11: "G11 — Flat rate (stała cena)",
        TariffType.G12: "G12 — Two-zone (13-15 + 22-06 taniej)",
        TariffType.G12W: "G12w — Two-zone + weekends",
        TariffType.G13: "G13 — Three-zone (recommended)",
        TariffType.DYNAMIC: "Dynamiczna — cena godzinowa ENTSO-E",
    },
    EnergyProvider.PGE: {
        TariffType.G11: "G11 — Flat rate (stała cena)",
        TariffType.G12: "G12 — Two-zone (letnia/zimowa + noc)",
        TariffType.G12W: "G12w — Two-zone + weekends",
        TariffType.G12N: "G12n — Niedzielna (noc 1-5 + niedziele)",
        TariffType.DYNAMIC: "Dynamiczna — cena godzinowa ENTSO-E",
    },
}


class G13Zone(StrEnum):
    """G13 tariff zones."""

    OFF_PEAK = "off_peak"
    MORNING_PEAK = "morning_peak"
    AFTERNOON_PEAK = "afternoon_peak"


# ── Tauron 2026 prices (PLN/kWh brutto) ──
# G13 Tauron 2026 prices
G13_PRICES: Final = {
    G13Zone.OFF_PEAK: 0.63,
    G13Zone.MORNING_PEAK: 0.91,
    G13Zone.AFTERNOON_PEAK: 1.50,
}

TAURON_G11_PRICE: Final = 0.87   # flat rate per kWh (avg for 4000 kWh/yr)
TAURON_G12_PRICES: Final = {"off_peak": 0.55, "peak": 1.10}
TAURON_G12W_PRICES: Final = {"off_peak": 0.55, "peak": 1.10}

# ── PGE 2026 prices (PLN/kWh brutto, energia + przesył) ──
PGE_G11_PRICE: Final = 1.10      # energia czynna 0.62 + przesył 0.48
PGE_G12_PRICES: Final = {"off_peak": 0.61, "peak": 1.25}
PGE_G12W_PRICES: Final = {"off_peak": 0.69, "peak": 1.30}
PGE_G12N_PRICES: Final = {"off_peak": 0.59, "peak": 1.21}

# ── Unified provider → tariff → prices dictionary ──
PROVIDER_TARIFF_PRICES: Final = {
    EnergyProvider.TAURON: {
        TariffType.G11: {"flat": TAURON_G11_PRICE},
        TariffType.G12: TAURON_G12_PRICES,
        TariffType.G12W: TAURON_G12W_PRICES,
        TariffType.G13: {
            "off_peak": G13_PRICES[G13Zone.OFF_PEAK],
            "morning": G13_PRICES[G13Zone.MORNING_PEAK],
            "peak": G13_PRICES[G13Zone.AFTERNOON_PEAK],
        },
    },
    EnergyProvider.PGE: {
        TariffType.G11: {"flat": PGE_G11_PRICE},
        TariffType.G12: PGE_G12_PRICES,
        TariffType.G12W: PGE_G12W_PRICES,
        TariffType.G12N: PGE_G12N_PRICES,
    },
}

# G13 Schedule — WINTER (October–March)
G13_WINTER_SCHEDULE: Final = {
    # (start_hour, end_hour): zone
    (7, 13): G13Zone.MORNING_PEAK,
    (13, 16): G13Zone.OFF_PEAK,
    (16, 21): G13Zone.AFTERNOON_PEAK,
    (21, 7): G13Zone.OFF_PEAK,  # wraps midnight
}

# G13 Schedule — SUMMER (April–September)
G13_SUMMER_SCHEDULE: Final = {
    (7, 13): G13Zone.MORNING_PEAK,
    (13, 19): G13Zone.OFF_PEAK,
    (19, 22): G13Zone.AFTERNOON_PEAK,
    (22, 7): G13Zone.OFF_PEAK,  # wraps midnight
}

# PGE G12 zone schedules (differ from Tauron)
# SUMMER (April–September)
PGE_G12_SUMMER: Final = {
    # off-peak: 15:00–17:00 + 22:00–06:00
    # peak:     06:00–15:00 + 17:00–22:00
    "off_peak_ranges": [(15, 17), (22, 6)],
    "peak_ranges": [(6, 15), (17, 22)],
}
# WINTER (October–March)
PGE_G12_WINTER: Final = {
    # off-peak: 13:00–15:00 + 22:00–06:00
    # peak:     06:00–13:00 + 15:00–22:00
    "off_peak_ranges": [(13, 15), (22, 6)],
    "peak_ranges": [(6, 13), (15, 22)],
}

# PGE G12n zone schedules
# Mon-Sat: peak 05:00–01:00, off-peak 01:00–05:00
# Sunday + holidays: off-peak all day
PGE_G12N_SCHEDULE: Final = {
    "weekday_peak": (5, 1),    # 05:00 – 01:00 next day (20h)
    "weekday_offpeak": (1, 5), # 01:00 – 05:00 (4h)
    "weekend_offpeak": True,   # all day off-peak
}

# Months: winter = 10,11,12,1,2,3; summer = 4,5,6,7,8,9
WINTER_MONTHS: Final = {10, 11, 12, 1, 2, 3}
SUMMER_MONTHS: Final = {4, 5, 6, 7, 8, 9}

# =============================================================================
# RCE (Rynek Cen Energii)
# =============================================================================
RCE_PROSUMER_COEFFICIENT: Final = 1.23  # 2026 prosumer selling coefficient
RCE_PRICE_THRESHOLDS: Final = {
    "very_cheap": 100,   # PLN/MWh — charge battery
    "cheap": 150,        # PLN/MWh — charge battery
    "normal": 250,       # PLN/MWh
    "expensive": 300,    # PLN/MWh — sell
    "very_expensive": 500,  # PLN/MWh — max sell
}

# Dynamic tariff (ENTSO-E) price thresholds — PLN/kWh all-in
DYNAMIC_PRICE_THRESHOLDS: Final = {
    "very_cheap": 0.10,     # PLN/kWh — ładuj agresywnie
    "cheap": 0.30,          # PLN/kWh — ładuj baterię
    "normal": 0.60,         # PLN/kWh — autokonsumpcja
    "expensive": 0.90,      # PLN/kWh — sprzedawaj
    "very_expensive": 1.20, # PLN/kWh — max eksport
}


class RCEPriceLevel(StrEnum):
    """RCE price evaluation levels."""

    EXCELLENT = "excellent"
    GOOD = "good"
    NORMAL = "normal"
    POOR = "poor"
    TERRIBLE = "terrible"


class RCETrend(StrEnum):
    """RCE price trend."""

    RISING = "rising"
    FALLING = "falling"
    STABLE = "stable"


# =============================================================================
# HEMS (Home Energy Management System)
# =============================================================================

class HEMSMode(StrEnum):
    """HEMS operating modes."""

    AUTO = "auto"
    SELL = "sell"
    CHARGE = "charge"
    PEAK_SAVE = "peak_save"
    NIGHT_ARBITRAGE = "night_arbitrage"
    EMERGENCY = "emergency"
    MANUAL = "manual"


class HEMSStrategy(StrEnum):
    """HEMS optimization strategies."""

    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"


class AutopilotStrategy(StrEnum):
    """Autopilot energy management strategies."""

    MAX_SELF_CONSUMPTION = "max_self_consumption"
    MAX_PROFIT = "max_profit"
    BATTERY_PROTECTION = "battery_protection"
    ZERO_EXPORT = "zero_export"
    WEATHER_ADAPTIVE = "weather_adaptive"
    AI_FULL_AUTONOMY = "ai_full_autonomy"


AUTOPILOT_STRATEGY_LABELS: Final = {
    AutopilotStrategy.MAX_SELF_CONSUMPTION: "🟢 Max Autokonsumpcja",
    AutopilotStrategy.MAX_PROFIT: "💰 Max Zysk (Arbitraż)",
    AutopilotStrategy.BATTERY_PROTECTION: "🔋 Ochrona Baterii",
    AutopilotStrategy.ZERO_EXPORT: "⚡ Zero Export",
    AutopilotStrategy.WEATHER_ADAPTIVE: "🌧️ Pogodowy Adaptacyjny",
    AutopilotStrategy.AI_FULL_AUTONOMY: "🧠 AI Pełna Autonomia",
}

AUTOPILOT_STRATEGY_DESCRIPTIONS: Final = {
    AutopilotStrategy.MAX_SELF_CONSUMPTION: "Priorytet: zużycie własne PV, minimalne import/export. Bateria buforuje nadwyżki.",
    AutopilotStrategy.MAX_PROFIT: "Kupuj tanio (off-peak/RCE niskie), sprzedawaj drogo (peak/RCE wysokie). Max arbitraż.",
    AutopilotStrategy.BATTERY_PROTECTION: "Zachowawcze DOD, pełna bateria przed szczytem, ochrona żywotności.",
    AutopilotStrategy.ZERO_EXPORT: "Zerowy eksport do sieci. Cała energia w domu + bateria.",
    AutopilotStrategy.WEATHER_ADAPTIVE: "AI analizuje prognozę pogody i dynamicznie zmienia strategię godzina po godzinie.",
    AutopilotStrategy.AI_FULL_AUTONOMY: "AI sam decyduje o strategii na każdą godzinę dnia. Pełna autonomia.",
}

# Autopilot default interval (minutes)
AUTOPILOT_DEFAULT_INTERVAL: Final = 60
AUTOPILOT_SETTINGS_KEY: Final = "autopilot_active_strategy"
AUTOPILOT_PLAN_KEY: Final = "ai_autopilot_plan"
AUTOPILOT_ACTIONS_STATE_KEY: Final = "autopilot_actions_state"
AUTOPILOT_ACTIONS_OVERRIDES_KEY: Final = "autopilot_action_sensor_overrides"

# =============================================================================
# Schedule Manager — Manual Mode & Autopilot Scheduler
# =============================================================================


class ManualMode(StrEnum):
    """Manual operating modes for user-defined hourly schedule."""

    SELF_CONSUMPTION = "self_consumption"   # PV→home→battery, surplus→grid
    SELL_TO_GRID = "sell_to_grid"           # Battery discharges to grid
    CHARGE_BATTERY = "charge_battery"       # PV→battery priority
    CHARGE_FROM_GRID = "charge_from_grid"   # Battery charges from grid (arbitrage)
    PEAK_SAVE = "peak_save"                 # Block grid charge, battery powers home
    ZERO_EXPORT = "zero_export"             # All PV to home/battery, nothing to grid
    BATTERY_HOLD = "battery_hold"           # Battery idle — no charge, no discharge
    OFF = "off"                             # No management, inverter defaults


MANUAL_MODE_LABELS: Final = {
    ManualMode.SELF_CONSUMPTION: "🏠 Autokonsumpcja",
    ManualMode.SELL_TO_GRID: "💰 Sprzedaż do sieci",
    ManualMode.CHARGE_BATTERY: "🔋 Ładuj baterię (PV)",
    ManualMode.CHARGE_FROM_GRID: "⚡🔋 Ładuj z sieci",
    ManualMode.PEAK_SAVE: "🛡️ Oszczędzaj na szczyt",
    ManualMode.ZERO_EXPORT: "🚫 Zero eksport",
    ManualMode.BATTERY_HOLD: "⏸️ Bateria hold",
    ManualMode.OFF: "⭕ System OFF",
}

MANUAL_MODE_DESCRIPTIONS: Final = {
    ManualMode.SELF_CONSUMPTION: "Produkcja PV zasila dom, nadwyżka do baterii, reszta do sieci.",
    ManualMode.SELL_TO_GRID: "Bateria rozładowuje do sieci — maksymalny eksport, optymalnie w szczycie.",
    ManualMode.CHARGE_BATTERY: "Priorytet PV → dom → bateria. Nadwyżka PV ładuje baterię. NIE pobiera z sieci do ładowania. Bezpieczne w dzień.",
    ManualMode.CHARGE_FROM_GRID: "Agresywne ładowanie z PV + sieci (eco_charge). ⚠️ Pobiera z sieci! Używaj TYLKO: arbitraż nocny, ujemna cena RCE.",
    ManualMode.PEAK_SAVE: "Zablokuj ładowanie z sieci, bateria zasila dom przed szczytem.",
    ManualMode.ZERO_EXPORT: "Cała energia w domu + bateria. Zerowy eksport do sieci.",
    ManualMode.BATTERY_HOLD: "Bateria wstrzymana — nie ładuj, nie rozładowuj. Stan czuwania.",
    ManualMode.OFF: "Brak zarządzania energią. Falownik działa w trybie domyślnym.",
}

MANUAL_MODE_ICONS: Final = {
    ManualMode.SELF_CONSUMPTION: "mdi:home-lightning-bolt",
    ManualMode.SELL_TO_GRID: "mdi:cash-plus",
    ManualMode.CHARGE_BATTERY: "mdi:battery-charging",
    ManualMode.CHARGE_FROM_GRID: "mdi:battery-charging-wireless",
    ManualMode.PEAK_SAVE: "mdi:shield-sun",
    ManualMode.ZERO_EXPORT: "mdi:cancel",
    ManualMode.BATTERY_HOLD: "mdi:pause-circle",
    ManualMode.OFF: "mdi:power-off",
}

# Schedule persistence keys (settings.json)
SCHEDULE_ENABLED_KEY: Final = "schedule_enabled"
SCHEDULE_MODE_KEY: Final = "schedule_mode"       # "weekday_weekend" or "single"
SCHEDULE_WEEKDAY_KEY: Final = "schedule_weekday"  # 24-slot weekday schedule
SCHEDULE_WEEKEND_KEY: Final = "schedule_weekend"  # 24-slot weekend schedule
SCHEDULE_ACTIVE_SLOT_KEY: Final = "schedule_active_slot"
SCHEDULE_LAST_TRANSITION_KEY: Final = "schedule_last_transition"
SCHEDULE_TEMPLATES_KEY: Final = "schedule_templates"

# Schedule services
SERVICE_SAVE_SCHEDULE: Final = "save_schedule"
SERVICE_GET_SCHEDULE_STATUS: Final = "get_schedule_status"
SERVICE_APPLY_MANUAL_MODE: Final = "apply_manual_mode"

# Default schedule: all autopilot with max_self_consumption
DEFAULT_SCHEDULE_SLOT: Final = {
    "mode": "autopilot",
    "strategy": AutopilotStrategy.MAX_SELF_CONSUMPTION.value,
}

# Default weekday schedule — optimized for G13 tariff
DEFAULT_SCHEDULE_WEEKDAY: Final = {
    str(h).zfill(2): (
        {"mode": "autopilot", "strategy": "max_profit"}
        if 7 <= h < 13 or 16 <= h < 21
        else {"mode": "autopilot", "strategy": "max_self_consumption"}
    )
    for h in range(24)
}

# Default weekend schedule — all self-consumption (off-peak all day)
DEFAULT_SCHEDULE_WEEKEND: Final = {
    str(h).zfill(2): {"mode": "autopilot", "strategy": "max_self_consumption"}
    for h in range(24)
}

# Peak Sell — active energy export during expensive afternoon peak
# User sets what % of battery SOC to sell to grid during AFTERNOON_PEAK
DEFAULT_PEAK_SELL_SOC_PERCENT: Final = 50   # % of battery SOC allocated for grid sell
PEAK_SELL_SOC_MIN: Final = 0                # 0% = no sell (only self-consumption)
PEAK_SELL_SOC_MAX: Final = 80               # 80% = aggressive sell (keep 20% for home)
PEAK_SELL_SOC_FLOOR: Final = 20             # absolute minimum SOC after selling
PEAK_SELL_SETTINGS_KEY: Final = "peak_sell_soc_percent"


class EMSMode(StrEnum):
    """GoodWe EMS modes."""

    AUTO = "auto"
    SELF_USE = "self_use"
    FORCE_CHARGE = "force_charge"
    FORCE_DISCHARGE = "force_discharge"
    HOLD = "hold"


# Voltage protection thresholds (V)
VOLTAGE_THRESHOLD_WARNING: Final = 252
VOLTAGE_THRESHOLD_HIGH: Final = 253
VOLTAGE_THRESHOLD_CRITICAL: Final = 254
VOLTAGE_THRESHOLD_RECOVERY: Final = 248
VOLTAGE_RECOVERY_DELAY: Final = 300  # seconds (5 min)

# PV Surplus thresholds (W)
PV_SURPLUS_TIER1: Final = 2000   # Bojler ON
PV_SURPLUS_TIER2: Final = 3000   # + Klima ON
PV_SURPLUS_TIER3: Final = 4000   # + Gniazdko 2 ON
PV_SURPLUS_OFF: Final = 500      # All OFF
PV_SURPLUS_MIN_SOC_TIER1: Final = 80
PV_SURPLUS_MIN_SOC_TIER2: Final = 85
PV_SURPLUS_MIN_SOC_TIER3: Final = 90

# SOC Safety thresholds (%)
SOC_EMERGENCY: Final = 5  # Próg krytyczny — identyczny z DEFAULT_BATTERY_MIN_SOC
SOC_GRID_IMPORT_THRESHOLD: Final = 5  # % — poniżej tego progu dozwolone pobieranie z sieci
GRID_IMPORT_DETECT_THRESHOLD_W: Final = 100  # W — próg wykrycia importu z sieci
SOC_CHECK_11_THRESHOLD: Final = 50
SOC_CHECK_12_THRESHOLD: Final = 70
SOC_NIGHT_CHARGE_TARGET: Final = 90
SOC_NIGHT_CHARGE_MIN_FORECAST: Final = 8.0  # kWh

# Night arbitrage
NIGHT_ARBITRAGE_START_HOUR: Final = 23
NIGHT_ARBITRAGE_STOP_HOUR: Final = 6
NIGHT_ARBITRAGE_MIN_FORECAST: Final = 8.0  # kWh

# =============================================================================
# Sensor Entity IDs & Definitions
# =============================================================================

# Source sensor entity IDs (from GoodWe integration)
SENSOR_PV_POWER: Final = "sensor.pv_power"
SENSOR_PV1_POWER: Final = "sensor.pv1_power"
SENSOR_PV2_POWER: Final = "sensor.pv2_power"
SENSOR_PV1_VOLTAGE: Final = "sensor.pv1_voltage"
SENSOR_PV2_VOLTAGE: Final = "sensor.pv2_voltage"
SENSOR_PV1_CURRENT: Final = "sensor.pv1_current"
SENSOR_PV2_CURRENT: Final = "sensor.pv2_current"
SENSOR_PV_GENERATION_TODAY: Final = "sensor.today_s_pv_generation"
SENSOR_PV_GENERATION_TOTAL: Final = "sensor.total_pv_generation"

SENSOR_GRID_POWER_TOTAL: Final = "sensor.meter_active_power_total"
SENSOR_GRID_POWER_L1: Final = "sensor.meter_active_power_l1"
SENSOR_GRID_POWER_L2: Final = "sensor.meter_active_power_l2"
SENSOR_GRID_POWER_L3: Final = "sensor.meter_active_power_l3"
SENSOR_GRID_VOLTAGE_L1: Final = "sensor.on_grid_l1_voltage"
SENSOR_GRID_VOLTAGE_L2: Final = "sensor.on_grid_l2_voltage"
SENSOR_GRID_VOLTAGE_L3: Final = "sensor.on_grid_l3_voltage"
SENSOR_GRID_FREQUENCY_L1: Final = "sensor.on_grid_l1_frequency"
SENSOR_GRID_FREQUENCY_L2: Final = "sensor.on_grid_l2_frequency"
SENSOR_GRID_FREQUENCY_L3: Final = "sensor.on_grid_l3_frequency"

SENSOR_BATTERY_SOC: Final = "sensor.battery_state_of_charge"
SENSOR_BATTERY_POWER: Final = "sensor.battery_power"
SENSOR_BATTERY_VOLTAGE: Final = "sensor.battery_voltage"
SENSOR_BATTERY_CURRENT: Final = "sensor.battery_current"
SENSOR_BATTERY_TEMPERATURE: Final = "sensor.battery_temperature"
SENSOR_BATTERY_MODE: Final = "sensor.battery_mode"
SENSOR_BATTERY_CHARGE_TODAY: Final = "sensor.today_battery_charge"
SENSOR_BATTERY_DISCHARGE_TODAY: Final = "sensor.today_battery_discharge"

SENSOR_LOAD_TOTAL: Final = "sensor.load"
SENSOR_LOAD_L1: Final = "sensor.load_l1"
SENSOR_LOAD_L2: Final = "sensor.load_l2"
SENSOR_LOAD_L3: Final = "sensor.load_l3"
SENSOR_LOAD_TODAY: Final = "sensor.today_load"

SENSOR_WORK_MODE: Final = "sensor.work_mode"
SENSOR_INVERTER_TEMP: Final = "sensor.inverter_temperature_air"

# GoodWe extended sensors (Phase 1 — diagnostics & health)
SENSOR_BATTERY_SOH: Final = "sensor.battery_state_of_health"
SENSOR_BATTERY_CHARGE_LIMIT: Final = "sensor.battery_charge_limit"
SENSOR_BATTERY_DISCHARGE_LIMIT: Final = "sensor.battery_discharge_limit"
SENSOR_INVERTER_TEMP_RADIATOR: Final = "sensor.inverter_temperature_radiator"
SENSOR_DIAG_STATUS_CODE: Final = "sensor.diag_status_code"
SENSOR_METER_POWER_FACTOR: Final = "sensor.meter_power_factor"
SENSOR_BACKUP_LOAD: Final = "sensor.back_up_load"
SENSOR_UPS_LOAD: Final = "sensor.ups_load"
SENSOR_EMS_MODE: Final = "sensor.ems_mode"

# RCE PSE sensors — migrated to v2.0.0 entity names
# See docs/MIGRACJA-V2.md for full changelog
SENSOR_RCE_PRICE: Final = "sensor.rce_pse_cena"
SENSOR_RCE_SELL_PROSUMER: Final = "sensor.rce_pse_cena_sprzedazy_prosument"
SENSOR_RCE_NEXT_PERIOD: Final = "sensor.rce_pse_cena_nastepny_okres"  # v2: was cena_nastepnej_godziny
SENSOR_RCE_PREV_PERIOD: Final = "sensor.rce_pse_cena_poprzedni_okres"  # v2 new
SENSOR_RCE_AVG_TODAY: Final = "sensor.rce_pse_srednia_cena_dzisiaj"
SENSOR_RCE_MIN_TODAY: Final = "sensor.rce_pse_minimalna_cena_dzisiaj"
SENSOR_RCE_MAX_TODAY: Final = "sensor.rce_pse_maksymalna_cena_dzisiaj"
SENSOR_RCE_MEDIAN_TODAY: Final = "sensor.rce_pse_mediana_cen_dzisiaj"  # v2 new
SENSOR_RCE_PRICE_TOMORROW: Final = "sensor.rce_pse_cena_jutro"  # v2 — full 96-point grid for tomorrow
SENSOR_RCE_AVG_TOMORROW: Final = "sensor.rce_pse_srednia_cena_jutro"  # v2 new
SENSOR_RCE_TOMORROW_VS_TODAY: Final = "sensor.rce_pse_jutro_vs_dzisiaj_srednia"  # v2 new (%)

# v2 Energy Compass (PDGSZ) — PSE grid demand recommendation
SENSOR_RCE_COMPASS_TODAY: Final = "sensor.rce_pse_kompas_energetyczny_dzisiaj"
SENSOR_RCE_COMPASS_TOMORROW: Final = "sensor.rce_pse_kompas_energetyczny_jutro"

# v2 Configurable window average prices
SENSOR_RCE_CHEAP_WINDOW_AVG: Final = "sensor.rce_pse_tanie_okno_srednia_cena_dzisiaj"
SENSOR_RCE_EXPENSIVE_WINDOW_AVG: Final = "sensor.rce_pse_drogie_okno_srednia_cena_dzisiaj"

# v2 Window timestamps
SENSOR_RCE_CHEAP_WINDOW_START: Final = "sensor.rce_pse_tanie_okno_dzisiaj_poczatek"
SENSOR_RCE_CHEAP_WINDOW_END: Final = "sensor.rce_pse_tanie_okno_dzisiaj_koniec"
SENSOR_RCE_EXPENSIVE_WINDOW_START: Final = "sensor.rce_pse_drogie_okno_dzisiaj_poczatek"
SENSOR_RCE_EXPENSIVE_WINDOW_END: Final = "sensor.rce_pse_drogie_okno_dzisiaj_koniec"

# v2 Threshold binary triggers
BINARY_RCE_BELOW_THRESHOLD: Final = "binary_sensor.rce_pse_cena_ponizej_progu_aktywna"
BINARY_RCE_ABOVE_THRESHOLD: Final = "binary_sensor.rce_pse_cena_powyzej_progu_aktywna"

# v1 compatibility aliases (REMOVED in v2 — computed from prices attribute)
# SENSOR_RCE_2H, SENSOR_RCE_3H, SENSOR_RCE_PRICE_KWH — no longer exist
# SENSOR_RCE_NEXT_HOUR — renamed to SENSOR_RCE_NEXT_PERIOD

# ENTSO-E Dynamic Pricing sensors
SENSOR_ENTSOE_PRICE_NOW: Final = "sensor.entso_e_aktualna_cena_energii"
SENSOR_ENTSOE_ALLIN_NOW: Final = "sensor.entso_e_koszt_all_in_teraz"
SENSOR_ENTSOE_ALLIN_NEXT: Final = "sensor.entso_e_koszt_all_in_nastepna_h"
SENSOR_ENTSOE_ALLIN_MIN: Final = "sensor.entso_e_koszt_all_in_min_dzisiaj"
SENSOR_ENTSOE_ALLIN_MAX: Final = "sensor.entso_e_koszt_all_in_max_dzisiaj"
SENSOR_ENTSOE_AVG_TODAY: Final = "sensor.entso_e_srednia_dzisiaj"
SENSOR_ENTSOE_RANK: Final = "sensor.entso_e_ranking_biezacej_godziny"
SENSOR_ENTSOE_PERCENTILE: Final = "sensor.entso_e_percentyl_biezacej_godziny"

# Forecast.Solar sensors
SENSOR_FORECAST_POWER_1: Final = "sensor.power_production_now"
SENSOR_FORECAST_POWER_2: Final = "sensor.power_production_now_2"
SENSOR_FORECAST_TODAY_1: Final = "sensor.energy_production_today"
SENSOR_FORECAST_TODAY_2: Final = "sensor.energy_production_today_2"
SENSOR_FORECAST_REMAINING_1: Final = "sensor.energy_production_today_remaining"
SENSOR_FORECAST_REMAINING_2: Final = "sensor.energy_production_today_remaining_2"
SENSOR_FORECAST_TOMORROW_1: Final = "sensor.energy_production_tomorrow"
SENSOR_FORECAST_TOMORROW_2: Final = "sensor.energy_production_tomorrow_2"

# GoodWe control entities
SELECT_WORK_MODE: Final = "select.goodwe_tryb_pracy_falownika"
NUMBER_EXPORT_LIMIT: Final = "number.goodwe_grid_export_limit"
NUMBER_DOD_ON_GRID: Final = "number.goodwe_depth_of_discharge_on_grid"
NUMBER_ECO_MODE_POWER: Final = "number.goodwe_eco_mode_power"
NUMBER_ECO_MODE_SOC: Final = "number.goodwe_eco_mode_soc"

# Sofar Solar control entities
SELECT_SOFAR_WORK_MODE: Final = "select.sofar_energy_storage_mode"
NUMBER_SOFAR_DOD: Final = "number.sofar_depth_of_discharge"
NUMBER_SOFAR_EXPORT_LIMIT: Final = "number.sofar_grid_export_limit"
NUMBER_SOFAR_CHARGE_POWER: Final = "number.sofar_charge_power_limit"
NUMBER_SOFAR_DISCHARGE_POWER: Final = "number.sofar_discharge_power_limit"

# Binary sensors from RCE PSE — v2 names
BINARY_RCE_CHEAPEST: Final = "binary_sensor.rce_pse_tanie_okno_aktywne"
BINARY_RCE_EXPENSIVE: Final = "binary_sensor.rce_pse_drogie_okno_aktywne"

# Controllable devices
SWITCH_BOILER: Final = "switch.bojler_3800"
SWITCH_AC: Final = "switch.klimatyzacja_socket_1"
SWITCH_SOCKET2: Final = "switch.drugie_gniazdko"
SWITCH_FLOOD_PUMP: Final = "switch.pompa_zalania_socket_1"

# GoodWe device ID (default)
DEFAULT_GOODWE_DEVICE_ID: Final = "02592f41265ac022d0c8b8aa99728b3e"

# =============================================================================
# Services
# =============================================================================
SERVICE_SET_MODE: Final = "set_mode"
SERVICE_FORCE_CHARGE: Final = "force_charge"
SERVICE_FORCE_DISCHARGE: Final = "force_discharge"
SERVICE_FORCE_CUSTOM: Final = "force_custom"
SERVICE_STOP_FORCE_CHARGE: Final = "stop_force_charge"
SERVICE_STOP_FORCE_DISCHARGE: Final = "stop_force_discharge"
SERVICE_EMERGENCY_STOP: Final = "emergency_stop"
SERVICE_SET_EXPORT_LIMIT: Final = "set_export_limit"
SERVICE_ASK_AI: Final = "ask_ai_advisor"
SERVICE_GENERATE_REPORT: Final = "generate_report"
SERVICE_RUN_AUTOPILOT: Final = "run_autopilot"

# =============================================================================
# AI Advisor
# =============================================================================
AI_GEMINI_MODEL: Final = "gemini-2.5-flash"  # default — GA since June 2025
AI_GEMINI_MODELS: Final = {
    "gemini-2.5-flash": "Gemini 2.5 Flash (szybki, domyślny)",
    "gemini-2.5-pro": "Gemini 2.5 Pro (zaawansowany)",
    "gemini-3-flash-preview": "Gemini 3 Flash (Preview)",
}
AI_CLAUDE_MODEL: Final = "claude-sonnet-4-6"  # default — Sonnet 4.6
AI_CLAUDE_MODELS: Final = {
    "claude-sonnet-4-6": "Claude Sonnet 4.6 (szybki)",
    "claude-opus-4-6": "Claude Opus 4.6 (najpotężniejszy)",
    "claude-3-5-haiku": "Claude Haiku 3.5 (najtańszy)",
}
AI_MAX_TOKENS: Final = 16384
AI_TEMPERATURE: Final = 0.3
AI_RATE_LIMIT_CALLS: Final = 60  # advisory calls per hour (HEMS, reports, user queries)
AI_RATE_LIMIT_CONTROLLER: Final = 30  # controller/strategist calls per hour
AI_RATE_LIMIT_WINDOW: Final = 3600  # seconds

# =============================================================================
# Icons
# =============================================================================
ICON_PV: Final = "mdi:solar-power-variant"
ICON_BATTERY: Final = "mdi:battery"
ICON_GRID: Final = "mdi:transmission-tower"
ICON_LOAD: Final = "mdi:home-lightning-bolt"
ICON_MONEY: Final = "mdi:currency-usd"
ICON_TARIFF: Final = "mdi:clock-time-eight-outline"
ICON_AI: Final = "mdi:robot"
ICON_HEMS: Final = "mdi:brain"
ICON_LICENSE: Final = "mdi:license"
ICON_EXPORT: Final = "mdi:upload"
ICON_IMPORT: Final = "mdi:download"
ICON_VOLTAGE: Final = "mdi:flash-alert"
ICON_TEMPERATURE: Final = "mdi:thermometer"
ICON_FREQUENCY: Final = "mdi:sine-wave"
ICON_AUTARKY: Final = "mdi:shield-sun"
ICON_FORECAST: Final = "mdi:weather-sunny"
ICON_RCE: Final = "mdi:chart-line"
ICON_ARBITRAGE: Final = "mdi:swap-horizontal"
