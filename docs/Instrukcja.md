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

### 3.2 Oczekiwane zachowanie po włączeniu (POTWIERDZONE 26.03.2026)

- `sensor.battery_power` → **ujemny** (~-3800 W) = ładowanie na BT ✅
- `sensor.meter_active_power_total` → **dodatni** (~5000 W) = import z sieci ✅
- `sensor.battery_current` → **ujemny** (~-18.0 A) ✅
- SOC rośnie
- Falownik moc: ~5.1 kW (bateria 3.8 kW + dom 1.1 kW)

### 3.3 PRZYCISK STOP — Zatrzymaj ładowanie

Gdy użytkownik kliknie STOP na panelu "Wymuś Ładowanie", wykonaj **całą sekwencję**:

```yaml
# ═══ STOP FORCE CHARGE — pełna sekwencja przycisku ═══

# Krok 1: Resetuj moc Eco Mode na 0%
- action: number.set_value
  target:
    entity_id: number.goodwe_eco_mode_power
  data:
    value: 0

# Krok 2: Resetuj docelowy SOC
- action: number.set_value
  target:
    entity_id: number.goodwe_eco_mode_soc
  data:
    value: 100

# Krok 3: Przywróć tryb general
- action: select.select_option
  target:
    entity_id: select.goodwe_tryb_pracy_falownika
  data:
    option: "general"

# Krok 4: Przywróć normalny prąd ładowania (dla PV)
- action: goodwe.set_parameter
  data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "18.5"

# Krok 5: Wyłącz przełącznik (jeśli używasz input_boolean)
- action: input_boolean.turn_off
  target:
    entity_id: input_boolean.hems_force_grid_charge
```

**Po kliknięciu STOP oczekuj:**

- `select.goodwe_tryb_pracy_falownika` → "general"
- `number.goodwe_eco_mode_power` → 0
- `sensor.meter_active_power_total` → spada do ~0 W (brak importu)
- `sensor.battery_power` → zależy od PV i zużycia domu

### 3.4 Automatyczny STOP — bateria pełna

Agent MUSI monitorować SOC i automatycznie zatrzymać ładowanie:

```yaml
# ═══ AUTO-STOP CHARGE — gdy bateria pełna ═══
# Trigger: sensor.battery_state_of_charge >= 95%
# LUB: sensor.battery_state_of_charge >= wartość docelowa z panelu

# Wykonaj IDENTYCZNĄ sekwencję jak PRZYCISK STOP (sekcja 3.3):
# eco_mode_power=0 → eco_mode_soc=100 → general → charge=18.5 → input_boolean OFF
```

**Progi automatycznego STOP dla ładowania:**

| Próg SOC                  | Akcja                     | Powód                                                                |
| ------------------------- | ------------------------- | -------------------------------------------------------------------- |
| **95%**                   | STOP + przejdź na general | Domyślny bezpieczny próg (BMS chroni 95-100%)                        |
| **100%**                  | STOP natychmiastowy       | Bateria pełna — falownik sam zatrzyma, ale ECO mode zostanie aktywny |
| **Docelowy SOC z panelu** | STOP + general            | Użytkownik wybrał ile chce naładować                                 |

**WAŻNE:** Nawet jeśli falownik sam zatrzyma ładowanie przy 100%, tryb `eco_charge`
pozostanie aktywny! Agent MUSI wykonać sekwencję STOP żeby wrócić do `general`.
Inaczej o 07:00 automatyzacja HEMS `hems_morning_sell_mode` nie przełączy na sprzedaż,
bo Modbus Guard zablokuje ją (input_boolean nadal ON).

**Implementacja w panelu:**

```yaml
# Opcja 1: Prosty auto-stop na 95%
# Agent nasłuchuje sensor.battery_state_of_charge
# Gdy >= 95% AND input_boolean.hems_force_grid_charge == "on":
#   → wykonaj sekwencję STOP z sekcji 3.3

# Opcja 2: Auto-stop na wartość docelową z panelu
# Panel ma pole "Ładuj do SOC: [___]%" (domyślnie 95%)
# Agent nasłuchuje sensor.battery_state_of_charge
# Gdy >= wartość_docelowa AND input_boolean.hems_force_grid_charge == "on":
#   → wykonaj sekwencję STOP z sekcji 3.3
```

**Timeout safety:** Jeśli SOC nie osiągnie progu przez 6 godzin → STOP awaryjny.
Bateria 10.2 kWh ładuje się z 3.8 kW w ~3 godziny. 6h to podwójny margines.

### 3.5 WAŻNE: eco_mode_power = 0% vs 100%

| Wartość       | Moc ładowania z sieci      | Moc rozładowania             | Potwierdzone   |
| ------------- | -------------------------- | ---------------------------- | -------------- |
| 0% (domyślne) | ~770 W                     | ~770 W (ale ładowało!)       | 19.03.2026     |
| 100%          | **~3.8 kW** (5 kW z sieci) | **~3.6 kW** (2.4 kW eksport) | **26.03.2026** |

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

