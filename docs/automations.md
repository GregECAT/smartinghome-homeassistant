- id: '1750763368204'
  alias: Harmonogram pracy boilera
  description: Automatyczne włączanie/wyłączanie oraz powiadomienia push na iPhone
  triggers:
  - at: 06:00:00
    trigger: time
  - at: 07:00:00
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
  - choose:
    - conditions:
      - condition: template
        value_template: '{{ now().strftime(''%H:%M'') in [''07:00'', ''12:00'', ''17:00''] }}'
        sequence:
      - action: switch.turn_on
        data: {}
        target:
        entity_id: switch.bojler_3800
      - action: notify.mobile_app_iphone_henryk
        data:
        title: "\U0001F525 Boiler WŁĄCZONY"
        message: "Boiler uruchomiony o {{ now().strftime('%H:%M') }}"
      - action: notify.mobile_app_iphone_grzegorz
        data:
        title: "\U0001F525 Boiler WŁĄCZONY"
        message: "Boiler uruchomiony o {{ now().strftime('%H:%M') }}"
    - conditions: - condition: template
      value_template: '{{ now().strftime(''%H:%M'') in [''06:00'', ''14:00'', ''17:00''] }}'
      sequence: - action: switch.turn_off
      data: {}
      target:
      entity_id: switch.bojler_3800 - action: notify.mobile_app_iphone_henryk
      data:
      title: "\U0001F4A7 Boiler WYŁĄCZONY"
      message: "Boiler wyłączony o {{ now().strftime('%H:%M') }}" - action: notify.mobile_app_iphone_grzegorz
      data:
      title: "\U0001F4A7 Boiler WYŁĄCZONY"
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
  - choose:
    - conditions: '{{ trigger.id == ''woda_wykryta'' }}'
      sequence:
      - target:
        entity_id: switch.pompa_zalania_socket_1
        action: switch.turn_on
    - conditions: '{{ trigger.id == ''sucho'' }}'
      sequence: - delay: 00:01:00 - target:
      entity_id: switch.pompa_zalania_socket_1
      action: switch.turn_off
      mode: single

# ============================================================================

# HEMS G13 + RCE v5 — GRID IMPORT GUARD + TARIFF-AWARE SOC

# ============================================================================

#

# ZMIANY vs v4:

# - NOWY: Grid Import Guard — blokuje ładowanie baterii z sieci w drogich godz.

# - NOWY: PV Surplus Smart Charge — ładuje baterię TYLKO z nadwyżki PV

# - NOWY: Emergency SOC < 5% — jedyny wyjątek od reguły "nie ładuj z sieci"

# - ZASTĄPIONY: hems_low_soc_protection → hems_smart_soc_protection (tariff-aware)

# - ZASTĄPIONY: hems_smart_soc_check_11 → hems_smart_soc_check_11_v2

# - ZASTĄPIONY: hems_smart_soc_check_12 → hems_smart_soc_check_12_v2

#

# 4 WARSTWY:

# W1 — G13 harmonogram: 07:00 sprzedaj → 13:00 ładuj → 16-21 szczyt

# W2 — RCE dynamiczna: binary_sensor okna + progi cenowe

# W3 — SOC bezpieczeństwo: tariff-aware + PV-only w drogich godzinach

# W4 — GRID IMPORT GUARD: nigdy nie importuj z sieci na baterię w szczycie

#

# ZASADA NADRZĘDNA:

# W drogich godzinach G13 (0.91 zł lub 1.50 zł) bateria ładuje się

# WYŁĄCZNIE z nadwyżki PV. Import z sieci na baterię jest ZABLOKOWANY.

#

# WYJĄTKI (gdy Guard NIE blokuje):

# 1. Emergency SOC < 5% (ochrona baterii przed shutdown)

# 2. RCE < 100 PLN/MWh → eksport bezwartościowy (< 0.12 zł/kWh sell),

# arbitraż opłacalny: kup 0.91 zł teraz → oszczędź 1.50 zł wieczorem

# = zysk 0.59 zł/kWh. Ładuj baterię zamiast eksportować za grosze!

# ============================================================================

# ╔══════════════════════════════════════════════════════════════════════════╗

# ║ W4: GRID IMPORT GUARD — NADRZĘDNA OCHRONA PRZED IMPORTEM ║

# ║ Działa jako safety net dla WSZYSTKICH automatyzacji ładowania ║

# ╚══════════════════════════════════════════════════════════════════════════╝

- id: hems_grid_import_guard
  alias: "HEMS: Grid Import Guard — STOP ładowania w drogich godzinach"
  description: >
  Wykrywa import z sieci podczas ładowania baterii w drogich godzinach G13.
  Blokuje ładowanie baterii — dom musi być zasilany z PV, nie z sieci!
  WYJĄTEK: gdy RCE < 100 PLN/MWh — eksport bezwartościowy, arbitraż opłacalny
  (kup 0.91 zł → oszczędź 1.50 zł wieczorem = zysk 0.59 zł/kWh).
  Trigger: meter_active_power_total > +100W przez 30 sekund.
  triggers:
  - trigger: numeric_state
    entity_id: sensor.meter_active_power_total
    above: 100
    for:
    seconds: 30
    conditions:
  - condition: time
    weekday:
    - mon
    - tue
    - wed
    - thu
    - fri

  # Tylko w drogich godzinach (szczyt przedpołudniowy LUB popołudniowy)
  - condition: template
    value_template: >
    {% set h = now().hour %}
    {% set m = now().month %}
    {% set przedpoludniowy = (h >= 7 and h < 13) %}
    {% set popoludniowy_zima = (m in [1,2,3,10,11,12]) and (h >= 16 and h < 21) %}
    {% set popoludniowy_lato = (m in [4,5,6,7,8,9]) and (h >= 19 and h < 22) %}
    {{ przedpoludniowy or popoludniowy_zima or popoludniowy_lato }}

  # Bateria aktualnie się ładuje (moc baterii > 100W = ładowanie)
  - condition: numeric_state
    entity_id: sensor.battery_power
    above: 100

  # WYJĄTEK RCE: NIE blokuj gdy RCE < 100 PLN/MWh

  # Bo wtedy eksport = bezwartościowy, a arbitraż opłacalny

  # (kup 0.91 zł przedpołudnie → oszczędź 1.50 zł wieczorem)
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
    title: "🛡️ Grid Guard: STOP ładowania baterii!"
    message: >
    Import z sieci: {{ states('sensor.meter_active_power_total') }} W
    w drogiej taryfie! Blokuję ładowanie baterii.
    PV: {{ states('sensor.pv_power') }} W | Dom: {{ states('sensor.load') }} W
    SOC: {{ states('sensor.battery_state_of_charge') }}%
    Bateria będzie się ładować TYLKO z nadwyżki PV.
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "🛡️ Grid Guard: STOP ładowania baterii!"
    message: >
    Import z sieci: {{ states('sensor.meter_active_power_total') }} W
    w drogiej taryfie! Blokuję ładowanie baterii.
    PV: {{ states('sensor.pv_power') }} W | Dom: {{ states('sensor.load') }} W
    SOC: {{ states('sensor.battery_state_of_charge') }}%
    Bateria będzie się ładować TYLKO z nadwyżki PV.
    mode: single

