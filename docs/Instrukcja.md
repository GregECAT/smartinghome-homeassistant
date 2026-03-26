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

## 5A. AUTOPILOT — POWRÓT DO NORMALNEGO TRYBU

Autopilot musi wiedzieć jaki jest "normalny tryb" — bo zależy on od **pory dnia,
dnia tygodnia i taryfy G13**. Nie wystarczy wrócić do general + charge=18.5!

### 5A.1 Jak Autopilot wykrywa że ładowanie z sieci jest aktywne

```yaml
# Sprawdź te warunki — jeśli KTÓRYKOLWIEK jest true, bateria ładuje z sieci:

# Warunek 1: Force Charge aktywny (przycisk użytkownika)
is_state('input_boolean.hems_force_grid_charge', 'on')

# Warunek 2: Tryb eco_charge aktywny (nocny arbitraż lub Force)
is_state('select.goodwe_tryb_pracy_falownika', 'eco_charge')

# Warunek 3: Bateria ładuje się (BT: ujemny = ładowanie) + import z sieci
states('sensor.battery_power') | float < -100
  AND states('sensor.meter_active_power_total') | float > 100
```

### 5A.2 Tabela — "normalny tryb" wg pory dnia (G13)

| Pora                        | Godziny (pon-pt) | Tryb    | charge_current | Co robi                          | Cel                      |
| --------------------------- | ---------------- | ------- | -------------- | -------------------------------- | ------------------------ |
| **Szczyt poranny**          | 07:00-13:00      | general | **"0"**        | Blokada ładowania, PV → sprzedaż | Sprzedawaj po 0.91 zł    |
| **Off-peak (zima)**         | 13:00-16:00      | general | **"18.5"**     | Ładowanie z PV                   | Ładuj baterię na wieczór |
| **Off-peak (lato)**         | 13:00-19:00      | general | **"18.5"**     | Ładowanie z PV                   | Ładuj baterię na wieczór |
| **Szczyt wieczorny (zima)** | 16:00-21:00      | general | **"18.5"**     | Bateria → dom                    | Oszczędzaj 1.50 zł       |
| **Szczyt wieczorny (lato)** | 19:00-22:00      | general | **"18.5"**     | Bateria → dom                    | Oszczędzaj 1.50 zł       |
| **Noc**                     | 21/22:00-07:00   | general | **"18.5"**     | Bateria → dom                    | Pozaszczytowa 0.63 zł    |
| **Weekend**                 | cała doba        | general | **"18.5"**     | Autokonsumpcja                   | Pozaszczytowa 0.63 zł    |

### 5A.3 Sekwencja — Autopilot wyłącza ładowanie z sieci i wraca do normalnego trybu

```yaml
# ═══ AUTOPILOT: POWRÓT DO NORMALNEGO TRYBU ═══
# Użyj gdy chcesz wyłączyć eco_charge / Force Charge
# i wrócić do trybu odpowiedniego dla aktualnej pory dnia

# Krok 1: Reset Eco Mode
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

# Krok 2: Wyłącz przełączniki Force (jeśli aktywne)
- action: input_boolean.turn_off
  target:
    entity_id:
      - input_boolean.hems_force_grid_charge
      - input_boolean.hems_force_battery_discharge

# Krok 3: Przywróć tryb general
- action: select.select_option
  target:
    entity_id: select.goodwe_tryb_pracy_falownika
  data:
    option: "general"

# Krok 4: Ustaw export limit
- action: goodwe.set_parameter
  data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: grid_export_limit
    value: "16000"
# Krok 5: KLUCZOWY — ustaw charge_current wg pory dnia
# ════════════════════════════════════════════════════
# PORANNE GODZINY SPRZEDAŻY (07:00-13:00 pon-pt):
#   charge_current = "0"  → bateria NIE ładuje, PV idzie na sprzedaż
#
# WSZYSTKIE INNE GODZINY:
#   charge_current = "18.5"  → bateria ładuje z PV (lub z sieci w off-peak)
# ════════════════════════════════════════════════════
```