### 4.2 Oczekiwane zachowanie po włączeniu (POTWIERDZONE 26.03.2026)

- `sensor.battery_power` → **dodatni** (~3600 W) = rozładowanie na BT ✅
- `sensor.battery_current` → **dodatni** (~18.1 A) = rozładowanie na BT ✅
- `sensor.meter_active_power_total` → **ujemny** (~-2400 W) = eksport do sieci ✅
- SOC spada
- Falownik moc: ~5.1 kW (bateria 3.6 kW + reszta na dom)

**POTWIERDZONE 26.03.2026 o 18:28:**

- eco_discharge z eco_mode_power=100% + 47509=1 → **3.6 kW rozładowanie + 2.4 kW eksport do sieci** ✅
- eco_charge z eco_mode_power=100% → **3.8 kW ładowanie + 5.0 kW import z sieci** ✅
- Wcześniej (19.03) eco_discharge dawał ładowanie 770W bo eco_mode_power=0% i 47509=0
- Klucz do sukcesu: eco_mode_power=100% + grid_export_enabled (47509=1)

### 4.3 PRZYCISK STOP — Zatrzymaj rozładowanie

Gdy użytkownik kliknie STOP na panelu "Wymuś Rozładowanie", wykonaj **całą sekwencję**:

```yaml
# ═══ STOP FORCE DISCHARGE — pełna sekwencja przycisku ═══

# Krok 1: Resetuj moc Eco Mode na 0%
- action: number.set_value
  target:
    entity_id: number.goodwe_eco_mode_power
  data:
    value: 0

# Krok 2: Resetuj docelowy SOC na 100%
- action: number.set_value
  target:
    entity_id: number.goodwe_eco_mode_soc
  data:
    value: 100

# Krok 3: Przywróć tryb general
- action: select.select_option
  target:
    entity_id: select.goodwe_tryb_pracy_falownika
  data:
    option: "general"

# Krok 4: Przywróć ładowanie baterii (odblokuj dla PV)
- action: goodwe.set_parameter
  data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: "18.5"

# Krok 5: Utrzymaj export limit (nie zeruj — PV nadal może eksportować)
- action: goodwe.set_parameter
  data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: grid_export_limit
    value: "16000"

# Krok 6: Wyłącz przełącznik (jeśli używasz input_boolean)
- action: input_boolean.turn_off
  target:
    entity_id: input_boolean.hems_force_battery_discharge
```

**Po kliknięciu STOP oczekuj:**

- `select.goodwe_tryb_pracy_falownika` → "general"
- `number.goodwe_eco_mode_power` → 0
- `sensor.meter_active_power_total` → wzrasta do ~0 W (brak eksportu z baterii)
- `sensor.battery_power` → zależy od PV i zużycia domu
- Bateria przestaje się aktywnie rozładowywać do sieci

**UWAGA:** NIE wyłączaj `47509` (grid export enabled) w STOP —
zostaw na 1, bo automatyzacje HEMS mogą potrzebować eksportu PV.

### 4.4 Automatyczny STOP — bateria rozładowana

Agent MUSI monitorować SOC i automatycznie zatrzymać rozładowanie:

```yaml
# ═══ AUTO-STOP DISCHARGE — gdy bateria rozładowana ═══
# Trigger: sensor.battery_state_of_charge <= 20% (bezpieczny próg)
# LUB: sensor.battery_state_of_charge <= wartość minimalna z panelu

# Wykonaj IDENTYCZNĄ sekwencję jak PRZYCISK STOP (sekcja 4.3):
# eco_mode_power=0 → eco_mode_soc=100 → general → charge=18.5 → export=16000 → input_boolean OFF
```

**Progi automatycznego STOP dla rozładowania:**

| Próg SOC                   | Akcja                     | Powód                                     |
| -------------------------- | ------------------------- | ----------------------------------------- |
| **20%**                    | STOP + przejdź na general | Bezpieczny próg — zostaw rezerwę na dom   |
| **10%**                    | STOP pilny                | Niski poziom — bateria blisko minimum     |
| **5%**                     | STOP KRYTYCZNY            | BMS może wyłączyć baterię! Shutdown grozi |
| **Minimalny SOC z panelu** | STOP + general            | Użytkownik wybrał do ilu rozładować       |

**WAŻNE:** Jeśli SOC spadnie do 5%, Lynx Home U BMS może odciąć baterię (shutdown).
Odzyskanie po shutdown wymaga ręcznej interwencji lub ładowania z PV.
Agent powinien NIGDY nie pozwolić zejść poniżej 10% podczas force discharge.

**KRYTYCZNE:** Po STOP discharge przywróć `charge_current=18.5` — inaczej bateria
nie będzie mogła się ładować z PV ani z sieci. To najczęstszy błąd!

**Implementacja w panelu:**