- id: hems_pv_surplus_smart_charge
  alias: "HEMS: PV Surplus → ładuj baterię (drogie godziny)"
  description: >
  Wykrywa nadwyżkę PV (eksport do sieci > 300W) w drogich godzinach G13
  i włącza ładowanie baterii. Histereza: włącz przy -300W, wyłącz przy +100W.
  Pozwala ładować baterię BEZ importu z sieci. Guard czuwa nad importem.
  triggers:
  - trigger: numeric_state
    entity_id: sensor.meter_active_power_total
    below: -300
    for:
    seconds: 60
    conditions:
  - condition: time
    weekday:
    - mon
    - tue
    - wed
    - thu
    - fri
  # Tylko w drogich godzinach
  - condition: template
    value_template: >
    {% set h = now().hour %}
    {% set m = now().month %}
    {% set przedpoludniowy = (h >= 7 and h < 13) %}
    {% set popoludniowy_zima = (m in [1,2,3,10,11,12]) and (h >= 16 and h < 21) %}
    {% set popoludniowy_lato = (m in [4,5,6,7,8,9]) and (h >= 19 and h < 22) %}
    {{ przedpoludniowy or popoludniowy_zima or popoludniowy_lato }}
  # Bateria potrzebuje ładowania
  - condition: numeric_state
    entity_id: sensor.battery_state_of_charge
    below: 95
  # PV produkuje
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
    title: "☀️🔋 PV Surplus → ładuję baterię"
    message: >
    Nadwyżka PV: {{ states('sensor.meter_active_power_total') | float | abs | round(0) }} W eksportu
    PV: {{ states('sensor.pv_power') }} W | Dom: {{ states('sensor.load') }} W
    SOC: {{ states('sensor.battery_state_of_charge') }}%
    Ładuję baterię z nadwyżki (Guard czuwa nad importem!)
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "☀️🔋 PV Surplus → ładuję baterię"
    message: >
    Nadwyżka PV: {{ states('sensor.meter_active_power_total') | float | abs | round(0) }} W eksportu
    PV: {{ states('sensor.pv_power') }} W | Dom: {{ states('sensor.load') }} W
    SOC: {{ states('sensor.battery_state_of_charge') }}%
    Ładuję baterię z nadwyżki (Guard czuwa nad importem!)
    mode: single

# ╔══════════════════════════════════════════════════════════════════════════╗

# ║ W1: G13 HARMONOGRAM DOBOWY ║

# ╚══════════════════════════════════════════════════════════════════════════╝

- id: hems_morning_sell_mode
  alias: "HEMS: Rano — sprzedaż lub arbitraż (7:00)"
  description: >
  G13 szczyt poranny (0.91 zł). Sprzedawaj 7-13 gdy RCE opłacalna.
  ALE: gdy RCE < 100 PLN/MWh → eksport bezwartościowy, ładuj baterię
  (arbitraż: kup 0.91 → oszczędź 1.50 wieczorem).
  triggers:
  - trigger: time
    at: "07:00:00"
    conditions:
  - condition: time
    weekday:
    - mon
    - tue
    - wed
    - thu
    - fri
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

  # Decyzja: sprzedaż vs arbitraż zależnie od RCE
  - choose: - conditions: - condition: numeric_state
    entity_id: sensor.rce_pse_cena
    below: 100
    sequence: # RCE ultra-niska → ładuj baterię (arbitraż opłacalny) - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "18.5" - action: notify.mobile_app_iphone_henryk
    data:
    title: "⚡🔋 G13 7:00: RCE niska → ARBITRAŻ (nie sprzedaż!)"
    message: >
    RCE: {{ states('sensor.rce_sell_price') }} zł (bezwartościowa!)
    Ładuję baterię: kup 0.91 → oszczędź 1.50 wieczorem = +0.59 zł/kWh
    SOC: {{ states('sensor.battery_state_of_charge') }}% - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "⚡🔋 G13 7:00: RCE niska → ARBITRAŻ (nie sprzedaż!)"
    message: >
    RCE: {{ states('sensor.rce_sell_price') }} zł (bezwartościowa!)
    Ładuję baterię: kup 0.91 → oszczędź 1.50 wieczorem = +0.59 zł/kWh
    SOC: {{ states('sensor.battery_state_of_charge') }}%
    default: # RCE normalna → sprzedawaj jak dotychczas - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "0" - action: notify.mobile_app_iphone_henryk
    data:
    title: "☀️ G13: Sprzedaż (0.91 zł)"
    message: "RCE: {{ states('sensor.rce_sell_price') }} zł | SOC: {{ states('sensor.battery_state_of_charge') }}%" - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "☀️ G13: Sprzedaż (0.91 zł)"
    message: "RCE: {{ states('sensor.rce_sell_price') }} zł | SOC: {{ states('sensor.battery_state_of_charge') }}%"
    mode: single

- id: hems_midday_charge_mode
  alias: "HEMS: Off-peak — ładowanie baterii (13:00)"
  triggers:
  - trigger: time
    at: "13:00:00"
    conditions:
  - condition: time
    weekday:
    - mon
    - tue
    - wed
    - thu
    - fri
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
    title: "🔋 G13: Ładowanie (0.63 zł)"
    message: "RCE: {{ states('sensor.rce_sell_price') }} zł | SOC: {{ states('sensor.battery_state_of_charge') }}%"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "🔋 G13: Ładowanie (0.63 zł)"
    message: "RCE: {{ states('sensor.rce_sell_price') }} zł | SOC: {{ states('sensor.battery_state_of_charge') }}%"
    mode: single

- id: hems_evening_peak_start
  alias: "HEMS: Szczyt wieczorny — bateria zasila dom"
  triggers:
  - trigger: template
    value_template: "{{ states('sensor.g13_is_afternoon_peak') == 'on' }}"
    conditions:
  - condition: time
    weekday:
    - mon
    - tue
    - wed
    - thu
    - fri
      actions:
  - action: notify.mobile_app_iphone_henryk
    data:
    title: "💰 SZCZYT G13 (1.50 zł) + RCE {{ states('sensor.rce_sell_price') }} zł"
    message: "SOC: {{ states('sensor.battery_state_of_charge') }}%"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "💰 SZCZYT G13 (1.50 zł) + RCE {{ states('sensor.rce_sell_price') }} zł"
    message: "SOC: {{ states('sensor.battery_state_of_charge') }}%"
    mode: single