### 5A.4 Implementacja — template do wyboru charge_current

Autopilot powinien użyć tego template do decyzji:

```yaml
# Template — jaki charge_current ustawić teraz?
{% set h = now().hour %}
{% set wd = now().weekday() %}
{% set is_weekday = wd < 5 %}
{% set morning_sell = is_weekday and (h >= 7 and h < 13) %}

{% if morning_sell %}
  0
{% else %}
  18.5
{% endif %}

# Użycie w akcji:
- action: goodwe.set_parameter
  data:
    device_id: 02592f41265ac022d0c8b8aa99728b3e
    parameter: battery_charge_current
    value: >
      {% set h = now().hour %}
      {% set wd = now().weekday() %}
      {% if wd < 5 and h >= 7 and h < 13 %}"0"{% else %}"18.5"{% endif %}
```

### 5A.5 Wyjątek RCE — arbitraż

Jeśli w momencie powrotu do normalnego trybu RCE < 100 PLN/MWh (eksport bezwartościowy),
Autopilot powinien ustawić `charge_current = "18.5"` **nawet w godzinach sprzedaży 07-13**,
bo arbitraż opłacalny (kup 0.91 → oszczędź 1.50 wieczorem = +0.59 zł/kWh).

```yaml
# Rozszerzony template z wyjątkiem RCE:
{% set h = now().hour %}
{% set wd = now().weekday() %}
{% set is_weekday = wd < 5 %}
{% set morning_sell = is_weekday and (h >= 7 and h < 13) %}
{% set rce_low = states('sensor.rce_pse_cena') | float(250) < 100 %}

{% if morning_sell and not rce_low %}
  0
{% else %}
  18.5
{% endif %}
```

### 5A.6 Scenariusze użycia Autopilota

| Scenariusz                                       | Co Autopilot robi         | charge_current              |
| ------------------------------------------------ | ------------------------- | --------------------------- |
| Nocne ładowanie zakończone (SOC=90%, jest 05:30) | STOP eco_charge → general | "18.5" (noc)                |
| Nocne ładowanie, przyszło 07:00                  | STOP eco_charge → general | **"0"** (sprzedaż!)         |
| Force Charge zakończony o 10:00                  | STOP → general            | **"0"** (godziny sprzedaży) |
| Force Charge zakończony o 14:00                  | STOP → general            | "18.5" (off-peak)           |
| Force Discharge zakończony o 20:00               | STOP → general            | "18.5" (szczyt wieczorny)   |
| Użytkownik kliknie "wróć do auto"                | Sekcja 5A.3               | wg pory dnia                |
| Coś poszło nie tak                               | EMERGENCY STOP (sekcja 5) | "18.5" (bezpieczne)         |

### 5A.7 Oczekiwane zachowanie po powrocie do normalnego trybu

**W godzinach sprzedaży (07-13 pon-pt, charge=0):**

- `sensor.battery_power` → dodatni (rozładowanie na dom, BT konwencja)
- `sensor.meter_active_power_total` → ujemny (eksport PV do sieci)
- PV zasila dom + eksportuje nadwyżkę, bateria nie ładuje

**W godzinach off-peak / wieczór / weekend (charge=18.5):**

- `sensor.battery_power` → zależy od PV (ładuje jeśli jest nadwyżka PV)
- `sensor.meter_active_power_total` → blisko 0 (autokonsumpcja)
- PV → bateria → dom, minimalna wymiana z siecią

---

## 6. WYMAGANA KONFIGURACJA — configuration.yaml

**BEZ TYCH WPISÓW SYSTEM NIE ZADZIAŁA!** Poniższe elementy MUSZĄ istnieć
w `configuration.yaml` Home Assistant zanim agent uruchomi Force Charge/Discharge.

