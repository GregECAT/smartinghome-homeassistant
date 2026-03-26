# Smarting HOME — Instrukcja: Wymuś Ładowanie / Wymuś Rozładowanie

## Dla agenta AI zarządzającego Home Assistant

Data: 26.03.2026 | System: GoodWe BT 3-fazowy | Bateria: Lynx Home U 10.2 kWh

---

## 1. KRYTYCZNE ODKRYCIA — MUSISZ TO WIEDZIEĆ

### 1.1 Odwrócone znaki na GoodWe BT (potwierdzone testami 25.03.2026)

| Sensor                            | Wartość UJEMNA        | Wartość DODATNIA         |
| --------------------------------- | --------------------- | ------------------------ |
| `sensor.battery_power`            | **ŁADOWANIE** baterii | **ROZŁADOWANIE** baterii |
| `sensor.battery_current`          | **ŁADOWANIE** baterii | **ROZŁADOWANIE** baterii |
| `sensor.meter_active_power_total` | **EKSPORT** do sieci  | **IMPORT** z sieci       |

**UWAGA**: To jest odwrotnie niż w typowej dokumentacji GoodWe ET!

### 1.2 Odwrócone rejestry Modbus na BT (vs dokumentacja ET)

| Wartość 47511 | Dokumentacja ET | Rzeczywiste na BT                       |
| ------------- | --------------- | --------------------------------------- |
| 0             | Auto            | Auto                                    |
| 1             | Self-use        | Self-use                                |
| 2             | Force Charge    | **NIEPRZETESTOWANE**                    |
| 4             | Force Discharge | **Force Charge z sieci** (potwierdzone) |

### 1.3 Grid Export domyślnie WYŁĄCZONY

Rejestr `47509` (Grid Export Enabled) = **0** domyślnie na tym falowniku.
Przed jakimkolwiek rozładowaniem do sieci MUSISZ go włączyć na 1.

---

## 2. METODA STEROWANIA — ECO MODE via mletenay (ZALECANA)

### 2.1 Dostępne encje sterujące

Integracja HACS mletenay (experimental) udostępnia:

| Encja                                      | Typ     | Opis                            | Zakres                                                                  |
| ------------------------------------------ | ------- | ------------------------------- | ----------------------------------------------------------------------- |
| `select.goodwe_tryb_pracy_falownika`       | select  | Tryb pracy                      | general, eco_charge, eco_discharge, backup, off_grid, eco, peak_shaving |
| `number.goodwe_eco_mode_power`             | number  | Moc Eco Mode                    | 0-100%                                                                  |
| `number.goodwe_eco_mode_soc`               | number  | Docelowy SOC Eco Mode           | 0-100%                                                                  |
| `goodwe.set_parameter`                     | service | Parametry falownika             | battery_charge_current, grid_export_limit                               |
| `number.goodwe_depth_of_discharge_on_grid` | number  | DOD on-grid                     | 0-99%                                                                   |
| `number.goodwe_grid_export_limit`          | number  | Limit eksportu (GUI max 10000W) | 0-10000 W                                                               |

**Device ID falownika**: `02592f41265ac022d0c8b8aa99728b3e`

### 2.2 Kluczowe parametry set_parameter

```yaml
# Wartości MUSZĄ być STRING (nie int!)
goodwe.set_parameter:
  device_id: 02592f41265ac022d0c8b8aa99728b3e
  parameter: battery_charge_current
  value: "18.5"   # max ładowanie
  # value: "0"    # blokada ładowania

goodwe.set_parameter:
  device_id: 02592f41265ac022d0c8b8aa99728b3e
  parameter: grid_export_limit
  value: "16000"   # max eksport (powyżej 10000 wymaga set_parameter, nie GUI)
```

---

## 3. WYMUŚ ŁADOWANIE (Force Grid Charge)

### 3.1 Sekwencja komend (w tej kolejności!)