- id: hems_weekend_mode
  alias: "HEMS: Weekend — autokonsumpcja"
  triggers:
  - trigger: time
    at: "07:00:00"
    conditions:
  - condition: time
    weekday:
    - sat
    - sun
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
    message: "RCE: {{ states('sensor.rce_sell_price') }} zł | Najtaniej: {{ states('sensor.rce_pse_najtansze_okno_czasowe_dzisiaj') }}"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "🏖️ Weekend — off-peak"
    message: "RCE: {{ states('sensor.rce_sell_price') }} zł | Najtaniej: {{ states('sensor.rce_pse_najtansze_okno_czasowe_dzisiaj') }}"
    mode: single

# ╔══════════════════════════════════════════════════════════════════════════╗

# ║ W2: RCE DYNAMICZNA — okna cenowe + progi ║

# ╚══════════════════════════════════════════════════════════════════════════╝

- id: hems_rce_cheapest_window_charge
  alias: "HEMS RCE: Najtańsze okno → ładuj baterię"
  description: "Binary sensor PSE: najtańsze okno aktywne. Ładuj baterię — najniższe ceny na rynku."
  triggers:
  - trigger: state
    entity_id: binary_sensor.rce_pse_aktywne_najtansze_okno_dzisiaj
    to: "on"
    conditions:
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
    title: "🟢 RCE: NAJTAŃSZE OKNO — ładuję baterię"
    message: "RCE: {{ states('sensor.rce_sell_price') }} zł | Okno: {{ states('sensor.rce_pse_najtansze_okno_czasowe_dzisiaj') }} | SOC: {{ states('sensor.battery_state_of_charge') }}%"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "🟢 RCE: NAJTAŃSZE OKNO — ładuję baterię"
    message: "RCE: {{ states('sensor.rce_sell_price') }} zł | Okno: {{ states('sensor.rce_pse_najtansze_okno_czasowe_dzisiaj') }} | SOC: {{ states('sensor.battery_state_of_charge') }}%"
    mode: single

- id: hems_rce_cheapest_window_end
  alias: "HEMS RCE: Koniec najtańszego okna → wróć do sprzedaży"
  description: >
  Najtańsze okno się skończyło. Wróć do sprzedaży jeśli przed 13:00.
  ALE: nie wracaj do sprzedaży jeśli RCE nadal < 100 PLN/MWh —
  eksport bezwartościowy, lepiej ładować baterię!
  triggers:
  - trigger: state
    entity_id: binary_sensor.rce_pse_aktywne_najtansze_okno_dzisiaj
    to: "off"
    conditions:
  - condition: template
    value_template: "{{ now().hour >= 7 and now().hour < 13 }}"
  - condition: time
    weekday:
    - mon
    - tue
    - wed
    - thu
    - fri

  # NIE wracaj do sprzedaży jeśli RCE nadal ultra niska
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
  alias: "HEMS RCE: Najdroższe okno → max oszczędność"
  description: "Binary sensor PSE: najdroższe okno aktywne. Bateria zasila dom, nie kupuj z sieci."
  triggers:
  - trigger: state
    entity_id: binary_sensor.rce_pse_aktywne_najdrozsze_okno_dzisiaj
    to: "on"
    actions:
  - action: notify.mobile_app_iphone_henryk
    data:
    title: "🔴 RCE: NAJDROŻSZE OKNO!"
    message: "RCE: {{ states('sensor.rce_sell_price') }} zł | G13: {{ states('sensor.g13_buy_price') }} zł | Okno: {{ states('sensor.rce_pse_najdrozsze_okno_czasowe_dzisiaj') }} | SOC: {{ states('sensor.battery_state_of_charge') }}% | Bateria zasila dom!"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "🔴 RCE: NAJDROŻSZE OKNO!"
    message: "RCE: {{ states('sensor.rce_sell_price') }} zł | G13: {{ states('sensor.g13_buy_price') }} zł | Okno: {{ states('sensor.rce_pse_najdrozsze_okno_czasowe_dzisiaj') }} | SOC: {{ states('sensor.battery_state_of_charge') }}% | Bateria zasila dom!"
    mode: single

- id: hems_rce_low_price_charge
  alias: "HEMS RCE: Niska cena → ładuj baterię"
  description: "RCE < 150 PLN/MWh — nie opłaca się sprzedawać."
  triggers:
  - trigger: numeric_state
    entity_id: sensor.rce_pse_cena
    below: 150
    for:
    minutes: 2
    conditions:
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
    title: "📉 RCE niska ({{ states('sensor.rce_sell_price') }} zł) → ładuję"
    message: "Trend: {{ states('sensor.rce_price_trend') }}"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "📉 RCE niska ({{ states('sensor.rce_sell_price') }} zł) → ładuję"
    message: "Trend: {{ states('sensor.rce_price_trend') }}"
    mode: single

- id: hems_rce_price_restored_sell
  alias: "HEMS RCE: Cena wzrosła → wróć do sprzedaży"
  description: "RCE > 300 PLN/MWh — opłaca się sprzedawać."
  triggers:
  - trigger: numeric_state
    entity_id: sensor.rce_pse_cena
    above: 300
    for:
    minutes: 2
    conditions:
  - condition: template
    value_template: "{{ now().hour >= 7 and now().hour < 13 }}"
  - condition: time
    weekday:
    - mon
    - tue
    - wed
    - thu
    - fri
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
    title: "📈 RCE wzrosła ({{ states('sensor.rce_sell_price') }} zł) → sprzedaję"
    message: "Trend: {{ states('sensor.rce_price_trend') }}"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "📈 RCE wzrosła ({{ states('sensor.rce_sell_price') }} zł) → sprzedaję"
    message: "Trend: {{ states('sensor.rce_price_trend') }}"
    mode: single

- id: hems_rce_high_price_evening
  alias: "HEMS RCE: Wysoka cena wieczorem"
  description: "RCE > 500 PLN/MWh wieczorem + G13 szczyt."
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
    message: "RCE: {{ states('sensor.rce_sell_price') }} zł + G13: 1.50 zł | SOC: {{ states('sensor.battery_state_of_charge') }}% | Max oszczędność!"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "💰💰 RCE PEAK + G13 SZCZYT!"
    message: "RCE: {{ states('sensor.rce_sell_price') }} zł + G13: 1.50 zł | SOC: {{ states('sensor.battery_state_of_charge') }}% | Max oszczędność!"
    mode: single

