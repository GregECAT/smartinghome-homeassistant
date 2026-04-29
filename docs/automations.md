- id: '1750763368204'
  alias: Harmonogram pracy boilera
  description: Automatyczne włączanie/wyłączanie oraz powiadomienia push na iPhone
  triggers:
  - at: '06:00:00'
    trigger: time
  - at: '07:00:00'
    trigger: time
  - at: '14:00:00'
    trigger: time
  - at: '15:00:00'
    trigger: time
  - at: '17:00:00'
    trigger: time
  - at: '18:00:00'
    trigger: time
    actions:
  - choose: - conditions: - condition: template
    value_template: "{{ now().strftime('%H:%M') in ['07:00', '12:00', '17:00'] }}"
    sequence: - action: switch.turn_on
    data: {}
    target:
    entity_id: switch.bojler_3800 - action: notify.mobile_app_iphone_henryk
    data:
    title: "🔥 Boiler WŁĄCZONY"
    message: "Boiler uruchomiony o {{ now().strftime('%H:%M') }}" - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "🔥 Boiler WŁĄCZONY"
    message: "Boiler uruchomiony o {{ now().strftime('%H:%M') }}" - conditions: - condition: template
    value_template: "{{ now().strftime('%H:%M') in ['06:00', '14:00', '17:00'] }}"
    sequence: - action: switch.turn_off
    data: {}
    target:
    entity_id: switch.bojler_3800 - action: notify.mobile_app_iphone_henryk
    data:
    title: "💧 Boiler WYŁĄCZONY"
    message: "Boiler wyłączony o {{ now().strftime('%H:%M') }}" - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "💧 Boiler WYŁĄCZONY"
    message: "Boiler wyłączony o {{ now().strftime('%H:%M') }}"
    mode: single

- id: '1763053799726'
  alias: Sterowanie pompą piwnica przez czujnik zalania
  description: Włącza pompę przy zalaniu, wyłącza 1 minutę po ustaniu zalania
  triggers:
  - id: woda_wykryta
    entity_id: binary_sensor.shui_jin_chuan_gan_qi_wilgoc
    to: 'on'
    trigger: state
  - id: sucho
    entity_id: binary_sensor.shui_jin_chuan_gan_qi_wilgoc
    to: 'off'
    trigger: state
    actions:
  - choose: - conditions: "{{ trigger.id == 'woda_wykryta' }}"
    sequence: - target:
    entity_id: switch.pompa_zalania_socket_1
    action: switch.turn_on - conditions: "{{ trigger.id == 'sucho' }}"
    sequence: - delay: '00:01:00' - target:
    entity_id: switch.pompa_zalania_socket_1
    action: switch.turn_off
    mode: single

# ============================================================================

# HEMS G13 + RCE v5.3 — ECO MODE CHARGE/DISCHARGE via mletenay

# ============================================================================

#

# KONWENCJA ZNAKOW GoodWe BT + mletenay:

# battery_power: UJEMNY = ladowanie, DODATNI = rozladowanie

# meter_active_power_total: DODATNI = import, UJEMNY = eksport

#

# FORCE CHARGE/DISCHARGE v2 (26.03.2026):

# Zamiast Modbus 47511 repeat → eco_mode_power + eco_charge/discharge

# number.goodwe_eco_mode_power = 100% → pelna moc (~2-3 kW)

# number.goodwe_eco_mode_soc = docelowy SOC

# gw_modbus_grid_export_enabled (47509) musi byc = 1 dla discharge

# ============================================================================

# ---- W4: GRID IMPORT GUARD ----

- id: hems_grid_import_guard
  alias: "HEMS: Grid Import Guard — STOP ladowania w drogich godzinach"
  description: "Wykrywa import z sieci (meter > +100W) podczas ladowania baterii (battery_power < -100W na BT) w drogich godzinach G13. Wyjatek RCE < 100 PLN/MWh."
  triggers:
  - trigger: numeric_state
    entity_id: sensor.meter_active_power_total
    above: 100
    for:
    seconds: 30
    conditions:
  - condition: template
    value_template: "{{ is_state('input_boolean.hems_force_grid_charge', 'off') and is_state('input_boolean.hems_force_battery_discharge', 'off') }}"
  - condition: time
    weekday: [mon, tue, wed, thu, fri]
  - condition: template
    value_template: >
    {% set h = now().hour %}
    {% set m = now().month %}
    {% set przedpoludniowy = (h >= 7 and h < 13) %}
    {% set popoludniowy_zima = (m in [1,2,3,10,11,12]) and (h >= 16 and h < 21) %}
    {% set popoludniowy_lato = (m in [4,5,6,7,8,9]) and (h >= 19 and h < 22) %}
    {{ przedpoludniowy or popoludniowy_zima or popoludniowy_lato }}
  - condition: numeric_state
    entity_id: sensor.battery_power
    below: -100
  - condition: numeric_state
    entity_id: sensor.rce_pse_cena
    above: 100
    actions:
  - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "0"
  - action: notify.mobile_app_iphone_henryk
    data:
    title: "🛡️ Grid Guard: STOP ladowania!"
    message: "Import: {{ states('sensor.meter_active_power_total') }}W | PV: {{ states('sensor.pv_power') }}W | SOC: {{ states('sensor.battery_state_of_charge') }}%"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "🛡️ Grid Guard: STOP ladowania!"
    message: "Import: {{ states('sensor.meter_active_power_total') }}W | SOC: {{ states('sensor.battery_state_of_charge') }}%"
    mode: single

- id: hems_pv_surplus_smart_charge
  alias: "HEMS: PV Surplus → laduj baterie (drogie godziny)"
  description: "meter UJEMNY > 300W = eksport = nadwyzka PV → laduj baterie."
  triggers:
  - trigger: numeric_state
    entity_id: sensor.meter_active_power_total
    below: -300
    for:
    seconds: 60
    conditions:
  - condition: template
    value_template: "{{ is_state('input_boolean.hems_force_grid_charge', 'off') and is_state('input_boolean.hems_force_battery_discharge', 'off') }}"
  - condition: time
    weekday: [mon, tue, wed, thu, fri]
  - condition: template
    value_template: >
    {% set h = now().hour %}
    {% set m = now().month %}
    {% set przedpoludniowy = (h >= 7 and h < 13) %}
    {% set popoludniowy_zima = (m in [1,2,3,10,11,12]) and (h >= 16 and h < 21) %}
    {% set popoludniowy_lato = (m in [4,5,6,7,8,9]) and (h >= 19 and h < 22) %}
    {{ przedpoludniowy or popoludniowy_zima or popoludniowy_lato }}
  - condition: numeric_state
    entity_id: sensor.battery_state_of_charge
    below: 95
  - condition: numeric_state
    entity_id: sensor.pv_power
    above: 500
    actions:
  - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "18.5"
  - action: notify.mobile_app_iphone_henryk
    data:
    title: "☀️🔋 PV Surplus → laduje baterie"
    message: "Eksport: {{ states('sensor.meter_active_power_total') | float | abs | round(0) }}W | SOC: {{ states('sensor.battery_state_of_charge') }}%"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "☀️🔋 PV Surplus → laduje baterie"
    message: "Eksport: {{ states('sensor.meter_active_power_total') | float | abs | round(0) }}W | SOC: {{ states('sensor.battery_state_of_charge') }}%"
    mode: single

# ---- W1: G13 HARMONOGRAM ----

