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
VERSION: Final = "1.1.0"

# =============================================================================
# Config Entry Keys
# =============================================================================
CONF_LICENSE_KEY: Final = "license_key"
CONF_LICENSE_MODE: Final = "license_mode"
CONF_DEVICE_ID: Final = "device_id"
CONF_TARIFF: Final = "tariff"
CONF_TARIFF_PRICES: Final = "tariff_prices"
CONF_RCE_ENABLED: Final = "rce_enabled"
CONF_GEMINI_API_KEY: Final = "gemini_api_key"
CONF_ANTHROPIC_API_KEY: Final = "anthropic_api_key"
CONF_AI_ENABLED: Final = "ai_enabled"
CONF_MODBUS_ENABLED: Final = "modbus_enabled"
CONF_MODBUS_PORT: Final = "modbus_port"
CONF_MODBUS_SLAVE: Final = "modbus_slave"
CONF_UPDATE_INTERVAL: Final = "update_interval"

# License mode values
LICENSE_MODE_FREE: Final = "free"
LICENSE_MODE_PRO: Final = "pro"

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
# Tariffs
# =============================================================================

class TariffType(StrEnum):
    """Supported tariff types."""

    G11 = "g11"
    G12 = "g12"
    G13 = "g13"


class G13Zone(StrEnum):
    """G13 tariff zones."""

    OFF_PEAK = "off_peak"
    MORNING_PEAK = "morning_peak"
    AFTERNOON_PEAK = "afternoon_peak"


# G13 Tauron 2026 prices (PLN/kWh brutto)
G13_PRICES: Final = {
    G13Zone.OFF_PEAK: 0.63,
    G13Zone.MORNING_PEAK: 0.91,
    G13Zone.AFTERNOON_PEAK: 1.50,
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
SOC_EMERGENCY: Final = 20
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

# RCE PSE sensors
SENSOR_RCE_PRICE: Final = "sensor.rce_pse_cena"
SENSOR_RCE_PRICE_KWH: Final = "sensor.rce_pse_cena_za_kwh"
SENSOR_RCE_SELL_PROSUMER: Final = "sensor.rce_pse_cena_sprzedazy_prosument"
SENSOR_RCE_NEXT_HOUR: Final = "sensor.rce_pse_cena_nastepnej_godziny"
SENSOR_RCE_2H: Final = "sensor.rce_pse_cena_za_2_godziny"
SENSOR_RCE_3H: Final = "sensor.rce_pse_cena_za_3_godziny"
SENSOR_RCE_AVG_TODAY: Final = "sensor.rce_pse_srednia_cena_dzisiaj"
SENSOR_RCE_MIN_TODAY: Final = "sensor.rce_pse_minimalna_cena_dzisiaj"
SENSOR_RCE_MAX_TODAY: Final = "sensor.rce_pse_maksymalna_cena_dzisiaj"

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

# Binary sensors from RCE PSE
BINARY_RCE_CHEAPEST: Final = "binary_sensor.rce_pse_aktywne_najtansze_okno_dzisiaj"
BINARY_RCE_EXPENSIVE: Final = "binary_sensor.rce_pse_aktywne_najdrozsze_okno_dzisiaj"

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
SERVICE_SET_EXPORT_LIMIT: Final = "set_export_limit"
SERVICE_ASK_AI: Final = "ask_ai_advisor"
SERVICE_GENERATE_REPORT: Final = "generate_report"

# =============================================================================
# AI Advisor
# =============================================================================
AI_GEMINI_MODEL: Final = "gemini-2.0-flash"
AI_CLAUDE_MODEL: Final = "claude-sonnet-4-20250514"
AI_MAX_TOKENS: Final = 2048
AI_TEMPERATURE: Final = 0.3
AI_RATE_LIMIT_CALLS: Final = 30  # per hour
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