- id: hems_rce_negative_price
  alias: "HEMS RCE: Cena ultra-niska / ujemna → ładuj + bojler"
  description: >
  RCE < 50 PLN/MWh (sell price < 0.06 zł) — eksport bezwartościowy!
  Ładuj baterię + bojler. Przy ujemnej cenie: dosłownie darmowa energia.
  Przy zerowej/niskiej: arbitraż opłacalny (kup 0.91 → oszczędź 1.50).
  triggers:
  - trigger: numeric_state
    entity_id: sensor.rce_pse_cena
    below: 50
    for:
    minutes: 1
    conditions:
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
    title: >
    {{ '🤑 RCE UJEMNA!' if states('sensor.rce_pse_cena') | float(0) < 0
           else '⚡ RCE ultra-niska!' }} ({{ states('sensor.rce_pse_cena') }} PLN/MWh)
    message: >
    Eksport bezwartościowy ({{ states('sensor.rce_sell_price') }} zł/kWh)!
    Ładuję baterię na max + bojler ON.
    SOC: {{ states('sensor.battery_state_of_charge') }}%
    Arbitraż: kup 0.91 → oszczędź 1.50 wieczorem = zysk 0.59 zł/kWh!
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: >
    {{ '🤑 RCE UJEMNA!' if states('sensor.rce_pse_cena') | float(0) < 0
             else '⚡ RCE ultra-niska!' }} ({{ states('sensor.rce_pse_cena') }} PLN/MWh)
    message: >
    Eksport bezwartościowy ({{ states('sensor.rce_sell_price') }} zł/kWh)!
    Ładuję baterię na max + bojler ON.
    SOC: {{ states('sensor.battery_state_of_charge') }}%
    Arbitraż: kup 0.91 → oszczędź 1.50 wieczorem = zysk 0.59 zł/kWh!
    mode: single

# ╔══════════════════════════════════════════════════════════════════════════╗

# ║ W3: SOC BEZPIECZEŃSTWO — TARIFF-AWARE (v2) ║

# ║ Zastępuje stare: hems_low_soc_protection, \_check_11, \_check_12 ║

# ╚══════════════════════════════════════════════════════════════════════════╝

- id: hems_smart_soc_protection
  alias: "HEMS v2: Smart SOC Protection (tariff-aware)"
  description: >
  Ochrona SOC z uwzględnieniem taryfy G13:
  - DROGIE godziny (szczyt): charge_current=0, pozwól tylko na PV surplus charge
  - TANIE godziny (off-peak/weekend): charge_current=18.5, ładuj normalnie
    Zastępuje starą hems_low_soc_protection!
    triggers:
  - trigger: numeric_state
    entity_id: sensor.battery_state_of_charge
    below: 20
    for:
    minutes: 3
    actions:
  - choose: # ── DROGIE GODZINY: NIE ładuj z sieci! ── - conditions: - condition: time
    weekday: - mon - tue - wed - thu - fri - condition: template
    value_template: >
    {% set h = now().hour %}
    {% set m = now().month %}
    {% set przedpoludniowy = (h >= 7 and h < 13) %}
    {% set popoludniowy_zima = (m in [1,2,3,10,11,12]) and (h >= 16 and h < 21) %}
    {% set popoludniowy_lato = (m in [4,5,6,7,8,9]) and (h >= 19 and h < 22) %}
    {{ przedpoludniowy or popoludniowy_zima or popoludniowy_lato }}
    sequence: - action: notify.mobile_app_iphone_henryk
    data:
    title: "🔋⚠️ SOC {{ states('sensor.battery_state_of_charge') }}% — DROGA TARYFA!"
    message: >
    SOC krytycznie niski, ALE trwa drogi szczyt!
    NIE ładuję z sieci ({{
                      '0.91' if now().hour >= 7 and now().hour < 13
                      else '1.50' }} zł/kWh).
    Bateria ładuje się TYLKO z nadwyżki PV.
    PV: {{ states('sensor.pv_power') }} W | Dom: {{ states('sensor.load') }} W - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "🔋⚠️ SOC {{ states('sensor.battery_state_of_charge') }}% — DROGA TARYFA!"
    message: >
    SOC krytycznie niski, ALE trwa drogi szczyt!
    NIE ładuję z sieci ({{
                      '0.91' if now().hour >= 7 and now().hour < 13
                      else '1.50' }} zł/kWh).
    Bateria ładuje się TYLKO z nadwyżki PV.
    PV: {{ states('sensor.pv_power') }} W | Dom: {{ states('sensor.load') }} W # ── TANIE GODZINY: Ładuj normalnie ──
    default: - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "18.5" - action: notify.mobile_app_iphone_henryk
    data:
    title: "🔋 Ochrona SOC — ładuję (tania taryfa)"
    message: >
    SOC: {{ states('sensor.battery_state_of_charge') }}%
    Taryfa tania (0.63 zł) — ładuję baterię z sieci + PV. - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "🔋 Ochrona SOC — ładuję (tania taryfa)"
    message: >
    SOC: {{ states('sensor.battery_state_of_charge') }}%
    Taryfa tania (0.63 zł) — ładuję baterię z sieci + PV.
    mode: single

- id: hems_emergency_soc_critical
  alias: "HEMS v2: EMERGENCY SOC < 5% — ładuj awaryjnie!"
  description: >
  SOC spadł poniżej 5% — blisko shutdown! Priorytet = przetrwanie baterii.
  Ładuj niezależnie od taryfy do 15%, potem wróć do PV-only w szczycie.
  triggers:
  - trigger: numeric_state
    entity_id: sensor.battery_state_of_charge
    below: 5
    for:
    seconds: 30
    actions:
  - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "18.5"
  - action: notify.mobile_app_iphone_henryk
    data:
    title: "🚨🔋 EMERGENCY: SOC {{ states('sensor.battery_state_of_charge') }}%!"
    message: >
    KRYTYCZNIE NISKI SOC! Ładuję awaryjnie niezależnie od taryfy.
    Bateria bliska shutdown (5%). Jednorazowe ładowanie ratunkowe.
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "🚨🔋 EMERGENCY: SOC {{ states('sensor.battery_state_of_charge') }}%!"
    message: >
    KRYTYCZNIE NISKI SOC! Ładuję awaryjnie niezależnie od taryfy.
    Bateria bliska shutdown (5%). Jednorazowe ładowanie ratunkowe.

  # Ładuj awaryjnie do 15% i STOP
  - wait_for_trigger:
    - trigger: numeric_state
      entity_id: sensor.battery_state_of_charge
      above: 15
      timeout: "01:00:00"
      continue_on_timeout: true

  # Po 15% sprawdź czy drogie godziny → wróć do PV-only
  - choose: - conditions: - condition: template
    value_template: >
    {% set h = now().hour %}
    {% set m = now().month %}
    {% set wd = now().weekday() %}
    {% set drogie = false %}
    {% if wd < 5 %}
    {% set drogie = (h >= 7 and h < 13) or
                      (m in [1,2,3,10,11,12] and h >= 16 and h < 21) or
                      (m in [4,5,6,7,8,9] and h >= 19 and h < 22) %}
    {% endif %}
    {{ drogie }}
    sequence: - action: goodwe.set_parameter
    data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "0" - action: notify.mobile_app_iphone_henryk
    data:
    title: "🔋 Emergency STOP — SOC {{ states('sensor.battery_state_of_charge') }}%"
    message: "Osiągnięto 15%. Drogi szczyt — wracam do PV-only charge." - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "🔋 Emergency STOP — SOC {{ states('sensor.battery_state_of_charge') }}%"
    message: "Osiągnięto 15%. Drogi szczyt — wracam do PV-only charge."
    mode: single