- id: hems_morning_sell_mode
  alias: "HEMS: Rano — sprzedaz lub arbitraz (7:00)"
  triggers:
  - trigger: time
    at: "07:00:00"
    conditions:
  - condition: template
    value_template: "{{ is_state('input_boolean.hems_force_grid_charge', 'off') and is_state('input_boolean.hems_force_battery_discharge', 'off') }}"
  - condition: time
    weekday: [mon, tue, wed, thu, fri]
    actions:
  - action: select.select_option
    target:
    entity_id: select.goodwe_tryb_pracy_falownika
    data:
    option: "general"
  - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: grid_export_limit
    value: "16000"
  - choose: - conditions: - condition: numeric_state
    entity_id: sensor.rce_pse_cena
    below: 100
    sequence: - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "18.5" - action: notify.mobile_app_iphone_henryk
    data:
    title: "⚡🔋 G13 7:00: RCE niska → ARBITRAZ"
    message: "RCE: {{ states('sensor.rce_sell_price') }} zl | SOC: {{ states('sensor.battery_state_of_charge') }}%" - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "⚡🔋 G13 7:00: RCE niska → ARBITRAZ"
    message: "RCE: {{ states('sensor.rce_sell_price') }} zl | SOC: {{ states('sensor.battery_state_of_charge') }}%"
    default: - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "0" - action: notify.mobile_app_iphone_henryk
    data:
    title: "☀️ G13: Sprzedaz (0.91 zl)"
    message: "RCE: {{ states('sensor.rce_sell_price') }} zl | SOC: {{ states('sensor.battery_state_of_charge') }}%" - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "☀️ G13: Sprzedaz (0.91 zl)"
    message: "RCE: {{ states('sensor.rce_sell_price') }} zl | SOC: {{ states('sensor.battery_state_of_charge') }}%"
    mode: single

- id: hems_midday_charge_mode
  alias: "HEMS: Off-peak — ladowanie baterii (13:00)"
  triggers:
  - trigger: time
    at: "13:00:00"
    conditions:
  - condition: template
    value_template: "{{ is_state('input_boolean.hems_force_grid_charge', 'off') and is_state('input_boolean.hems_force_battery_discharge', 'off') }}"
  - condition: time
    weekday: [mon, tue, wed, thu, fri]
    actions:
  - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "18.5"
  - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: grid_export_limit
    value: "16000"
  - action: select.select_option
    target:
    entity_id: select.goodwe_tryb_pracy_falownika
    data:
    option: "general"
  - action: notify.mobile_app_iphone_henryk
    data:
    title: "🔋 G13: Ladowanie (0.63 zl)"
    message: "RCE: {{ states('sensor.rce_sell_price') }} zl | SOC: {{ states('sensor.battery_state_of_charge') }}%"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "🔋 G13: Ladowanie (0.63 zl)"
    message: "RCE: {{ states('sensor.rce_sell_price') }} zl | SOC: {{ states('sensor.battery_state_of_charge') }}%"
    mode: single

- id: hems_evening_peak_start
  alias: "HEMS: Szczyt wieczorny — bateria zasila dom"
  triggers:
  - trigger: template
    value_template: "{{ states('sensor.g13_is_afternoon_peak') == 'on' }}"
    conditions:
  - condition: time
    weekday: [mon, tue, wed, thu, fri]
    actions:
  - action: notify.mobile_app_iphone_henryk
    data:
    title: "💰 SZCZYT G13 (1.50 zl) + RCE {{ states('sensor.rce_sell_price') }} zl"
    message: "SOC: {{ states('sensor.battery_state_of_charge') }}%"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "💰 SZCZYT G13 (1.50 zl) + RCE {{ states('sensor.rce_sell_price') }} zl"
    message: "SOC: {{ states('sensor.battery_state_of_charge') }}%"
    mode: single

- id: hems_weekend_mode
  alias: "HEMS: Weekend — autokonsumpcja"
  triggers:
  - trigger: time
    at: "07:00:00"
    conditions:
  - condition: template
    value_template: "{{ is_state('input_boolean.hems_force_grid_charge', 'off') and is_state('input_boolean.hems_force_battery_discharge', 'off') }}"
  - condition: time
    weekday: [sat, sun]
    actions:
  - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "18.5"
  - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: grid_export_limit
    value: "16000"
  - action: select.select_option
    target:
    entity_id: select.goodwe_tryb_pracy_falownika
    data:
    option: "general"
  - action: notify.mobile_app_iphone_henryk
    data:
    title: "🏖️ Weekend — off-peak"
    message: "RCE: {{ states('sensor.rce_sell_price') }} zl"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "🏖️ Weekend — off-peak"
    message: "RCE: {{ states('sensor.rce_sell_price') }} zl"
    mode: single

# ---- W2: RCE DYNAMICZNA ----

- id: hems_rce_cheapest_window_charge
  alias: "HEMS RCE: Najtansze okno → laduj baterie"
  triggers:
  - trigger: state
    entity_id: binary_sensor.rce_pse_tanie_okno_aktywne
    to: "on"
    conditions:
  - condition: template
    value_template: "{{ is_state('input_boolean.hems_force_grid_charge', 'off') and is_state('input_boolean.hems_force_battery_discharge', 'off') }}"
  - condition: numeric_state
    entity_id: sensor.battery_state_of_charge
    below: 95
    actions:
  - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "18.5"
  - action: notify.mobile_app_iphone_henryk
    data:
    title: "🟢 RCE: NAJTANSZE OKNO — laduje"
    message: "RCE: {{ states('sensor.rce_sell_price') }} zl | SOC: {{ states('sensor.battery_state_of_charge') }}%"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "🟢 RCE: NAJTANSZE OKNO — laduje"
    message: "RCE: {{ states('sensor.rce_sell_price') }} zl | SOC: {{ states('sensor.battery_state_of_charge') }}%"
    mode: single

- id: hems_rce_cheapest_window_end
  alias: "HEMS RCE: Koniec najtanszego okna → wroc do sprzedazy"
  triggers:
  - trigger: state
    entity_id: binary_sensor.rce_pse_tanie_okno_aktywne
    to: "off"
    conditions:
  - condition: template
    value_template: "{{ is_state('input_boolean.hems_force_grid_charge', 'off') and is_state('input_boolean.hems_force_battery_discharge', 'off') }}"
  - condition: template
    value_template: "{{ now().hour >= 7 and now().hour < 13 }}"
  - condition: time
    weekday: [mon, tue, wed, thu, fri]
  - condition: numeric_state
    entity_id: sensor.rce_pse_cena
    above: 100
    actions:
  - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "0"
    mode: single

- id: hems_rce_expensive_window_alert
  alias: "HEMS RCE: Najdrozsze okno → max oszczednosc"
  triggers:
  - trigger: state
    entity_id: binary_sensor.rce_pse_drogie_okno_aktywne
    to: "on"
    actions:
  - action: notify.mobile_app_iphone_henryk
    data:
    title: "🔴 RCE: NAJDROZSZE OKNO!"
    message: "RCE: {{ states('sensor.rce_sell_price') }} zl | SOC: {{ states('sensor.battery_state_of_charge') }}%"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "🔴 RCE: NAJDROZSZE OKNO!"
    message: "RCE: {{ states('sensor.rce_sell_price') }} zl | SOC: {{ states('sensor.battery_state_of_charge') }}%"
    mode: single

- id: hems_rce_low_price_charge
  alias: "HEMS RCE: Niska cena → laduj baterie"
  triggers:
  - trigger: numeric_state
    entity_id: sensor.rce_pse_cena
    below: 150
    for:
    minutes: 2
    conditions:
  - condition: template
    value_template: "{{ is_state('input_boolean.hems_force_grid_charge', 'off') and is_state('input_boolean.hems_force_battery_discharge', 'off') }}"
  - condition: numeric_state
    entity_id: sensor.battery_state_of_charge
    below: 95
  - condition: sun
    after: sunrise
    before: sunset
    actions:
  - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "18.5"
  - action: notify.mobile_app_iphone_henryk
    data:
    title: "📉 RCE niska ({{ states('sensor.rce_sell_price') }} zl) → laduje"
    message: "Trend: {{ states('sensor.rce_price_trend') }}"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "📉 RCE niska ({{ states('sensor.rce_sell_price') }} zl) → laduje"
    message: "Trend: {{ states('sensor.rce_price_trend') }}"
    mode: single