### 6.1 Input Boolean — przełączniki Force (WYMAGANE!)

Dodaj w sekcji `input_boolean:` pliku `configuration.yaml`:

```yaml
input_boolean:
  hems_force_grid_charge:
    name: "Wymuś ładowanie z sieci"
    icon: mdi:battery-charging-wireless

  hems_force_battery_discharge:
    name: "Wymuś rozładowanie do sieci"
    icon: mdi:battery-arrow-down

  hems_modbus_emergency_stop:
    name: "Emergency STOP"
    icon: mdi:alert-octagon

  hems_battery_export_enabled:
    name: "Eksport baterii do sieci"
    icon: mdi:battery-arrow-up
```

**Bez tych helperów** agent dostanie błąd:
`Referenced entities input_boolean.hems_force_grid_charge are missing or not currently available`

Po dodaniu → **restart Home Assistant** (nie wystarczy przeładowanie konfiguracji).

### 6.2 Modbus RS485 — połączenie z falownikiem GoodWe BT

Dodaj w głównym pliku `configuration.yaml`:

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

    sensors:
      # ── EMS Mode (kluczowy do monitorowania trybu) ──
      - name: GW Modbus EMS Mode
        slave: 247
        address: 47511
        input_type: holding
        data_type: uint16
        scan_interval: 15

      - name: GW Modbus EMS Power Limit
        slave: 247
        address: 47512
        input_type: holding
        data_type: uint16
        unit_of_measurement: W
        scan_interval: 15

      # ── Grid Export (musi być = 1 dla discharge!) ──
      - name: GW Modbus Grid Export Enabled
        slave: 247
        address: 47509
        input_type: holding
        data_type: uint16
        scan_interval: 30

      - name: GW Modbus Grid Export Limit
        slave: 247
        address: 47510
        input_type: holding
        data_type: uint16
        unit_of_measurement: W
        scan_interval: 30

      # ── Eco Mode Slot switches (monitoring) ──
      - name: GW Modbus Eco Mode 1 Switch
        slave: 247
        address: 47518
        input_type: holding
        data_type: uint16
        scan_interval: 120

      - name: GW Modbus Eco Mode 2 Switch
        slave: 247
        address: 47522
        input_type: holding
        data_type: uint16
        scan_interval: 120

      # ── Battery / SOC protection ──
      - name: GW Modbus Battery SOC Protection
        slave: 247
        address: 47500
        input_type: holding
        data_type: uint16
        unit_of_measurement: "%"
        scan_interval: 60

      - name: GW Modbus DOD On Grid
        slave: 247
        address: 47501
        input_type: holding
        data_type: uint16
        scan_interval: 60

      - name: GW Modbus DOD Off Grid
        slave: 247
        address: 47502
        input_type: holding
        data_type: uint16
        unit_of_measurement: "%"
        scan_interval: 60