- id: hems_smart_soc_check_11_v2
  alias: "HEMS v2: SOC check 11:00 — PV-only w szczycie"
  description: >
  SOC < 50% o 11:00. Sprawdza nadwyżkę PV przed włączeniem ładowania.
  W drogim szczycie: NIE ładuj z sieci, tylko z nadwyżki PV.
  triggers:
  - trigger: time
    at: "11:00:00"
    conditions:
  - condition: numeric_state
    entity_id: sensor.battery_state_of_charge
    below: 50
  - condition: time
    weekday: - mon - tue - wed - thu - fri
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
    title: "⚠️🔋 SOC {{ states('sensor.battery_state_of_charge') }}% — ładuję z PV"
    message: >
    SOC niski, ale mamy nadwyżkę PV ({{ states('sensor.meter_active_power_total') }} W).
    Ładuję baterię z PV. Grid Guard czuwa nad importem! - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "⚠️🔋 SOC {{ states('sensor.battery_state_of_charge') }}% — ładuję z PV"
    message: >
    SOC niski, ale mamy nadwyżkę PV ({{ states('sensor.meter_active_power_total') }} W).
    Ładuję baterię z PV. Grid Guard czuwa nad importem!
    default: - action: notify.mobile_app_iphone_henryk
    data:
    title: "⚠️🔋 SOC {{ states('sensor.battery_state_of_charge') }}% — brak nadwyżki PV!"
    message: >
    Brak nadwyżki PV ({{ states('sensor.meter_active_power_total') }} W import).
    Drogi szczyt — NIE ładuję z sieci. Czekam na nadwyżkę PV.
    PV: {{ states('sensor.pv_power') }} W | Dom: {{ states('sensor.load') }} W - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "⚠️🔋 SOC {{ states('sensor.battery_state_of_charge') }}% — brak nadwyżki PV!"
    message: >
    Brak nadwyżki PV ({{ states('sensor.meter_active_power_total') }} W import).
    Drogi szczyt — NIE ładuję z sieci. Czekam na nadwyżkę PV.
    PV: {{ states('sensor.pv_power') }} W | Dom: {{ states('sensor.load') }} W
    mode: single

- id: hems_smart_soc_check_12_v2
  alias: "HEMS v2: SOC check 12:00 — ostatnia szansa PV w szczycie"
  description: >
  SOC < 70% o 12:00. Za godzinę off-peak (13:00).
  Jeśli nadwyżka PV → ładuj. Jeśli nie → poczekaj na 13:00 (tania taryfa).
  triggers:
  - trigger: time
    at: "12:00:00"
    conditions:
  - condition: numeric_state
    entity_id: sensor.battery_state_of_charge
    below: 70
  - condition: time
    weekday: - mon - tue - wed - thu - fri
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
    title: "⚠️ SOC {{ states('sensor.battery_state_of_charge') }}% — ładuję z nadwyżki PV"
    message: "Za godzinę off-peak. Nadwyżka PV → bateria. Guard czuwa!" - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "⚠️ SOC {{ states('sensor.battery_state_of_charge') }}% — ładuję z nadwyżki PV"
    message: "Za godzinę off-peak. Nadwyżka PV → bateria. Guard czuwa!"
    default: - action: notify.mobile_app_iphone_henryk
    data:
    title: "⚠️ SOC {{ states('sensor.battery_state_of_charge') }}% — czekam na 13:00"
    message: >
    Brak nadwyżki PV. Za godzinę off-peak (0.63 zł) — wtedy naładuję.
    PV: {{ states('sensor.pv_power') }} W | Dom: {{ states('sensor.load') }} W - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "⚠️ SOC {{ states('sensor.battery_state_of_charge') }}% — czekam na 13:00"
    message: >
    Brak nadwyżki PV. Za godzinę off-peak (0.63 zł) — wtedy naładuję.
    PV: {{ states('sensor.pv_power') }} W | Dom: {{ states('sensor.load') }} W
    mode: single

# ╔══════════════════════════════════════════════════════════════════════════╗

# ║ ARBITRAŻ NOCNY ║

# ╚══════════════════════════════════════════════════════════════════════════╝

- id: hems_night_grid_charge
  alias: "HEMS: Nocne ładowanie z sieci (tania taryfa)"
  triggers:
  - trigger: time
    at: "23:00:00"
    conditions:
  - condition: numeric_state
    entity_id: sensor.pv_forecast_tomorrow_total
    below: 8
  - condition: numeric_state
    entity_id: sensor.battery_state_of_charge
    below: 50
  - condition: time
    weekday:
    - sun
    - mon
    - tue
    - wed
    - thu
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
    title: "🌙 Nocny arbitraż (0.63 → 1.50 zł)"
    message: "Prognoza: {{ states('sensor.pv_forecast_tomorrow_total') }} kWh | SOC: {{ states('sensor.battery_state_of_charge') }}%"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "🌙 Nocny arbitraż (0.63 → 1.50 zł)"
    message: "Prognoza: {{ states('sensor.pv_forecast_tomorrow_total') }} kWh | SOC: {{ states('sensor.battery_state_of_charge') }}%"
    mode: single

- id: hems_night_charge_stop
  alias: "HEMS: Stop ładowania nocnego"
  triggers:
  - trigger: numeric_state
    entity_id: sensor.battery_state_of_charge
    above: 90
  - trigger: time
    at: "06:00:00"
    conditions:
  - condition: state
    entity_id: select.goodwe_tryb_pracy_falownika
    state: "eco_charge"
    actions:
  - action: select.select_option
    target:
    entity_id: select.goodwe_tryb_pracy_falownika
    data:
    option: "general"
    mode: single