```yaml
# Krok 1: Ustaw docelowy SOC na 100%
- action: number.set_value
  target:
    entity_id: number.goodwe_eco_mode_soc
  data:
    value: 100

# Krok 2: Ustaw moc Eco Mode na 100% (pełna moc ~2-3 kW z sieci)
- action: number.set_value
  target:
    entity_id: number.goodwe_eco_mode_power
  data:
    value: 100

# Krok 3: Ustaw max prąd ładowania
- action: goodwe.set_parameter
  data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "18.5"

# Krok 4: Przełącz na eco_charge
- action: select.select_option
  target:
    entity_id: select.goodwe_tryb_pracy_falownika
  data:
    option: "eco_charge"
```

### 3.2 Oczekiwane zachowanie po włączeniu

- `sensor.battery_power` → **ujemny** (~-1500 do -3000 W) = ładowanie
- `sensor.meter_active_power_total` → **dodatni** (~800-2000 W) = import z sieci
- `sensor.battery_current` → **ujemny** (~-6 do -12 A)
- SOC rośnie

### 3.3 Warunek stopu

- SOC osiągnął 95% → przywróć tryb general
- Lub ręczne wyłączenie przez użytkownika

### 3.4 Sekwencja przywracania (cleanup)

```yaml
# Krok 1: Resetuj moc Eco Mode
- action: number.set_value
  target:
    entity_id: number.goodwe_eco_mode_power
  data:
    value: 0

# Krok 2: Przywróć tryb general
- action: select.select_option
  target:
    entity_id: select.goodwe_tryb_pracy_falownika
  data:
    option: "general"
```

### 3.5 WAŻNE: eco_mode_power = 0% vs 100%

| Wartość       | Moc ładowania z sieci | Notatka                |
| ------------- | --------------------- | ---------------------- |
| 0% (domyślne) | ~770 W                | Domyślny slot Eco Mode |
| 100%          | ~2000-3000 W          | Pełna moc falownika    |

To jest kluczowe odkrycie — SolarGo ustawił eco_mode_power na wyższą wartość i dlatego ładowanie z sieci dawało 2 kW zamiast 770W.

---

## 4. WYMUŚ ROZŁADOWANIE (Force Battery Discharge to Grid)

### 4.1 Sekwencja komend (w tej kolejności!)

```yaml
# Krok 1: KRYTYCZNE — włącz eksport do sieci (domyślnie wyłączony!)
- action: modbus.write_register
  data:
    hub: goodwe_rs485
    slave: 247
    address: 47509
    value: 1

# Krok 2: Ustaw max limit eksportu
- action: goodwe.set_parameter
  data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: grid_export_limit
    value: "16000"

# Krok 3: Zablokuj ładowanie baterii
- action: goodwe.set_parameter
  data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "0"

# Krok 4: Ustaw minimalny SOC docelowy (do jakiego poziomu rozładować)
- action: number.set_value
  target:
    entity_id: number.goodwe_eco_mode_soc
  data:
    value: 5

# Krok 5: Ustaw moc Eco Mode na 100%
- action: number.set_value
  target:
    entity_id: number.goodwe_eco_mode_power
  data:
    value: 100

# Krok 6: Przełącz na eco_discharge
- action: select.select_option
  target:
    entity_id: select.goodwe_tryb_pracy_falownika
  data:
    option: "eco_discharge"
```

### 4.2 Oczekiwane zachowanie po włączeniu

- `sensor.battery_power` → **dodatni** (rozładowanie na BT!)
- `sensor.meter_active_power_total` → **ujemny** (eksport do sieci)
- SOC spada

**UWAGA**: eco_discharge na BT wcześniej (19.03) powodował ładowanie z sieci (~770W) zamiast rozładowania, bo domyślne sloty Eco Mode były ustawione na charge. Po zmianie eco_mode_power i eco_mode_soc, zachowanie MOŻE być inne. Wymaga testu!

### 4.3 Jeśli eco_discharge NIE działa — metoda alternatywna

```yaml
# Alternatywa: tryb general + charge_current=0 + DOD=95%
# Bateria rozładowuje się PASYWNIE na dom (nie aktywnie do sieci)
- action: select.select_option
  target:
    entity_id: select.goodwe_tryb_pracy_falownika
  data:
    option: "general"
- action: goodwe.set_parameter
  data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "0"
```