```

**Port RS485** — adapter FTDI FT232R. Sprawdź czy istnieje:
`ls /dev/serial/by-id/usb-FTDI_FT232R_USB_UART_BG02YUXJ-if00-port0`

### 6.3 Integracja GoodWe — HACS mletenay (WYMAGANE!)

System wymaga integracji **mletenay (experimental)** z HACS, NIE natywnej integracji HA.

**Instalacja:**

1. HACS → Integracje → wyszukaj "GoodWe" → wybierz **mletenay/goodwe** (experimental)
2. Zainstaluj i restart HA
3. Dodaj integrację: Ustawienia → Integracje → + → GoodWe → wpisz IP falownika

**Encje które integracja musi udostępnić (sprawdź po instalacji):**

| Encja                                      | Typ    | Wymagane dla                  |
| ------------------------------------------ | ------ | ----------------------------- |
| `select.goodwe_tryb_pracy_falownika`       | select | START/STOP — zmiana trybu     |
| `number.goodwe_eco_mode_power`             | number | START — ustawienie mocy Eco   |
| `number.goodwe_eco_mode_soc`               | number | START — docelowy SOC          |
| `number.goodwe_depth_of_discharge_on_grid` | number | DOD management                |
| `number.goodwe_grid_export_limit`          | number | Export limit (GUI max 10000W) |
| `sensor.battery_power`                     | sensor | Monitoring mocy baterii       |
| `sensor.battery_state_of_charge`           | sensor | SOC — trigger auto-stop       |
| `sensor.meter_active_power_total`          | sensor | Monitoring import/eksport     |
| `sensor.pv_power`                          | sensor | Monitoring PV                 |
| `sensor.load`                              | sensor | Monitoring zużycia domu       |
| `sensor.battery_current`                   | sensor | Monitoring prądu baterii      |
| `sensor.battery_voltage`                   | sensor | Monitoring napięcia baterii   |

**Device ID falownika:** `02592f41265ac022d0c8b8aa99728b3e`

Sprawdź w: Ustawienia → Urządzenia → GoodWe → kliknij falownik → URL zawiera device_id.

### 6.4 Serwis goodwe.set_parameter — parametry falownika

Integracja mletenay rejestruje serwis `goodwe.set_parameter`. Sprawdź dostępność:
Narzędzia deweloperskie → Akcje → wyszukaj "goodwe.set_parameter"

**Potwierdzone parametry dla GoodWe BT:**

| Parameter                | Wartości          | Opis                     |
| ------------------------ | ----------------- | ------------------------ |
| `battery_charge_current` | `"0"` / `"18.5"`  | Prąd ładowania (STRING!) |
| `grid_export_limit`      | `"0"` - `"16000"` | Limit eksportu (STRING!) |

**NIE działające parametry na BT:**

- `eco_mode_1` → zwraca "Unknown error"
- Jakiekolwiek parametry eco slot → nie obsługiwane

### 6.5 Template sensor — dekodowanie EMS Mode (opcjonalny ale przydatny)

Dodaj w sekcji `template: → sensor:` w `configuration.yaml`:

```yaml
template:
  - sensor:
      - name: "GoodWe EMS Mode"
        unique_id: goodwe_ems_mode
        state: >
          {% set mode = states('sensor.gw_modbus_ems_mode') | int(0) %}
          {% if mode == 0 %}Auto
          {% elif mode == 1 %}Self-use
          {% elif mode == 2 %}Force charge
          {% elif mode == 4 %}Force discharge
          {% elif mode == 8 %}Hold battery
          {% else %}Nieznany ({{ mode }})
          {% endif %}
        icon: >
          {% set mode = states('sensor.gw_modbus_ems_mode') | int(0) %}
          {% if mode == 0 %}mdi:refresh-auto
          {% elif mode == 1 %}mdi:home-battery
          {% elif mode == 2 %}mdi:battery-charging-high
          {% elif mode == 4 %}mdi:battery-arrow-down-outline
          {% elif mode == 8 %}mdi:battery-lock
          {% else %}mdi:help-circle
          {% endif %}