# ╔══════════════════════════════════════════════════════════════════════════╗

# ║ KASKADA NAPIĘCIA — ochrona PV przed odcięciem ║

# ╚══════════════════════════════════════════════════════════════════════════╝

- id: hems_voltage_high_bojler_on
  alias: "HEMS: Wysokie napięcie → Bojler ON"
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
    title: "⚡ Wysokie napięcie → Bojler ON"
    message: "L1:{{ states('sensor.on_grid_l1_voltage') }}V L2:{{ states('sensor.on_grid_l2_voltage') }}V L3:{{ states('sensor.on_grid_l3_voltage') }}V"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "⚡ Wysokie napięcie → Bojler ON"
    message: "L1:{{ states('sensor.on_grid_l1_voltage') }}V L2:{{ states('sensor.on_grid_l2_voltage') }}V L3:{{ states('sensor.on_grid_l3_voltage') }}V"
    mode: single

- id: hems_voltage_high_klima_on
  alias: "HEMS: Bardzo wysokie napięcie → Klima ON"
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
    title: "⚡⚡ Bardzo wysokie napięcie → Klima ON"
    message: "L1:{{ states('sensor.on_grid_l1_voltage') }}V L2:{{ states('sensor.on_grid_l2_voltage') }}V L3:{{ states('sensor.on_grid_l3_voltage') }}V"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "⚡⚡ Bardzo wysokie napięcie → Klima ON"
    message: "L1:{{ states('sensor.on_grid_l1_voltage') }}V L2:{{ states('sensor.on_grid_l2_voltage') }}V L3:{{ states('sensor.on_grid_l3_voltage') }}V"
    mode: single

- id: hems_voltage_critical_charge_battery
  alias: "HEMS: Krytyczne napięcie → Ładuj baterię"
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
    title: "🔴 Krytyczne napięcie → Ładuj baterię!"
    message: "L1:{{ states('sensor.on_grid_l1_voltage') }}V"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "🔴 Krytyczne napięcie → Ładuj baterię!"
    message: "L1:{{ states('sensor.on_grid_l1_voltage') }}V"
    mode: single

- id: hems_voltage_normal_restore
  alias: "HEMS: Napięcie normalne → Przywróć tryb"
  triggers:
  - trigger: template
    value_template: >
    {{ states('sensor.on_grid_l1_voltage') | float(0) < 248
           and states('sensor.on_grid_l2_voltage') | float(0) < 248
           and states('sensor.on_grid_l3_voltage') | float(0) < 248 }}
    for:
    minutes: 5
    conditions:
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
  - choose:
    - conditions: - condition: time
      before: "13:00:00" - condition: numeric_state
      entity_id: sensor.rce_pse_cena
      above: 200 - condition: time
      weekday: - mon - tue - wed - thu - fri
      sequence: - action: goodwe.set_parameter
      data:
      device_id: 02592f41265ac022d0c8b8aa99728b3e
      parameter: battery_charge_current
      value: "0"
      mode: single

# ╔══════════════════════════════════════════════════════════════════════════╗

# ║ BEZPIECZEŃSTWO — prognoza, DOD, warning, awaryjne OFF ║

# ╚══════════════════════════════════════════════════════════════════════════╝

- id: hems_weak_forecast_protect
  alias: "HEMS: Słaba prognoza — zachowaj baterię"
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
    title: "🌧️ Słaba prognoza"
    message: "Jutro: {{ states('sensor.pv_forecast_tomorrow_total') }} kWh | DOD → 70%"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "🌧️ Słaba prognoza"
    message: "Jutro: {{ states('sensor.pv_forecast_tomorrow_total') }} kWh | DOD → 70%"
    mode: single

- id: hems_restore_dod_sunrise
  alias: "HEMS: Przywróć DOD po wschodzie"
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

# ╔══════════════════════════════════════════════════════════════════════════╗

# ║ KASKADA NADWYŻKI PV — bojler → klima → gniazdko ║

# ╚══════════════════════════════════════════════════════════════════════════╝

- id: hems_surplus_bojler_on
  alias: "HEMS: Nadwyżka → Bojler (L1)"
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
  alias: "HEMS: Bojler OFF (brak nadwyżki)"
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
  alias: "HEMS: Nadwyżka → Klima (L2)"
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
  alias: "HEMS: Nadwyżka → Gniazdko 2 (L3)"
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
    conditions:
    - condition: state
      entity_id: switch.bojler_3800
      state: "on"
    - condition: state
      entity_id: switch.klimatyzacja_socket_1
      state: "on"
    - condition: state
      entity_id: switch.drugie_gniazdko
      state: "on"
      actions:
  - action: switch.turn_off
    target:
    entity_id:
    - switch.drugie_gniazdko
    - switch.klimatyzacja_socket_1
    - switch.bojler_3800
  - action: notify.mobile_app_iphone_henryk
    data:
    title: "🚨 Awaryjne OFF"
    message: "SOC {{ states('sensor.battery_state_of_charge') }}%"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "🚨 Awaryjne OFF"
    message: "SOC {{ states('sensor.battery_state_of_charge') }}%"
    mode: single

# ╔══════════════════════════════════════════════════════════════════════════╗

# ║ RAPORT DOBOWY + ALERTY ║

# ╚══════════════════════════════════════════════════════════════════════════╝

- id: hems_daily_report
  alias: "HEMS: Raport dobowy"
  triggers:
  - trigger: time
    at: "21:00:00"
    actions:
  - action: notify.mobile_app_iphone_henryk
    data:
    title: "📊 Raport G13+RCE"
    message: "☀️PV:{{ states('sensor.today_s_pv_generation') }}kWh 🏠Dom:{{ states('sensor.today_load') }}kWh 📤Export:{{ states('sensor.today_energy_export') }}kWh 📥Import:{{ states('sensor.today_energy_import') }}kWh 🔋SOC:{{ states('sensor.battery_state_of_charge') }}% 💰Bilans:{{ states('sensor.g13_net_balance_today') }}zł RCE avg:{{ states('sensor.rce_average_today') }}zł 🌤️Jutro:{{ states('sensor.pv_forecast_tomorrow_total') }}kWh"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "📊 Raport G13+RCE"
    message: "☀️PV:{{ states('sensor.today_s_pv_generation') }}kWh 🏠Dom:{{ states('sensor.today_load') }}kWh 📤Export:{{ states('sensor.today_energy_export') }}kWh 📥Import:{{ states('sensor.today_energy_import') }}kWh 🔋SOC:{{ states('sensor.battery_state_of_charge') }}% 💰Bilans:{{ states('sensor.g13_net_balance_today') }}zł RCE avg:{{ states('sensor.rce_average_today') }}zł 🌤️Jutro:{{ states('sensor.pv_forecast_tomorrow_total') }}kWh"
    mode: single