```yaml
# Opcja 1: Prosty auto-stop na 20%
# Agent nasłuchuje sensor.battery_state_of_charge
# Gdy <= 20% AND input_boolean.hems_force_battery_discharge == "on":
#   → wykonaj sekwencję STOP z sekcji 4.3

# Opcja 2: Auto-stop na wartość minimalną z panelu
# Panel ma pole "Rozładuj do SOC: [___]%" (domyślnie 20%)
# Agent nasłuchuje sensor.battery_state_of_charge
# Gdy <= wartość_minimalna AND input_boolean.hems_force_battery_discharge == "on":
#   → wykonaj sekwencję STOP z sekcji 4.3

# Opcja 3: Wielopoziomowa ochrona
# SOC <= 20%: normalne STOP
# SOC <= 10%: STOP + powiadomienie "SOC krytycznie niski"
# SOC <= 5%:  EMERGENCY STOP (sekcja 5) + alarm
```

**Timeout safety:** Jeśli SOC nie osiągnie progu przez 6 godzin → STOP awaryjny.
Bateria 10.2 kWh rozładowuje się z 3.6 kW w ~2.5 godziny. 6h to duży margines.

**Co się dzieje gdy bateria się CAŁKOWICIE rozładuje (SOC = 0%):**

1. BMS Lynx Home U odcina baterię (hardware protection)
2. Falownik przechodzi na zasilanie domu wyłącznie z sieci
3. eco_discharge pozostanie aktywny ale nic nie rozładowuje (bateria odcięta)
4. Agent MUSI wykonać STOP żeby wrócić do general
5. Bateria zacznie się ładować dopiero po: STOP + charge_current=18.5 + PV/sieć

### 4.5 Metoda alternatywna (pasywna — bez eksportu do sieci)

Jeśli z jakiegoś powodu eco_discharge przestanie działać:

```yaml
# Tryb general + charge_current=0 + DOD=95%
# Bateria rozładowuje się PASYWNIE na dom (nie eksportuje do sieci)
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

To NIE eksportuje aktywnie do sieci — bateria tylko zasila dom.
Użyj eco_discharge (sekcja 4.1) dla aktywnego eksportu — **POTWIERDZONE 26.03.2026**.

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
5. **NIE używaj eco_discharge BEZ włączenia 47509=1** — eksport do sieci jest domyślnie wyłączony, bez tego bateria nie eksportuje
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

**▶ START Wymuś Ładowanie z sieci:**
`eco_mode_soc=100` → `eco_mode_power=100` → `charge_current=18.5` → `select: eco_charge`

**■ STOP Wymuś Ładowanie (ręczny lub automatyczny):**
`eco_mode_power=0` → `eco_mode_soc=100` → `select: general` → `charge_current=18.5` → `input_boolean.hems_force_grid_charge: OFF`

**⏹ AUTO-STOP Charge:** SOC >= 95% (lub docelowy SOC z panelu) → wykonaj STOP

**▶ START Wymuś Rozładowanie do sieci:**
`47509=1` → `export_limit=16000` → `charge_current=0` → `eco_mode_soc=5` → `eco_mode_power=100` → `select: eco_discharge`

**■ STOP Wymuś Rozładowanie (ręczny lub automatyczny):**
`eco_mode_power=0` → `eco_mode_soc=100` → `select: general` → `charge_current=18.5` → `export_limit=16000` → `input_boolean.hems_force_battery_discharge: OFF`

**⏹ AUTO-STOP Discharge:** SOC <= 20% (lub minimalny SOC z panelu) → wykonaj STOP

**🚨 EMERGENCY STOP (oba naraz):**
`eco_mode_power=0` → `eco_mode_soc=100` → `47511=0` → `select: general` → `charge_current=18.5` → `export_limit=16000` → wyłącz oba input_boolean

**⏱ TIMEOUT:** 6 godzin → STOP awaryjny (safety net)

## 12. TABELA AKCJI DLA AGENTA — SZYBKI REFERENCE

| Zdarzenie                       | Akcja               | Sekwencja               |
| ------------------------------- | ------------------- | ----------------------- |
| Klik "Wymuś Ładowanie"          | START Charge        | Sekcja 3.1              |
| Klik "STOP" na ładowaniu        | STOP Charge         | Sekcja 3.3              |
| SOC >= 95% podczas ładowania    | AUTO-STOP Charge    | Sekcja 3.3 (identyczna) |
| Klik "Wymuś Rozładowanie"       | START Discharge     | Sekcja 4.1              |
| Klik "STOP" na rozładowaniu     | STOP Discharge      | Sekcja 4.3              |
| SOC <= 20% podczas rozładowania | AUTO-STOP Discharge | Sekcja 4.3 (identyczna) |
| SOC <= 5% (krytyczny)           | EMERGENCY STOP      | Sekcja 5                |
| Timeout 6h                      | STOP odpowiedni     | Sekcja 3.3 lub 4.3      |
| Coś poszło nie tak              | EMERGENCY STOP      | Sekcja 5                |