- id: hems_rce_price_restored_sell
  alias: "HEMS RCE: Cena wzrosla → wroc do sprzedazy"
  triggers:
  - trigger: numeric_state
    entity_id: sensor.rce_pse_cena
    above: 300
    for:
    minutes: 2
    conditions:
  - condition: template
    value_template: "{{ is_state('input_boolean.hems_force_grid_charge', 'off') and is_state('input_boolean.hems_force_battery_discharge', 'off') }}"
  - condition: template
    value_template: "{{ now().hour >= 7 and now().hour < 13 }}"
  - condition: time
    weekday: [mon, tue, wed, thu, fri]
  - condition: numeric_state
    entity_id: sensor.battery_state_of_charge
    above: 40
    actions:
  - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "0"
  - action: notify.mobile_app_iphone_henryk
    data:
    title: "📈 RCE wzrosla ({{ states('sensor.rce_sell_price') }} zl) → sprzedaje"
    message: "Trend: {{ states('sensor.rce_price_trend') }}"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "📈 RCE wzrosla ({{ states('sensor.rce_sell_price') }} zl) → sprzedaje"
    message: "Trend: {{ states('sensor.rce_price_trend') }}"
    mode: single

- id: hems_rce_high_price_evening
  alias: "HEMS RCE: Wysoka cena wieczorem"
  triggers:
  - trigger: numeric_state
    entity_id: sensor.rce_pse_cena
    above: 500
    for:
    minutes: 2
    conditions:
  - condition: template
    value_template: "{{ states('sensor.g13_is_afternoon_peak') == 'on' }}"
  - condition: numeric_state
    entity_id: sensor.battery_state_of_charge
    above: 40
    actions:
  - action: notify.mobile_app_iphone_henryk
    data:
    title: "💰💰 RCE PEAK + G13 SZCZYT!"
    message: "RCE: {{ states('sensor.rce_sell_price') }} zl | SOC: {{ states('sensor.battery_state_of_charge') }}%"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "💰💰 RCE PEAK + G13 SZCZYT!"
    message: "RCE: {{ states('sensor.rce_sell_price') }} zl | SOC: {{ states('sensor.battery_state_of_charge') }}%"
    mode: single

- id: hems_rce_negative_price
  alias: "HEMS RCE: Cena ultra-niska / ujemna → laduj + bojler"
  triggers:
  - trigger: numeric_state
    entity_id: sensor.rce_pse_cena
    below: 50
    for:
    minutes: 1
    conditions:
  - condition: template
    value_template: "{{ is_state('input_boolean.hems_force_grid_charge', 'off') and is_state('input_boolean.hems_force_battery_discharge', 'off') }}"
  - condition: numeric_state
    entity_id: sensor.battery_state_of_charge
    below: 95
    actions:
  - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "18.5"
  - action: switch.turn_on
    target:
    entity_id: switch.bojler_3800
  - action: notify.mobile_app_iphone_henryk
    data:
    title: "{{ '🤑 RCE UJEMNA!' if states('sensor.rce_pse_cena') | float(0) < 0 else '⚡ RCE ultra-niska!' }} ({{ states('sensor.rce_pse_cena') }} PLN/MWh)"
    message: "Laduje baterie + bojler ON | SOC: {{ states('sensor.battery_state_of_charge') }}%"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "{{ '🤑 RCE UJEMNA!' if states('sensor.rce_pse_cena') | float(0) < 0 else '⚡ RCE ultra-niska!' }} ({{ states('sensor.rce_pse_cena') }} PLN/MWh)"
    message: "Laduje baterie + bojler ON | SOC: {{ states('sensor.battery_state_of_charge') }}%"
    mode: single

# ---- W3: SOC BEZPIECZENSTWO ----

- id: hems_smart_soc_protection
  alias: "HEMS v2: Smart SOC Protection (tariff-aware)"
  triggers:
  - trigger: numeric_state
    entity_id: sensor.battery_state_of_charge
    below: 20
    for:
    minutes: 3
    conditions:
  - condition: template
    value_template: "{{ is_state('input_boolean.hems_force_grid_charge', 'off') and is_state('input_boolean.hems_force_battery_discharge', 'off') }}"
    actions:
  - choose: - conditions: - condition: time
    weekday: [mon, tue, wed, thu, fri] - condition: template
    value_template: >
    {% set h = now().hour %}
    {% set m = now().month %}
    {{ (h >= 7 and h < 13) or (m in [1,2,3,10,11,12] and h >= 16 and h < 21) or (m in [4,5,6,7,8,9] and h >= 19 and h < 22) }}
    sequence: - action: notify.mobile_app_iphone_henryk
    data:
    title: "🔋⚠️ SOC {{ states('sensor.battery_state_of_charge') }}% — DROGA TARYFA!"
    message: "NIE laduje z sieci. PV: {{ states('sensor.pv_power') }}W | Dom: {{ states('sensor.load') }}W" - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "🔋⚠️ SOC {{ states('sensor.battery_state_of_charge') }}% — DROGA TARYFA!"
    message: "NIE laduje z sieci. Tylko PV surplus."
    default: - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "18.5" - action: notify.mobile_app_iphone_henryk
    data:
    title: "🔋 Ochrona SOC — laduje (tania taryfa)"
    message: "SOC: {{ states('sensor.battery_state_of_charge') }}%" - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "🔋 Ochrona SOC — laduje (tania taryfa)"
    message: "SOC: {{ states('sensor.battery_state_of_charge') }}%"
    mode: single

- id: hems_emergency_soc_critical
  alias: "HEMS v2: EMERGENCY SOC < 5%"
  triggers:
  - trigger: numeric_state
    entity_id: sensor.battery_state_of_charge
    below: 5
    for:
    seconds: 30
    conditions:
  - condition: template
    value_template: "{{ is_state('input_boolean.hems_force_grid_charge', 'off') and is_state('input_boolean.hems_force_battery_discharge', 'off') }}"
    actions:
  - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "18.5"
  - action: notify.mobile_app_iphone_henryk
    data:
    title: "🚨🔋 EMERGENCY: SOC {{ states('sensor.battery_state_of_charge') }}%!"
    message: "Laduje awaryjnie niezaleznie od taryfy."
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "🚨🔋 EMERGENCY: SOC {{ states('sensor.battery_state_of_charge') }}%!"
    message: "Laduje awaryjnie."
  - wait_for_trigger:
    - trigger: numeric_state
      entity_id: sensor.battery_state_of_charge
      above: 15
      timeout: "01:00:00"
      continue_on_timeout: true
  - choose: - conditions: - condition: template
    value_template: >
    {% set h = now().hour %}{% set m = now().month %}{% set wd = now().weekday() %}
    {% if wd < 5 %}{{ (h >= 7 and h < 13) or (m in [1,2,3,10,11,12] and h >= 16 and h < 21) or (m in [4,5,6,7,8,9] and h >= 19 and h < 22) }}{% else %}false{% endif %}
    sequence: - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "0" - action: notify.mobile_app_iphone_henryk
    data:
    title: "🔋 Emergency STOP — SOC {{ states('sensor.battery_state_of_charge') }}%"
    message: "Drogi szczyt — wracam do PV-only." - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "🔋 Emergency STOP — SOC {{ states('sensor.battery_state_of_charge') }}%"
    message: "PV-only charge."
    mode: single