```

### 6.6 Weryfikacja konfiguracji — checklist po restarcie HA

Po dodaniu wszystkich wpisów i restarcie HA, sprawdź w Narzędzia deweloperskie → Stany:

```
✅ input_boolean.hems_force_grid_charge         → off
✅ input_boolean.hems_force_battery_discharge    → off
✅ input_boolean.hems_modbus_emergency_stop      → off
✅ select.goodwe_tryb_pracy_falownika            → general
✅ number.goodwe_eco_mode_power                  → 0.0
✅ number.goodwe_eco_mode_soc                    → 100.0
✅ sensor.gw_modbus_ems_mode                     → 1 (Self-use)
✅ sensor.gw_modbus_grid_export_enabled          → 0 lub 1
✅ sensor.gw_modbus_grid_export_limit            → 16000
✅ sensor.battery_state_of_charge                → (wartość 0-100%)
✅ sensor.battery_power                          → (wartość W)
✅ sensor.meter_active_power_total               → (wartość W)
```

Jeśli którakolwiek encja jest `unavailable` lub `unknown`:

- `input_boolean.*` → nie dodano do configuration.yaml, restart wymagany
- `sensor.gw_modbus_*` → problem z Modbus RS485 (kabel, port, slave ID)
- `select.goodwe_*` / `number.goodwe_*` → integracja mletenay nie działa (sprawdź IP falownika)
- `sensor.battery_*` / `sensor.meter_*` → integracja mletenay nie połączona

### 6.7 Znane problemy konfiguracji

| Problem                       | Objaw                                 | Rozwiązanie                                                   |
| ----------------------------- | ------------------------------------- | ------------------------------------------------------------- |
| Brak input_boolean            | `Referenced entities ... are missing` | Dodaj sekcję 6.1, restart HA                                  |
| Modbus nie łączy              | `pymodbus returned isError True`      | Sprawdź kabel RS485, port, slave=247                          |
| Modbus i mletenay bijią się   | Modbus write nie działa               | Nie używaj Modbus write 47511, używaj eco_mode via mletenay   |
| set_parameter "expected str"  | Błąd typu danych                      | Wartość MUSI być string: `"0"` nie `0`                        |
| set_parameter "Unknown error" | Parametr nie obsługiwany              | Na BT działa TYLKO: battery_charge_current, grid_export_limit |
| eco_mode_power nie istnieje   | Encja not found                       | Zainstaluj mletenay experimental (nie stable)                 |
| Grid export nie działa        | Bateria nie eksportuje do sieci       | Sprawdź 47509 (musi być = 1), sprawdź grid_export_limit > 0   |

---

## 7. INTEGRACJA Z ISTNIEJĄCYM HEMS

### 7.1 Przełączniki (input_boolean) — już utworzone

| Helper                                       | Opis                        |
| -------------------------------------------- | --------------------------- |
| `input_boolean.hems_force_grid_charge`       | Wymuś ładowanie z sieci     |
| `input_boolean.hems_force_battery_discharge` | Wymuś rozładowanie do sieci |
| `input_boolean.hems_modbus_emergency_stop`   | Awaryjny STOP               |

### 7.2 Blokada automatyzacji HEMS

Gdy przełącznik Force jest ON, WSZYSTKIE automatyzacje HEMS (19 sztuk) mają warunek:

```yaml
- condition: template
  value_template: >
    {{ is_state('input_boolean.hems_force_grid_charge', 'off')
       and is_state('input_boolean.hems_force_battery_discharge', 'off') }}
```

Dzięki temu żadna automatyzacja HEMS nie nadpisze parametrów falownika gdy Force jest aktywny.

### 7.3 Automatyzacje Force w automations.yaml

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

## 8. KONFIGURACJA PRZYCISKÓW W DASHBOARDZIE

### 8.1 Prosty przycisk ON/OFF (toggle)

```yaml
type: button
entity: input_boolean.hems_force_grid_charge
name: "Wymuś Ładowanie"
icon: mdi:battery-charging-wireless
tap_action:
  action: toggle