- id: hems_phase_imbalance
  alias: "HEMS: Alert — nierównowaga faz"
  triggers:
  - trigger: numeric_state
    entity_id: sensor.goodwe_load_balance_difference
    above: 3000
    for:
    minutes: 10
    actions:
  - action: notify.mobile_app_iphone_henryk
    data:
    title: "⚖️ Nierównowaga faz!"
    message: "L1:{{ states('sensor.load_l1') }}W L2:{{ states('sensor.load_l2') }}W L3:{{ states('sensor.load_l3') }}W"
  - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "⚖️ Nierównowaga faz!"
    message: "L1:{{ states('sensor.load_l1') }}W L2:{{ states('sensor.load_l2') }}W L3:{{ states('sensor.load_l3') }}W"
    mode: single

# ╔══════════════════════════════════════════════════════════════════════════╗

# ║ SMART PRE-PEAK CHARGE — Ecowitt Weather + PV Forecast ║

# ╚══════════════════════════════════════════════════════════════════════════╝

- id: hems_smart_precharge_weather
  alias: "HEMS: Smart Pre-Peak Charge — pogoda Ecowitt + prognoza PV"
  description: >
  Inteligentne ładowanie baterii PRZED szczytami G13, bazujące na
  lokalnych danych pogodowych Ecowitt WH90 i prognozie Forecast.Solar.
  4 punkty kontrolne: 05:30, 10:00, 13:30, 18:00.
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
  - condition: time
    weekday: - mon - tue - wed - thu - fri
    actions:
  - choose: # ── PUNKT 1 — 05:30 PORANNY CHECK ── - conditions: - condition: trigger
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
    title: "🌧️⚡ Smart Pre-Peak: Ładuję z sieci (05:30)"
    message: >
    Prognoza PV: {{ states('sensor.pv_forecast_today_total') }} kWh (słaba!)
    SOC: {{ states('sensor.battery_state_of_charge') }}%
    Ładuję z sieci po 0.63 zł do 07:00 (potem 0.91 zł) - action: notify.mobile_app_iphone_grzegorz
    data:
    title: "🌧️⚡ Smart Pre-Peak: Ładuję z sieci (05:30)"
    message: >
    Prognoza PV: {{ states('sensor.pv_forecast_today_total') }} kWh (słaba!)
    SOC: {{ states('sensor.battery_state_of_charge') }}%
    Ładuję z sieci po 0.63 zł do 07:00 (potem 0.91 zł) - delay: "01:25:00" - action: select.select_option
    target:
    entity_id: select.goodwe_tryb_pracy_falownika
    data:
    option: "general"

          # ── PUNKT 2 — 10:00 WERYFIKACJA ECOWITT ──
          - conditions:
              - condition: trigger
                id: midmorning_verify
              - condition: numeric_state
                entity_id: sensor.battery_state_of_charge
                below: 60
              - condition: or
                conditions:
                  - condition: numeric_state
                    entity_id: sensor.ecowitt_solar_radiation_9747
                    below: 200
                  - condition: numeric_state
                    entity_id: sensor.ecowitt_rain_rate_9747
                    above: 0
                  - condition: template
                    value_template: >
                      {{ states('sensor.ecowitt_uv_index_9747') | int(0) == 0 }}
            sequence:
              - action: goodwe.set_parameter
                data:
                  device_id: 02592f41265ac022d0c8b8aa99728b3e
                  parameter: battery_charge_current
                  value: "18.5"
              - action: notify.mobile_app_iphone_henryk
                data:
                  title: "🌥️ Smart Pre-Peak: Pogoda słaba — priorytet bateria (10:00)"
                  message: >
                    ☀️ Radiacja: {{ states('sensor.ecowitt_solar_radiation_9747') }} W/m²
                    🌧️ Deszcz: {{ states('sensor.ecowitt_rain_rate_9747') }} mm/h
                    🔆 UV: {{ states('sensor.ecowitt_uv_index_9747') }}
                    🔋 SOC: {{ states('sensor.battery_state_of_charge') }}%
                    📊 PV remaining: {{ states('sensor.pv_forecast_remaining_today_total') }} kWh
                    ⚡ PV teraz: {{ states('sensor.pv_power') }} W
                    → Priorytet ładowania baterii zamiast sprzedaży!
              - action: notify.mobile_app_iphone_grzegorz
                data:
                  title: "🌥️ Smart Pre-Peak: Pogoda słaba — priorytet bateria (10:00)"
                  message: >
                    ☀️ Radiacja: {{ states('sensor.ecowitt_solar_radiation_9747') }} W/m²
                    🌧️ Deszcz: {{ states('sensor.ecowitt_rain_rate_9747') }} mm/h
                    🔆 UV: {{ states('sensor.ecowitt_uv_index_9747') }}
                    🔋 SOC: {{ states('sensor.battery_state_of_charge') }}%
                    📊 PV remaining: {{ states('sensor.pv_forecast_remaining_today_total') }} kWh
                    ⚡ PV teraz: {{ states('sensor.pv_power') }} W
                    → Priorytet ładowania baterii zamiast sprzedaży!

          # ── PUNKT 3 — 13:30 OSTATNIA SZANSA (ZIMA: szczyt od 16:00) ──
          - conditions:
              - condition: trigger
                id: prepeak_afternoon_winter
              - condition: numeric_state
                entity_id: sensor.battery_state_of_charge
                below: 80
              - condition: template
                value_template: "{{ now().month in [1, 2, 3, 10, 11, 12] }}"
              - condition: or
                conditions:
                  - condition: numeric_state
                    entity_id: sensor.pv_forecast_remaining_today_total
                    below: 5
                  - condition: numeric_state
                    entity_id: sensor.ecowitt_solar_radiation_9747
                    below: 150
                  - condition: numeric_state
                    entity_id: sensor.ecowitt_rain_rate_9747
                    above: 0
            sequence:
              - action: goodwe.set_parameter
                data:
                  device_id: 02592f41265ac022d0c8b8aa99728b3e
                  parameter: battery_charge_current
                  value: "18.5"
              - action: notify.mobile_app_iphone_henryk
                data:
                  title: "⚠️🔋 Smart Pre-Peak: OSTATNIA SZANSA — szczyt za 2.5h! (13:30)"
                  message: >
                    🔋 SOC: {{ states('sensor.battery_state_of_charge') }}% (cel: ≥80%)
                    ☀️ Radiacja: {{ states('sensor.ecowitt_solar_radiation_9747') }} W/m²
                    📊 PV remaining: {{ states('sensor.pv_forecast_remaining_today_total') }} kWh
                    ⚡ PV teraz: {{ states('sensor.pv_power') }} W
                    💰 O 16:00 prąd = 1.50 zł! Każdy % SOC to oszczędność!
              - action: notify.mobile_app_iphone_grzegorz
                data:
                  title: "⚠️🔋 Smart Pre-Peak: OSTATNIA SZANSA — szczyt za 2.5h! (13:30)"
                  message: >
                    🔋 SOC: {{ states('sensor.battery_state_of_charge') }}% (cel: ≥80%)
                    ☀️ Radiacja: {{ states('sensor.ecowitt_solar_radiation_9747') }} W/m²
                    📊 PV remaining: {{ states('sensor.pv_forecast_remaining_today_total') }} kWh
                    ⚡ PV teraz: {{ states('sensor.pv_power') }} W
                    💰 O 16:00 prąd = 1.50 zł! Każdy % SOC to oszczędność!

          # ── PUNKT 4 — 18:00 PRE-PEAK LETNI ──
          - conditions:
              - condition: trigger
                id: prepeak_afternoon_summer
              - condition: numeric_state
                entity_id: sensor.battery_state_of_charge
                below: 70
              - condition: template
                value_template: "{{ now().month in [4, 5, 6, 7, 8, 9] }}"
              - condition: or
                conditions:
                  - condition: numeric_state
                    entity_id: sensor.pv_forecast_remaining_today_total
                    below: 3
                  - condition: numeric_state
                    entity_id: sensor.ecowitt_solar_radiation_9747
                    below: 100
            sequence:
              - action: goodwe.set_parameter
                data:
                  device_id: 02592f41265ac022d0c8b8aa99728b3e
                  parameter: battery_charge_current
                  value: "18.5"
              - action: notify.mobile_app_iphone_henryk
                data:
                  title: "☀️⚠️ Smart Pre-Peak LATO: szczyt za 1h! (18:00)"
                  message: >
                    🔋 SOC: {{ states('sensor.battery_state_of_charge') }}%
                    📊 PV remaining: {{ states('sensor.pv_forecast_remaining_today_total') }} kWh
                    ☀️ Radiacja: {{ states('sensor.ecowitt_solar_radiation_9747') }} W/m²
                    💰 O 19:00 prąd = 1.50 zł!
              - action: notify.mobile_app_iphone_grzegorz
                data:
                  title: "☀️⚠️ Smart Pre-Peak LATO: szczyt za 1h! (18:00)"
                  message: >
                    🔋 SOC: {{ states('sensor.battery_state_of_charge') }}%
                    📊 PV remaining: {{ states('sensor.pv_forecast_remaining_today_total') }} kWh
                    ☀️ Radiacja: {{ states('sensor.ecowitt_solar_radiation_9747') }} W/m²
                    💰 O 19:00 prąd = 1.50 zł!

          # ── PUNKT 5 — REAKTYWNY: Nagłe zachmurzenie ──
          - conditions:
              - condition: trigger
                id: sudden_cloud_cover
              - condition: numeric_state
                entity_id: sensor.battery_state_of_charge
                below: 70
              - condition: sun
                after: sunrise
                before: sunset
              - condition: template
                value_template: >
                  {% set h = now().hour %}
                  {% set m = now().month %}
                  {% if m in [4,5,6,7,8,9] %}
                    {{ h >= 13 and h < 19 }}
                  {% else %}
                    {{ h >= 13 and h < 16 }}
                  {% endif %}
            sequence:
              - action: goodwe.set_parameter
                data:
                  device_id: 02592f41265ac022d0c8b8aa99728b3e
                  parameter: battery_charge_current
                  value: "18.5"
              - action: notify.mobile_app_iphone_henryk
                data:
                  title: "☁️ Nagłe zachmurzenie — priorytet bateria!"
                  message: >
                    ☀️ Radiacja spadła do {{ states('sensor.ecowitt_solar_radiation_9747') }} W/m²
                    🔋 SOC: {{ states('sensor.battery_state_of_charge') }}%
                    ⚡ PV: {{ states('sensor.pv_power') }} W
                    → Cała produkcja PV → bateria
              - action: notify.mobile_app_iphone_grzegorz
                data:
                  title: "☁️ Nagłe zachmurzenie — priorytet bateria!"
                  message: >
                    ☀️ Radiacja spadła do {{ states('sensor.ecowitt_solar_radiation_9747') }} W/m²
                    🔋 SOC: {{ states('sensor.battery_state_of_charge') }}%
                    ⚡ PV: {{ states('sensor.pv_power') }} W
                    → Cała produkcja PV → bateria

          # ── PUNKT 6 — REAKTYWNY: Deszcz ──
          - conditions:
              - condition: trigger
                id: rain_started
              - condition: numeric_state
                entity_id: sensor.battery_state_of_charge
                below: 70
              - condition: sun
                after: sunrise
                before: sunset
              - condition: template
                value_template: >
                  {% set h = now().hour %}
                  {% set m = now().month %}
                  {% if m in [4,5,6,7,8,9] %}
                    {{ h >= 13 and h < 19 }}
                  {% else %}
                    {{ h >= 13 and h < 16 }}
                  {% endif %}
            sequence:
              - action: goodwe.set_parameter
                data:
                  device_id: 02592f41265ac022d0c8b8aa99728b3e
                  parameter: battery_charge_current
                  value: "18.5"
              - action: notify.mobile_app_iphone_henryk
                data:
                  title: "🌧️ Deszcz! Priorytet bateria!"
                  message: >
                    🌧️ Deszcz: {{ states('sensor.ecowitt_rain_rate_9747') }} mm/h
                    🔋 SOC: {{ states('sensor.battery_state_of_charge') }}%
                    ⚡ PV: {{ states('sensor.pv_power') }} W
                    → Każdy wat PV → bateria
              - action: notify.mobile_app_iphone_grzegorz
                data:
                  title: "🌧️ Deszcz! Priorytet bateria!"
                  message: >
                    🌧️ Deszcz: {{ states('sensor.ecowitt_rain_rate_9747') }} mm/h
                    🔋 SOC: {{ states('sensor.battery_state_of_charge') }}%
                    ⚡ PV: {{ states('sensor.pv_power') }} W
                    → Każdy wat PV → bateria

    mode: queued
    max: 3