- id: hems_smart_soc_check_11_v2
  alias: "HEMS v2: SOC check 11:00 — PV-only w szczycie"
  triggers:
  - trigger: time
    at: "11:00:00"
    conditions:
  - condition: template
    value_template: "{{ is_state('input_boolean.hems_force_grid_charge', 'off') and is_state('input_boolean.hems_force_battery_discharge', 'off') }}"
  - condition: numeric_state
    entity_id: sensor.battery_state_of_charge
    below: 50
  - condition: time
    weekday: [mon, tue, wed, thu, fri]
    actions:
  - choose: - conditions: - condition: numeric_state
    entity_id: sensor.meter_active_power_total
    below: -200
    sequence: - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "18.5" - action: notify.mobile_app_iphone_henryk
    data:
    title: "⚠️🔋 SOC {{ states('sensor.battery_state_of_charge') }}% — laduje z PV"
    message: "Nadwyzka PV: {{ states('sensor.meter_active_power_total') }}W. Guard czuwa!" - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "⚠️🔋 SOC {{ states('sensor.battery_state_of_charge') }}% — laduje z PV"
    message: "Guard czuwa!"
    default: - action: notify.mobile_app_iphone_henryk
    data:
    title: "⚠️🔋 SOC {{ states('sensor.battery_state_of_charge') }}% — brak nadwyzki PV!"
    message: "NIE laduje z sieci. PV: {{ states('sensor.pv_power') }}W" - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "⚠️🔋 SOC {{ states('sensor.battery_state_of_charge') }}% — brak nadwyzki!"
    message: "Czekam na PV surplus."
    mode: single

- id: hems_smart_soc_check_12_v2
  alias: "HEMS v2: SOC check 12:00 — ostatnia szansa PV"
  triggers:
  - trigger: time
    at: "12:00:00"
    conditions:
  - condition: template
    value_template: "{{ is_state('input_boolean.hems_force_grid_charge', 'off') and is_state('input_boolean.hems_force_battery_discharge', 'off') }}"
  - condition: numeric_state
    entity_id: sensor.battery_state_of_charge
    below: 70
  - condition: time
    weekday: [mon, tue, wed, thu, fri]
    actions:
  - choose: - conditions: - condition: numeric_state
    entity_id: sensor.meter_active_power_total
    below: -200
    sequence: - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "18.5" - action: notify.mobile_app_iphone_henryk
    data:
    title: "⚠️ SOC {{ states('sensor.battery_state_of_charge') }}% — laduje z PV"
    message: "Za godzine off-peak. Guard czuwa!" - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "⚠️ SOC {{ states('sensor.battery_state_of_charge') }}% — laduje z PV"
    message: "Guard czuwa!"
    default: - action: notify.mobile_app_iphone_henryk
    data:
    title: "⚠️ SOC {{ states('sensor.battery_state_of_charge') }}% — czekam na 13:00"
    message: "Off-peak za godzine (0.63 zl)." - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "⚠️ SOC {{ states('sensor.battery_state_of_charge') }}% — czekam na 13:00"
    message: "Off-peak za godzine."
    mode: single

# ---- ARBITRAZ NOCNY ----

- id: hems_night_grid_charge
  alias: "HEMS: Nocne ladowanie z sieci (tania taryfa)"
  triggers:
  - trigger: time
    at: "23:00:00"
    conditions:
  - condition: template
    value_template: "{{ is_state('input_boolean.hems_force_grid_charge', 'off') and is_state('input_boolean.hems_force_battery_discharge', 'off') }}"
  - condition: numeric_state
    entity_id: sensor.battery_state_of_charge
    below: 50
  - condition: time
    weekday: [sun, mon, tue, wed, thu]
  - condition: or
    conditions: - condition: state
    entity_id: input_boolean.hems_night_arbitrage_approved
    state: "on" - condition: numeric_state
    entity_id: sensor.pv_forecast_tomorrow_total
    below: 8
    actions:
  - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "18.5"
  - action: select.select_option
    target:
    entity_id: select.goodwe_tryb_pracy_falownika
    data:
    option: "eco_charge"
  - action: notify.mobile_app_iphone_henryk
    data:
    title: "🌙 Nocny arbitraz (0.63 → 1.50 zl)"
    message: "Prognoza: {{ states('sensor.pv_forecast_tomorrow_total') }} kWh | SOC: {{ states('sensor.battery_state_of_charge') }}%"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "🌙 Nocny arbitraz (0.63 → 1.50 zl)"
    message: "SOC: {{ states('sensor.battery_state_of_charge') }}%"
    mode: single

- id: hems_night_charge_stop
  alias: "HEMS: Stop ladowania nocnego"
  triggers:
  - trigger: numeric_state
    entity_id: sensor.battery_state_of_charge
    above: 90
  - trigger: time
    at: "06:00:00"
    conditions:
  - condition: template
    value_template: "{{ is_state('input_boolean.hems_force_grid_charge', 'off') and is_state('input_boolean.hems_force_battery_discharge', 'off') }}"
  - condition: state
    entity_id: select.goodwe_tryb_pracy_falownika
    state: "eco_charge"
    actions:
  - action: select.select_option
    target:
    entity_id: select.goodwe_tryb_pracy_falownika
    data:
    option: "general"
  - action: input_boolean.turn_off
    target:
    entity_id: input_boolean.hems_night_arbitrage_approved
    mode: single

# ---- RCE v2: ENERGY COMPASS (PDGSZ) ----

# Kompas Energetyczny PSE dostarcza godzinowe sygnaly usage_fcst:

# 1 = normal_usage (normalne uzytkowanie)

# 0 = recommended_usage (zalecane ograniczenie — grid stress!)

# Gdy PSE sygnalizuje "recommended_usage" dla BIEZACEJ godziny:

# → ograniczamy eksport, priorytetyzujemy samowystarczalnosc

- id: hems_compass_grid_stress_guard
  alias: "HEMS RCE v2: Kompas — grid stress → ogranicz eksport"
  triggers:
  - trigger: time_pattern
    minutes: "/15"
    conditions:
  - condition: template
    value_template: "{{ is_state('input_boolean.hems_force_grid_charge', 'off') and is_state('input_boolean.hems_force_battery_discharge', 'off') }}"
  - condition: template
    value_template: >
    {% set compass = state_attr('sensor.rce_pse_kompas_energetyczny_dzisiaj', 'values') %}
    {% if compass is none %}false
    {% else %}
    {% set h = now().hour %}
    {% set current = compass | selectattr('dtime', 'search', now().strftime('%Y-%m-%d') ~ ' ' ~ '%02d' | format(h)) | list %}
    {% if current | length > 0 %}{{ current[0].state == 'recommended_usage' }}
    {% else %}false{% endif %}
    {% endif %}
  - condition: sun
    after: sunrise
    before: sunset
    actions:
  - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: grid_export_limit
    value: "0"
  - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "18.5"
  - action: notify.mobile_app_iphone_henryk
    data:
    title: "🧭 PSE Kompas: GRID STRESS — ogranicz eksport!"
    message: "PSE zaleca ograniczenie uzytkowania ({{ now().strftime('%H:%M') }}). Eksport=0, priorytet bateria. SOC: {{ states('sensor.battery_state_of_charge') }}%"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "🧭 PSE Kompas: GRID STRESS"
    message: "Eksport=0 | SOC: {{ states('sensor.battery_state_of_charge') }}%"
    mode: single

- id: hems_compass_grid_stress_end
  alias: "HEMS RCE v2: Kompas — grid stress KONIEC → przywroc eksport"
  triggers:
  - trigger: time_pattern
    minutes: "/15"
    conditions:
  - condition: template
    value_template: "{{ is_state('input_boolean.hems_force_grid_charge', 'off') and is_state('input_boolean.hems_force_battery_discharge', 'off') }}"
  - condition: template
    value_template: >
    {% set compass = state_attr('sensor.rce_pse_kompas_energetyczny_dzisiaj', 'values') %}
    {% if compass is none %}false
    {% else %}
    {% set h = now().hour %}
    {% set current = compass | selectattr('dtime', 'search', now().strftime('%Y-%m-%d') ~ ' ' ~ '%02d' | format(h)) | list %}
    {% if current | length > 0 %}{{ current[0].state == 'normal_usage' }}
    {% else %}true{% endif %}
    {% endif %}
  - condition: numeric_state
    entity_id: sensor.goodwe_grid_export_limit
    below: 100
  - condition: sun
    after: sunrise
    before: sunset
    actions:
  - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: grid_export_limit
    value: "16000"
  - action: notify.mobile_app_iphone_henryk
    data:
    title: "🧭✅ PSE Kompas: normalne uzytkowanie → eksport ON"
    message: "Grid stress zakonczony. Eksport przywrocony do 16kW. SOC: {{ states('sensor.battery_state_of_charge') }}%"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "🧭✅ Kompas: eksport ON"
    message: "SOC: {{ states('sensor.battery_state_of_charge') }}%"
    mode: single