```

### 8.2 Zaawansowany panel (jak na screenshocie)

Panel powinien pokazywać:

- Aktualny tryb pracy: `select.goodwe_tryb_pracy_falownika`
- Modbus 47511: `sensor.gw_modbus_ems_mode`
- Prąd ładowania: odczyt z `sensor.battery_current` (BT: ujemny = ładowanie)
- Export limit: `sensor.gw_modbus_grid_export_limit`
- Przycisk WYKONAJ: toggle `input_boolean.hems_force_grid_charge`

### 8.3 Weryfikacja działania (sensory do monitorowania)

| Co sprawdzić | Sensor                               | Oczekiwane przy charge | Oczekiwane przy discharge |
| ------------ | ------------------------------------ | ---------------------- | ------------------------- |
| Moc baterii  | `sensor.battery_power`               | ujemny (-W)            | dodatni (+W)              |
| Moc sieci    | `sensor.meter_active_power_total`    | dodatni (+W, import)   | ujemny (-W, eksport)      |
| SOC          | `sensor.battery_state_of_charge`     | rośnie                 | spada                     |
| Tryb         | `select.goodwe_tryb_pracy_falownika` | eco_charge             | eco_discharge             |
| Eco power    | `number.goodwe_eco_mode_power`       | 100                    | 100                       |
| Eco SOC      | `number.goodwe_eco_mode_soc`         | 100                    | 5                         |

---

## 9. CZEGO NIE ROBIĆ

1. **NIE używaj `select.select_option` jednocześnie z `modbus.write_register 47511`** — bijają się nawzajem (UDP vs RS485)
2. **NIE ustawiaj `grid_export_limit = 0`** — blokuje cały eksport, nawet PV
3. **NIE używaj `value: 0` (int)** — musi być `value: "0"` (string) w set_parameter
4. **NIE ufaj dokumentacji ET dla modelu BT** — znaki i rejestry są odwrócone
5. **NIE używaj eco_discharge BEZ włączenia 47509=1** — eksport do sieci jest domyślnie wyłączony, bez tego bateria nie eksportuje
6. **NIE zapominaj o cleanup** — po wyłączeniu Force ZAWSZE przywróć general + eco_mode_power=0

---

## 10. MODBUS RS485 — PARAMETRY POŁĄCZENIA

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

## 11. MAPA REJESTRÓW MODBUS (potwierdzone)

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

## 12. PODSUMOWANIE — NAJKRÓTSZA DROGA

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

**🔄 AUTOPILOT — Powrót do normalnego trybu:**
`eco_mode_power=0` → `eco_mode_soc=100` → oba `input_boolean: OFF` → `select: general` → `export_limit=16000` → `charge_current = wg pory dnia (0 w 07-13 pon-pt, 18.5 reszta)`

**⏱ TIMEOUT:** 6 godzin → STOP awaryjny (safety net)

## 13. TABELA AKCJI DLA AGENTA — SZYBKI REFERENCE

| Zdarzenie                          | Akcja                        | Sekwencja                        |
| ---------------------------------- | ---------------------------- | -------------------------------- |
| Klik "Wymuś Ładowanie"             | START Charge                 | Sekcja 3.1                       |
| Klik "STOP" na ładowaniu           | STOP Charge                  | Sekcja 3.3                       |
| SOC >= 95% podczas ładowania       | AUTO-STOP Charge             | Sekcja 3.3 (identyczna)          |
| Klik "Wymuś Rozładowanie"          | START Discharge              | Sekcja 4.1                       |
| Klik "STOP" na rozładowaniu        | STOP Discharge               | Sekcja 4.3                       |
| SOC <= 20% podczas rozładowania    | AUTO-STOP Discharge          | Sekcja 4.3 (identyczna)          |
| SOC <= 5% (krytyczny)              | EMERGENCY STOP               | Sekcja 5                         |
| Timeout 6h                         | STOP odpowiedni              | Sekcja 3.3 lub 4.3               |
| Coś poszło nie tak                 | EMERGENCY STOP               | Sekcja 5                         |
| Autopilot: "wróć do normalnego"    | Powrót do trybu G13          | **Sekcja 5A.3**                  |
| Nocne ładowanie zakończone         | STOP eco_charge + powrót G13 | **Sekcja 5A.3** (charge wg pory) |
| Force zakończony w godz. sprzedaży | STOP + charge_current="0"    | **Sekcja 5A.6**                  |
| Force zakończony w off-peak        | STOP + charge_current="18.5" | **Sekcja 5A.6**                  |