To NIE eksportuje aktywnie do sieci — bateria tylko zasila dom. Ale gwarantuje że nie ładuje z sieci.

### 4.4 Warunek stopu

- SOC spadł poniżej 20% → przywróć tryb general
- Lub ręczne wyłączenie

### 4.5 Sekwencja przywracania (cleanup)

```yaml
# Krok 1: Resetuj Eco Mode
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

# Krok 2: Przywróć tryb general
- action: select.select_option
  target:
    entity_id: select.goodwe_tryb_pracy_falownika
  data:
    option: "general"

# Krok 3: Przywróć ładowanie
- action: goodwe.set_parameter
  data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "18.5"
```

---

## 5. EMERGENCY STOP

Awaryjne przywrócenie normalnego stanu — użyj gdy coś pójdzie nie tak:

```yaml
# 1. Reset Eco Mode
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

# 2. Reset Modbus EMS Mode
- action: modbus.write_register
  data:
    hub: goodwe_rs485
    slave: 247
    address: 47511
    value: 0

# 3. Przywróć general
- action: select.select_option
  target:
    entity_id: select.goodwe_tryb_pracy_falownika
  data:
    option: "general"

# 4. Przywróć ładowanie i eksport
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
```

---

## 6. INTEGRACJA Z ISTNIEJĄCYM HEMS

### 6.1 Przełączniki (input_boolean) — już utworzone

| Helper                                       | Opis                        |
| -------------------------------------------- | --------------------------- |
| `input_boolean.hems_force_grid_charge`       | Wymuś ładowanie z sieci     |
| `input_boolean.hems_force_battery_discharge` | Wymuś rozładowanie do sieci |
| `input_boolean.hems_modbus_emergency_stop`   | Awaryjny STOP               |

### 6.2 Blokada automatyzacji HEMS

Gdy przełącznik Force jest ON, WSZYSTKIE automatyzacje HEMS (19 sztuk) mają warunek:

```yaml
- condition: template
  value_template: >
    {{ is_state('input_boolean.hems_force_grid_charge', 'off')
       and is_state('input_boolean.hems_force_battery_discharge', 'off') }}
```

Dzięki temu żadna automatyzacja HEMS nie nadpisze parametrów falownika gdy Force jest aktywny.

### 6.3 Automatyzacje Force w automations.yaml

ID automatyzacji:

- `hems_modbus_force_grid_charge` — trigger: `input_boolean.hems_force_grid_charge` → "on"
- `hems_modbus_force_battery_discharge` — trigger: `input_boolean.hems_force_battery_discharge` → "on"
- `hems_modbus_emergency_stop` — trigger: `input_boolean.hems_modbus_emergency_stop` → "on"

Każda automatyzacja:

1. Wykonuje sekwencję komend (sekcje 3/4/5)
2. `wait_for_trigger` na SOC target lub wyłączenie przełącznika
3. Cleanup — przywraca tryb general
4. Wyłącza przełącznik (`input_boolean.turn_off`)

---

## 7. KONFIGURACJA PRZYCISKÓW W DASHBOARDZIE

### 7.1 Prosty przycisk ON/OFF (toggle)

```yaml
type: button
entity: input_boolean.hems_force_grid_charge
name: "Wymuś Ładowanie"
icon: mdi:battery-charging-wireless
tap_action:
  action: toggle
```

### 7.2 Zaawansowany panel (jak na screenshocie)

Panel powinien pokazywać:

- Aktualny tryb pracy: `select.goodwe_tryb_pracy_falownika`
- Modbus 47511: `sensor.gw_modbus_ems_mode`
- Prąd ładowania: odczyt z `sensor.battery_current` (BT: ujemny = ładowanie)
- Export limit: `sensor.gw_modbus_grid_export_limit`
- Przycisk WYKONAJ: toggle `input_boolean.hems_force_grid_charge`