# ---- RCE v2: TOMORROW PRICE PLANNER (15:00) ----

# O 14:00 PSE publikuje ceny na jutro. O 15:00 analizujemy:

# - srednia cena jutro vs srednia cena dzis

# - kompas energetyczny jutro

# - prognoza PV jutro

# Decyzja: nocny arbitraz (ladowanie z sieci 23:00-06:00)

# zamiast sztywnego progu 8 kWh PV forecast

- id: hems_tomorrow_price_planner
  alias: "HEMS RCE v2: Tomorrow Price Planner (15:00)"
  triggers:
  - trigger: time
    at: "15:00:00"
    conditions:
  - condition: template
    value_template: "{{ is_state('input_boolean.hems_force_grid_charge', 'off') and is_state('input_boolean.hems_force_battery_discharge', 'off') }}"
  - condition: template
    value_template: "{{ states('sensor.rce_pse_srednia_cena_jutro') not in ['unavailable', 'unknown'] }}"
    actions:
  - choose: - conditions: - condition: template
    value_template: >
    {% set avg_tomorrow = states('sensor.rce_pse_srednia_cena_jutro') | float(0) %}
    {% set pv_forecast = states('sensor.pv_forecast_tomorrow_total') | float(0) %}
    {% set jutro_vs_dzis = states('sensor.rce_pse_jutro_vs_dzisiaj_srednia') | float(0) %}
    {{ avg_tomorrow > 400 and pv_forecast < 12 and jutro_vs_dzis > 10 }}
    sequence: - action: input_boolean.turn_on
    target:
    entity_id: input_boolean.hems_night_arbitrage_approved - action: notify.mobile_app_iphone_henryk
    data:
    title: "📊🌙 RCE Planner: NOCNY ARBITRAZ ZATWIERDZONY"
    message: "Jutro RCE srednia: {{ states('sensor.rce_pse_srednia_cena_jutro') }} PLN/MWh ({{ states('sensor.rce_pse_jutro_vs_dzisiaj_srednia') }}% wyzej). PV: {{ states('sensor.pv_forecast_tomorrow_total') }} kWh. SOC: {{ states('sensor.battery_state_of_charge') }}%" - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "📊🌙 NOCNY ARBITRAZ → TAK"
    message: "RCE jutro: {{ states('sensor.rce_pse_srednia_cena_jutro') }} PLN/MWh (+{{ states('sensor.rce_pse_jutro_vs_dzisiaj_srednia') }}%)" - conditions: - condition: template
    value_template: >
    {% set avg_tomorrow = states('sensor.rce_pse_srednia_cena_jutro') | float(0) %}
    {% set pv_forecast = states('sensor.pv_forecast_tomorrow_total') | float(0) %}
    {{ avg_tomorrow < 200 or pv_forecast > 15 }}
    sequence: - action: input_boolean.turn_off
    target:
    entity_id: input_boolean.hems_night_arbitrage_approved - action: notify.mobile_app_iphone_henryk
    data:
    title: "📊 RCE Planner: BRAK ARBITRAZU"
    message: "Jutro RCE srednia: {{ states('sensor.rce_pse_srednia_cena_jutro') }} PLN/MWh. PV: {{ states('sensor.pv_forecast_tomorrow_total') }} kWh. Nie oplacalne." - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "📊 BRAK ARBITRAZU"
    message: "RCE jutro niska ({{ states('sensor.rce_pse_srednia_cena_jutro') }}) lub dobre PV ({{ states('sensor.pv_forecast_tomorrow_total') }} kWh)"
    default: - action: notify.mobile_app_iphone_henryk
    data:
    title: "📊 RCE Planner: NEUTRALNE"
    message: "Jutro RCE: {{ states('sensor.rce_pse_srednia_cena_jutro') }} PLN/MWh | PV: {{ states('sensor.pv_forecast_tomorrow_total') }} kWh | Standardowe zasady nocne" - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "📊 RCE Planner: NEUTRALNE"
    message: "Standard rules"
    mode: single

# ---- KASKADA NAPIECIA ----

- id: hems_voltage_high_bojler_on
  alias: "HEMS: Wysokie napiecie → Bojler ON"
  triggers:
  - trigger: numeric_state
    entity_id: sensor.on_grid_l1_voltage
    above: 252
    for:
    seconds: 30
  - trigger: numeric_state
    entity_id: sensor.on_grid_l2_voltage
    above: 252
    for:
    seconds: 30
  - trigger: numeric_state
    entity_id: sensor.on_grid_l3_voltage
    above: 252
    for:
    seconds: 30
    conditions:
  - condition: state
    entity_id: switch.bojler_3800
    state: "off"
  - condition: sun
    after: sunrise
    before: sunset
    actions:
  - action: switch.turn_on
    target:
    entity_id: switch.bojler_3800
  - action: notify.mobile_app_iphone_henryk
    data:
    title: "⚡ Wysokie napiecie → Bojler ON"
    message: "L1:{{ states('sensor.on_grid_l1_voltage') }}V L2:{{ states('sensor.on_grid_l2_voltage') }}V L3:{{ states('sensor.on_grid_l3_voltage') }}V"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "⚡ Wysokie napiecie → Bojler ON"
    message: "L1:{{ states('sensor.on_grid_l1_voltage') }}V L2:{{ states('sensor.on_grid_l2_voltage') }}V L3:{{ states('sensor.on_grid_l3_voltage') }}V"
    mode: single

- id: hems_voltage_high_klima_on
  alias: "HEMS: Bardzo wysokie napiecie → Klima ON"
  triggers:
  - trigger: numeric_state
    entity_id: sensor.on_grid_l1_voltage
    above: 253
    for:
    seconds: 30
  - trigger: numeric_state
    entity_id: sensor.on_grid_l2_voltage
    above: 253
    for:
    seconds: 30
  - trigger: numeric_state
    entity_id: sensor.on_grid_l3_voltage
    above: 253
    for:
    seconds: 30
    conditions:
  - condition: state
    entity_id: switch.bojler_3800
    state: "on"
  - condition: state
    entity_id: switch.klimatyzacja_socket_1
    state: "off"
  - condition: sun
    after: sunrise
    before: sunset
    actions:
  - action: switch.turn_on
    target:
    entity_id: switch.klimatyzacja_socket_1
  - action: notify.mobile_app_iphone_henryk
    data:
    title: "⚡⚡ Bardzo wysokie napiecie → Klima ON"
    message: "L1:{{ states('sensor.on_grid_l1_voltage') }}V L2:{{ states('sensor.on_grid_l2_voltage') }}V L3:{{ states('sensor.on_grid_l3_voltage') }}V"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "⚡⚡ Bardzo wysokie napiecie → Klima ON"
    message: "L1:{{ states('sensor.on_grid_l1_voltage') }}V L2:{{ states('sensor.on_grid_l2_voltage') }}V L3:{{ states('sensor.on_grid_l3_voltage') }}V"
    mode: single

- id: hems_voltage_critical_charge_battery
  alias: "HEMS: Krytyczne napiecie → Laduj baterie"
  triggers:
  - trigger: numeric_state
    entity_id: sensor.on_grid_l1_voltage
    above: 254
    for:
    seconds: 15
  - trigger: numeric_state
    entity_id: sensor.on_grid_l2_voltage
    above: 254
    for:
    seconds: 15
  - trigger: numeric_state
    entity_id: sensor.on_grid_l3_voltage
    above: 254
    for:
    seconds: 15
    conditions:
  - condition: template
    value_template: "{{ is_state('input_boolean.hems_force_grid_charge', 'off') and is_state('input_boolean.hems_force_battery_discharge', 'off') }}"
  - condition: numeric_state
    entity_id: sensor.battery_state_of_charge
    below: 98
    actions:
  - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "18.5"
  - action: notify.mobile_app_iphone_henryk
    data:
    title: "🔴 Krytyczne napiecie → Laduj baterie!"
    message: "L1:{{ states('sensor.on_grid_l1_voltage') }}V"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "🔴 Krytyczne napiecie → Laduj baterie!"
    message: "L1:{{ states('sensor.on_grid_l1_voltage') }}V"
    mode: single

- id: hems_voltage_normal_restore
  alias: "HEMS: Napiecie normalne → Przywroc tryb"
  triggers:
  - trigger: template
    value_template: >
    {{ states('sensor.on_grid_l1_voltage') | float(0) < 248
             and states('sensor.on_grid_l2_voltage') | float(0) < 248
             and states('sensor.on_grid_l3_voltage') | float(0) < 248 }}
    for:
    minutes: 5
    conditions:
  - condition: template
    value_template: "{{ is_state('input_boolean.hems_force_grid_charge', 'off') and is_state('input_boolean.hems_force_battery_discharge', 'off') }}"
  - condition: sun
    after: sunrise
    before: sunset
    actions:
  - action: switch.turn_off
    target:
    entity_id: switch.bojler_3800
  - action: switch.turn_off
    target:
    entity_id: switch.klimatyzacja_socket_1
  - choose: - conditions: - condition: time
    before: "13:00:00" - condition: numeric_state
    entity_id: sensor.rce_pse_cena
    above: 200 - condition: time
    weekday: [mon, tue, wed, thu, fri]
    sequence: - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "0"
    mode: single

# ---- BEZPIECZENSTWO ----

- id: hems_weak_forecast_protect
  alias: "HEMS: Slaba prognoza — zachowaj baterie"
  triggers:
  - trigger: time
    at: "20:00:00"
    conditions:
  - condition: numeric_state
    entity_id: sensor.pv_forecast_tomorrow_total
    below: 5
    actions:
  - action: number.set_value
    target:
    entity_id: number.goodwe_depth_of_discharge_on_grid
    data:
    value: 70
  - action: notify.mobile_app_iphone_henryk
    data:
    title: "🌧️ Slaba prognoza"
    message: "Jutro: {{ states('sensor.pv_forecast_tomorrow_total') }} kWh | DOD → 70%"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "🌧️ Slaba prognoza"
    message: "Jutro: {{ states('sensor.pv_forecast_tomorrow_total') }} kWh | DOD → 70%"
    mode: single

- id: hems_restore_dod_sunrise
  alias: "HEMS: Przywroc DOD po wschodzie"
  triggers:
  - trigger: numeric_state
    entity_id: sensor.pv_power
    above: 500
    for:
    minutes: 10
    conditions:
  - condition: sun
    after: sunrise
    actions:
  - action: number.set_value
    target:
    entity_id: number.goodwe_depth_of_discharge_on_grid
    data:
    value: 95
    mode: single

- id: hems_inverter_warning
  alias: "HEMS: Alert — warning falownika"
  triggers:
  - trigger: numeric_state
    entity_id: sensor.warning_code
    above: 0
    for:
    minutes: 2
    actions:
  - action: notify.mobile_app_iphone_henryk
    data:
    title: "⚠️ Warning falownika"
    message: "Code: {{ states('sensor.warning_code') }}"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "⚠️ Warning falownika"
    message: "Code: {{ states('sensor.warning_code') }}"
    mode: single

# ---- KASKADA NADWYZKI PV ----

- id: hems_surplus_bojler_on
  alias: "HEMS: Nadwyzka → Bojler (L1)"
  triggers:
  - trigger: numeric_state
    entity_id: sensor.hems_pv_surplus_power
    above: 2000
    for:
    minutes: 3
    conditions:
  - condition: numeric_state
    entity_id: sensor.battery_state_of_charge
    above: 80
  - condition: state
    entity_id: switch.bojler_3800
    state: "off"
  - condition: sun
    after: sunrise
    before: sunset
    actions:
  - action: switch.turn_on
    target:
    entity_id: switch.bojler_3800
    mode: single

- id: hems_surplus_bojler_off
  alias: "HEMS: Bojler OFF (brak nadwyzki)"
  triggers:
  - trigger: numeric_state
    entity_id: sensor.hems_pv_surplus_power
    below: 500
    for:
    minutes: 3
    conditions:
  - condition: state
    entity_id: switch.bojler_3800
    state: "on"
    actions:
  - action: switch.turn_off
    target:
    entity_id: switch.bojler_3800
    mode: single

- id: hems_surplus_klima_on
  alias: "HEMS: Nadwyzka → Klima (L2)"
  triggers:
  - trigger: numeric_state
    entity_id: sensor.hems_pv_surplus_power
    above: 3000
    for:
    minutes: 5
    conditions:
  - condition: numeric_state
    entity_id: sensor.battery_state_of_charge
    above: 85
  - condition: state
    entity_id: switch.klimatyzacja_socket_1
    state: "off"
  - condition: state
    entity_id: switch.bojler_3800
    state: "on"
  - condition: sun
    after: sunrise
    before: sunset
    actions:
  - action: switch.turn_on
    target:
    entity_id: switch.klimatyzacja_socket_1
    mode: single

- id: hems_surplus_klima_off
  alias: "HEMS: Klima OFF"
  triggers:
  - trigger: numeric_state
    entity_id: sensor.hems_pv_surplus_power
    below: 1000
    for:
    minutes: 3
    conditions:
  - condition: state
    entity_id: switch.klimatyzacja_socket_1
    state: "on"
    actions:
  - action: switch.turn_off
    target:
    entity_id: switch.klimatyzacja_socket_1
    mode: single

- id: hems_surplus_gniazdko_on
  alias: "HEMS: Nadwyzka → Gniazdko 2 (L3)"
  triggers:
  - trigger: numeric_state
    entity_id: sensor.hems_pv_surplus_power
    above: 4000
    for:
    minutes: 5
    conditions:
  - condition: numeric_state
    entity_id: sensor.battery_state_of_charge
    above: 90
  - condition: state
    entity_id: switch.drugie_gniazdko
    state: "off"
  - condition: state
    entity_id: switch.bojler_3800
    state: "on"
  - condition: state
    entity_id: switch.klimatyzacja_socket_1
    state: "on"
  - condition: sun
    after: sunrise
    before: sunset
    actions:
  - action: switch.turn_on
    target:
    entity_id: switch.drugie_gniazdko
    mode: single

- id: hems_surplus_gniazdko_off
  alias: "HEMS: Gniazdko 2 OFF"
  triggers:
  - trigger: numeric_state
    entity_id: sensor.hems_pv_surplus_power
    below: 1500
    for:
    minutes: 3
    conditions:
  - condition: state
    entity_id: switch.drugie_gniazdko
    state: "on"
    actions:
  - action: switch.turn_off
    target:
    entity_id: switch.drugie_gniazdko
    mode: single

- id: hems_emergency_loads_off
  alias: "HEMS: Awaryjne OFF — SOC < 50%"
  triggers:
  - trigger: numeric_state
    entity_id: sensor.battery_state_of_charge
    below: 50
    conditions:
  - condition: or
    conditions: - condition: state
    entity_id: switch.bojler_3800
    state: "on" - condition: state
    entity_id: switch.klimatyzacja_socket_1
    state: "on" - condition: state
    entity_id: switch.drugie_gniazdko
    state: "on"
    actions:
  - action: switch.turn_off
    target:
    entity_id: - switch.drugie_gniazdko - switch.klimatyzacja_socket_1 - switch.bojler_3800
  - action: notify.mobile_app_iphone_henryk
    data:
    title: "🚨 Awaryjne OFF"
    message: "SOC {{ states('sensor.battery_state_of_charge') }}%"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "🚨 Awaryjne OFF"
    message: "SOC {{ states('sensor.battery_state_of_charge') }}%"
    mode: single