### 7.3 Weryfikacja działania (sensory do monitorowania)

| Co sprawdzić | Sensor                               | Oczekiwane przy charge | Oczekiwane przy discharge |
| ------------ | ------------------------------------ | ---------------------- | ------------------------- |
| Moc baterii  | `sensor.battery_power`               | ujemny (-W)            | dodatni (+W)              |
| Moc sieci    | `sensor.meter_active_power_total`    | dodatni (+W, import)   | ujemny (-W, eksport)      |
| SOC          | `sensor.battery_state_of_charge`     | rośnie                 | spada                     |
| Tryb         | `select.goodwe_tryb_pracy_falownika` | eco_charge             | eco_discharge             |
| Eco power    | `number.goodwe_eco_mode_power`       | 100                    | 100                       |
| Eco SOC      | `number.goodwe_eco_mode_soc`         | 100                    | 5                         |

---

## 8. CZEGO NIE ROBIĆ

1. **NIE używaj `select.select_option` jednocześnie z `modbus.write_register 47511`** — bijają się nawzajem (UDP vs RS485)
2. **NIE ustawiaj `grid_export_limit = 0`** — blokuje cały eksport, nawet PV
3. **NIE używaj `value: 0` (int)** — musi być `value: "0"` (string) w set_parameter
4. **NIE ufaj dokumentacji ET dla modelu BT** — znaki i rejestry są odwrócone
5. **NIE zakładaj że eco_discharge działa** — wymaga testu (wcześniej ładowało z sieci)
6. **NIE zapominaj o cleanup** — po wyłączeniu Force ZAWSZE przywróć general + eco_mode_power=0

---

## 9. MODBUS RS485 — PARAMETRY POŁĄCZENIA

```yaml
modbus:
  - name: goodwe_rs485
    type: serial
    method: rtu
    port: /dev/serial/by-id/usb-FTDI_FT232R_USB_UART_BG02YUXJ-if00-port0
    baudrate: 9600
    bytesize: 8
    parity: N
    stopbits: 1
```

Slave: 247

---

## 10. MAPA REJESTRÓW MODBUS (potwierdzone)

| Rejestr | Opis                    | Aktualny stan      |
| ------- | ----------------------- | ------------------ |
| 47500   | SOC Protection          | 1%                 |
| 47501   | DOD On Grid             | 3294 (packed)      |
| 47502   | DOD Off Grid            | 30%                |
| 47509   | Grid Export Enabled     | **0** (wyłączony!) |
| 47510   | Grid Export Limit       | 16000 W            |
| 47511   | EMS Mode                | 1 (Self-use)       |
| 47512   | EMS Power Limit         | 0 W                |
| 47513   | ?                       | 1                  |
| 47514   | ?                       | 257                |
| 47515   | Eco Slot1 Start         | 0 (00:00)          |
| 47516   | Eco Slot1 End           | 5947 (23:59)       |
| 47517   | Eco Slot1 Power         | 0                  |
| 47518   | Eco Slot1 Switch/Config | 65407 (0xFF7F)     |
| 47519   | Eco Slot1 ?             | 261                |
| 47520   | Eco Slot1 ?             | 4096               |
| 47522   | Eco Mode 2 Switch       | 64 (wyłączony)     |
| 47526   | Eco Mode 3 Switch       | 0                  |
| 47530   | Eco Mode 4 Switch       | 0                  |
| 45353   | Charge Current          | 185 (= 18.5 A)     |

---

## 11. PODSUMOWANIE — NAJKRÓTSZA DROGA

**Wymuś Ładowanie z sieci:**
`eco_mode_soc=100` → `eco_mode_power=100` → `charge_current=18.5` → `select: eco_charge`

**Wymuś Rozładowanie do sieci:**
`47509=1` → `export_limit=16000` → `charge_current=0` → `eco_mode_soc=5` → `eco_mode_power=100` → `select: eco_discharge`

**STOP:**
`eco_mode_power=0` → `eco_mode_soc=100` → `47511=0` → `select: general` → `charge_current=18.5` → `export_limit=16000`