# ---- RAPORT + ALERTY ----

- id: hems_daily_report
  alias: "HEMS: Raport dobowy"
  triggers:
  - trigger: time
    at: "21:00:00"
    actions:
  - action: notify.mobile_app_iphone_henryk
    data:
    title: "📊 Raport G13+RCE"
    message: "☀️PV:{{ states('sensor.today_s_pv_generation') }}kWh 🏠Dom:{{ states('sensor.today_load') }}kWh 📤Export:{{ states('sensor.today_energy_export') }}kWh 📥Import:{{ states('sensor.today_energy_import') }}kWh 🔋SOC:{{ states('sensor.battery_state_of_charge') }}% 💰Bilans:{{ states('sensor.g13_net_balance_today') }}zl"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "📊 Raport G13+RCE"
    message: "☀️PV:{{ states('sensor.today_s_pv_generation') }}kWh 🏠Dom:{{ states('sensor.today_load') }}kWh 🔋SOC:{{ states('sensor.battery_state_of_charge') }}%"
    mode: single

- id: hems_phase_imbalance
  alias: "HEMS: Alert — nierownoowaga faz"
  triggers:
  - trigger: numeric_state
    entity_id: sensor.goodwe_load_balance_difference
    above: 3000
    for:
    minutes: 10
    actions:
  - action: notify.mobile_app_iphone_henryk
    data:
    title: "⚖️ Nierownoowaga faz!"
    message: "L1:{{ states('sensor.load_l1') }}W L2:{{ states('sensor.load_l2') }}W L3:{{ states('sensor.load_l3') }}W"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "⚖️ Nierownoowaga faz!"
    message: "L1:{{ states('sensor.load_l1') }}W L2:{{ states('sensor.load_l2') }}W L3:{{ states('sensor.load_l3') }}W"
    mode: single

# ---- SMART PRE-PEAK CHARGE ----

- id: hems_smart_precharge_weather
  alias: "HEMS: Smart Pre-Peak Charge — pogoda Ecowitt + prognoza PV"
  triggers:
  - id: morning_precheck
    trigger: time
    at: "05:30:00"
  - id: midmorning_verify
    trigger: time
    at: "10:00:00"
  - id: prepeak_afternoon_winter
    trigger: time
    at: "13:30:00"
  - id: prepeak_afternoon_summer
    trigger: time
    at: "18:00:00"
  - id: sudden_cloud_cover
    trigger: numeric_state
    entity_id: sensor.ecowitt_solar_radiation_9747
    below: 50
    for:
    minutes: 20
  - id: rain_started
    trigger: numeric_state
    entity_id: sensor.ecowitt_rain_rate_9747
    above: 0.5
    for:
    minutes: 5
    conditions:
  - condition: template
    value_template: "{{ is_state('input_boolean.hems_force_grid_charge', 'off') and is_state('input_boolean.hems_force_battery_discharge', 'off') }}"
  - condition: time
    weekday: [mon, tue, wed, thu, fri]
    actions:
  - choose: - conditions: - condition: trigger
    id: morning_precheck - condition: numeric_state
    entity_id: sensor.battery_state_of_charge
    below: 80 - condition: numeric_state
    entity_id: sensor.pv_forecast_today_total
    below: 10
    sequence: - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "18.5" - action: select.select_option
    target:
    entity_id: select.goodwe_tryb_pracy_falownika
    data:
    option: "eco_charge" - action: notify.mobile_app_iphone_henryk
    data:
    title: "🌧️⚡ Smart Pre-Peak: Laduje z sieci (05:30)"
    message: "PV forecast: {{ states('sensor.pv_forecast_today_total') }} kWh | SOC: {{ states('sensor.battery_state_of_charge') }}%" - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "🌧️⚡ Smart Pre-Peak: Laduje z sieci (05:30)"
    message: "SOC: {{ states('sensor.battery_state_of_charge') }}%" - delay: "01:25:00" - action: select.select_option
    target:
    entity_id: select.goodwe_tryb_pracy_falownika
    data:
    option: "general" - conditions: - condition: trigger
    id: midmorning_verify - condition: numeric_state
    entity_id: sensor.battery_state_of_charge
    below: 60 - condition: or
    conditions: - condition: numeric_state
    entity_id: sensor.ecowitt_solar_radiation_9747
    below: 200 - condition: numeric_state
    entity_id: sensor.ecowitt_rain_rate_9747
    above: 0 - condition: template
    value_template: "{{ states('sensor.ecowitt_uv_index_9747') | int(0) == 0 }}"
    sequence: - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "18.5" - action: notify.mobile_app_iphone_henryk
    data:
    title: "🌥️ Pogoda slaba — priorytet bateria (10:00)"
    message: "Radiacja: {{ states('sensor.ecowitt_solar_radiation_9747') }} W/m2 | SOC: {{ states('sensor.battery_state_of_charge') }}%" - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "🌥️ Pogoda slaba — priorytet bateria (10:00)"
    message: "SOC: {{ states('sensor.battery_state_of_charge') }}%" - conditions: - condition: trigger
    id: prepeak_afternoon_winter - condition: numeric_state
    entity_id: sensor.battery_state_of_charge
    below: 80 - condition: template
    value_template: "{{ now().month in [1, 2, 3, 10, 11, 12] }}" - condition: or
    conditions: - condition: numeric_state
    entity_id: sensor.pv_forecast_remaining_today_total
    below: 5 - condition: numeric_state
    entity_id: sensor.ecowitt_solar_radiation_9747
    below: 150 - condition: numeric_state
    entity_id: sensor.ecowitt_rain_rate_9747
    above: 0
    sequence: - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "18.5" - action: notify.mobile_app_iphone_henryk
    data:
    title: "⚠️🔋 OSTATNIA SZANSA — szczyt za 2.5h! (13:30)"
    message: "SOC: {{ states('sensor.battery_state_of_charge') }}% | O 16:00 = 1.50 zl!" - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "⚠️🔋 OSTATNIA SZANSA (13:30)"
    message: "SOC: {{ states('sensor.battery_state_of_charge') }}%" - conditions: - condition: trigger
    id: prepeak_afternoon_summer - condition: numeric_state
    entity_id: sensor.battery_state_of_charge
    below: 70 - condition: template
    value_template: "{{ now().month in [4, 5, 6, 7, 8, 9] }}" - condition: or
    conditions: - condition: numeric_state
    entity_id: sensor.pv_forecast_remaining_today_total
    below: 3 - condition: numeric_state
    entity_id: sensor.ecowitt_solar_radiation_9747
    below: 100
    sequence: - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "18.5" - action: notify.mobile_app_iphone_henryk
    data:
    title: "☀️⚠️ Pre-Peak LATO: szczyt za 1h! (18:00)"
    message: "SOC: {{ states('sensor.battery_state_of_charge') }}% | O 19:00 = 1.50 zl!" - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "☀️⚠️ Pre-Peak LATO (18:00)"
    message: "SOC: {{ states('sensor.battery_state_of_charge') }}%" - conditions: - condition: trigger
    id: sudden_cloud_cover - condition: numeric_state
    entity_id: sensor.battery_state_of_charge
    below: 70 - condition: sun
    after: sunrise
    before: sunset - condition: template
    value_template: >
    {% set h = now().hour %}{% set m = now().month %}
    {% if m in [4,5,6,7,8,9] %}{{ h >= 13 and h < 19 }}{% else %}{{ h >= 13 and h < 16 }}{% endif %}
    sequence: - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "18.5" - action: notify.mobile_app_iphone_henryk
    data:
    title: "☁️ Zachmurzenie — priorytet bateria!"
    message: "Radiacja: {{ states('sensor.ecowitt_solar_radiation_9747') }} W/m2 | SOC: {{ states('sensor.battery_state_of_charge') }}%" - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "☁️ Zachmurzenie — priorytet bateria!"
    message: "SOC: {{ states('sensor.battery_state_of_charge') }}%" - conditions: - condition: trigger
    id: rain_started - condition: numeric_state
    entity_id: sensor.battery_state_of_charge
    below: 70 - condition: sun
    after: sunrise
    before: sunset - condition: template
    value_template: >
    {% set h = now().hour %}{% set m = now().month %}
    {% if m in [4,5,6,7,8,9] %}{{ h >= 13 and h < 19 }}{% else %}{{ h >= 13 and h < 16 }}{% endif %}
    sequence: - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "18.5" - action: notify.mobile_app_iphone_henryk
    data:
    title: "🌧️ Deszcz! Priorytet bateria!"
    message: "{{ states('sensor.ecowitt_rain_rate_9747') }} mm/h | SOC: {{ states('sensor.battery_state_of_charge') }}%" - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "🌧️ Deszcz! Priorytet bateria!"
    message: "SOC: {{ states('sensor.battery_state_of_charge') }}%"
    mode: queued
    max: 3

# ╔══════════════════════════════════════════════════════════════════════════╗

# ║ FORCE CHARGE / DISCHARGE v2 — eco_mode via mletenay (26.03.2026) ║

# ╚══════════════════════════════════════════════════════════════════════════╝

#

# eco_mode_power = 100% → pelna moc (~2-3 kW z sieci)

# eco_mode_soc = docelowy SOC

# eco_charge = ladowanie z sieci

# eco_discharge = rozladowanie do sieci

# 47509 = grid export enabled (musi byc 1 dla discharge!)

- id: hems_modbus_force_grid_charge
  alias: "HEMS: Force Grid Charge (eco_charge + power 100%)"
  description: "Wymusza ladowanie z sieci ~2-3 kW przez eco_charge z moca 100%."
  triggers:
  - trigger: state
    entity_id: input_boolean.hems_force_grid_charge
    to: "on"
    conditions: []
    actions:
  - action: number.set_value
    target:
    entity_id: number.goodwe_eco_mode_soc
    data:
    value: 100
  - action: number.set_value
    target:
    entity_id: number.goodwe_eco_mode_power
    data:
    value: 100
  - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "18.5"
  - action: select.select_option
    target:
    entity_id: select.goodwe_tryb_pracy_falownika
    data:
    option: "eco_charge"
  - action: notify.mobile_app_iphone_henryk
    data:
    title: "⚡🔋 Force Grid Charge START (eco 100%)"
    message: "eco_charge + power=100% | SOC: {{ states('sensor.battery_state_of_charge') }}%"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "⚡🔋 Force Grid Charge START"
    message: "SOC: {{ states('sensor.battery_state_of_charge') }}%"
  - wait_for_trigger:
    - trigger: numeric_state
      entity_id: sensor.battery_state_of_charge
      above: 95
    - trigger: state
      entity_id: input_boolean.hems_force_grid_charge
      to: "off"
      timeout: "06:00:00"
      continue_on_timeout: true
  - action: number.set_value
    target:
    entity_id: number.goodwe_eco_mode_power
    data:
    value: 0
  - action: select.select_option
    target:
    entity_id: select.goodwe_tryb_pracy_falownika
    data:
    option: "general"
  - action: input_boolean.turn_off
    target:
    entity_id: input_boolean.hems_force_grid_charge
  - action: notify.mobile_app_iphone_henryk
    data:
    title: "✅ Force Grid Charge STOP"
    message: "SOC: {{ states('sensor.battery_state_of_charge') }}% | Powrot do general"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "✅ Force Grid Charge STOP"
    message: "SOC: {{ states('sensor.battery_state_of_charge') }}%"
    mode: single

- id: hems_modbus_force_battery_discharge
  alias: "HEMS: Force Battery Discharge (eco_discharge + export ON)"
  description: "Wymusza rozladowanie baterii do sieci. Wlacza export (47509=1)."
  triggers:
  - trigger: state
    entity_id: input_boolean.hems_force_battery_discharge
    to: "on"
    conditions: []
    actions:
  - action: modbus.write_register
    data:
    hub: goodwe_rs485
    slave: 247
    address: 47509
    value: 1
  - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: grid_export_limit
    value: "16000"
  - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "0"
  - action: number.set_value
    target:
    entity_id: number.goodwe_eco_mode_soc
    data:
    value: 5
  - action: number.set_value
    target:
    entity_id: number.goodwe_eco_mode_power
    data:
    value: 100
  - action: select.select_option
    target:
    entity_id: select.goodwe_tryb_pracy_falownika
    data:
    option: "eco_discharge"
  - action: notify.mobile_app_iphone_henryk
    data:
    title: "🔋➡️🔌 Force Discharge START (eco_discharge)"
    message: "eco_discharge + power=100% + export ON | SOC: {{ states('sensor.battery_state_of_charge') }}%"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "🔋➡️🔌 Force Discharge START"
    message: "SOC: {{ states('sensor.battery_state_of_charge') }}%"
  - wait_for_trigger:
    - trigger: numeric_state
      entity_id: sensor.battery_state_of_charge
      below: 20
    - trigger: state
      entity_id: input_boolean.hems_force_battery_discharge
      to: "off"
      timeout: "06:00:00"
      continue_on_timeout: true
  - action: number.set_value
    target:
    entity_id: number.goodwe_eco_mode_power
    data:
    value: 0
  - action: number.set_value
    target:
    entity_id: number.goodwe_eco_mode_soc
    data:
    value: 100
  - action: select.select_option
    target:
    entity_id: select.goodwe_tryb_pracy_falownika
    data:
    option: "general"
  - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "18.5"
  - action: input_boolean.turn_off
    target:
    entity_id: input_boolean.hems_force_battery_discharge
  - action: notify.mobile_app_iphone_henryk
    data:
    title: "✅ Force Discharge STOP"
    message: "SOC: {{ states('sensor.battery_state_of_charge') }}% | Powrot do general"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "✅ Force Discharge STOP"
    message: "SOC: {{ states('sensor.battery_state_of_charge') }}%"
    mode: single

- id: hems_modbus_emergency_stop
  alias: "HEMS: Emergency STOP"
  description: "Awaryjne przywrocenie general + reset eco mode."
  triggers:
  - trigger: state
    entity_id: input_boolean.hems_modbus_emergency_stop
    to: "on"
    actions:
  - action: number.set_value
    target:
    entity_id: number.goodwe_eco_mode_power
    data:
    value: 0
  - action: number.set_value
    target:
    entity_id: number.goodwe_eco_mode_soc
    data:
    value: 100
  - action: modbus.write_register
    data:
    hub: goodwe_rs485
    slave: 247
    address: 47511
    value: 0
  - action: select.select_option
    target:
    entity_id: select.goodwe_tryb_pracy_falownika
    data:
    option: "general"
  - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "18.5"
  - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: grid_export_limit
    value: "16000"
  - action: input_boolean.turn_off
    target:
    entity_id: - input_boolean.hems_force_grid_charge - input_boolean.hems_force_battery_discharge - input_boolean.hems_modbus_emergency_stop
  - action: notify.mobile_app_iphone_henryk
    data:
    title: "🚨 EMERGENCY STOP!"
    message: "Przywrocono general + eco reset. SOC: {{ states('sensor.battery_state_of_charge') }}%"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "🚨 EMERGENCY STOP!"
    message: "SOC: {{ states('sensor.battery_state_of_charge') }}%"
    mode: single
