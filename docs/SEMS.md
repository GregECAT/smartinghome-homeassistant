# HEMS — Home Energy Management System

> **GoodWe BT 3-fazowy | Lynx Home U 10.2 kWh | Taryfa G13 Tauron | RCE PSE**
>
> Wersja: 2.0 | Data: 21.03.2026 | Lokalizacja: Łąkowa 13, Bobrek, Polska

---

## Spis treści

- [1. Architektura systemu](#1-architektura-systemu)
- [2. Kompletna mapa sensorów](#2-kompletna-mapa-sensorów)
  - [2.1 PV (Fotowoltaika)](#21-pv-fotowoltaika)
  - [2.2 Sieć — sensory z integracji (BŁĘDNE)](#22-sieć--sensory-z-integracji-błędne)
  - [2.3 Sieć — sensory NAPRAWIONE (Riemann sum)](#23-sieć--sensory-naprawione-riemann-sum)
  - [2.4 Bateria](#24-bateria)
  - [2.5 Obciążenie (Load)](#25-obciążenie-load)
  - [2.6 Falownik](#26-falownik)
  - [2.7 Sterowanie falownikiem](#27-sterowanie-falownikiem)
  - [2.8 Rejestry Modbus RS485](#28-rejestry-modbus-rs485)
  - [2.9 Prognoza PV (Forecast.Solar)](#29-prognoza-pv-forecastsolar)
  - [2.10 RCE PSE (ceny energii)](#210-rce-pse-ceny-energii)
  - [2.11 Template sensory (custom)](#211-template-sensory-custom)
  - [2.12 Utility meters](#212-utility-meters)
  - [2.13 HomeKit Live (opcjonalne)](#213-homekit-live-opcjonalne)
- [3. Taryfa G13 Tauron 2026](#3-taryfa-g13-tauron-2026)
- [4. Net-billing (sprzedaż do sieci)](#4-net-billing-sprzedaż-do-sieci)
- [5. Strategia HEMS G13+RCE](#5-strategia-hems-g13rce)
- [6. Automatyzacje](#6-automatyzacje)
- [7. Komendy sterujące](#7-komendy-sterujące)
- [8. Dashboard](#8-dashboard)
- [9. Pliki konfiguracyjne](#9-pliki-konfiguracyjne)
- [10. Znane problemy i bugi](#10-znane-problemy-i-bugi)
- [11. Historia zmian](#11-historia-zmian)

---

## 1. Architektura systemu

### 1.1 Sprzęt

| Komponent       | Model / Specyfikacja                 | Uwagi                                          |
| --------------- | ------------------------------------ | ---------------------------------------------- |
| Falownik        | GoodWe BT, 3-fazowy, hybrydowy       | Komunikacja UDP + RS485 Modbus                 |
| MPPT            | 2 stringi (PV1 + PV2)                | Dwa dachy, różne orientacje                    |
| Bateria         | Lynx Home U, 10.2 kWh                | LiFePO4, DOD 95%, min SOC 5%                   |
| Smart Meter     | 3-fazowy, zintegrowany z falownikiem | Pomiar na przyłączu sieciowym                  |
| Modbus RS485    | FTDI FT232R USB-UART                 | Slave 247, 9600 baud, 8N1                      |
| Bojler          | 3800 W (faza L1)                     | `switch.bojler_3800`                           |
| Klimatyzacja    | ~1500 W (faza L2)                    | `switch.klimatyzacja_socket_1`                 |
| Gniazdko 2      | zmienna (faza L3)                    | `switch.drugie_gniazdko`                       |
| Czujnik zalania | Zigbee, piwnica                      | Steruje pompą: `switch.pompa_zalania_socket_1` |

### 1.2 Oprogramowanie

| Komponent             | Wersja / Typ                                            |
| --------------------- | ------------------------------------------------------- |
| Home Assistant        | Najnowsza (OS)                                          |
| Integracja GoodWe     | **HACS mletenay (experimental)** — NIE natywna HA       |
| Komunikacja falownika | UDP (integracja) + RS485 Modbus (sensory diagnostyczne) |
| Karty dashboardu      | Sunsynk Power Flow Card, Lumina Energy Card, ApexCharts |
| Prognoza PV           | Forecast.Solar (2 instancje — 2 dachy)                  |
| Ceny energii RCE      | ha-rce-pse v2 (HACS)                                    |
| Pogoda                | AccuWeather                                             |

### 1.3 Kluczowe odkrycia techniczne

> ⚠️ **KRYTYCZNE** — poniższe problemy zostały odkryte podczas testów 19–21.03.2026

| Problem                        | Szczegóły                                                                                                                                 | Rozwiązanie                                                     |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| **Rejestry Modbus BT ≠ ET**    | Rejestr 47511 (EMS Mode) na BT ma **ODWROTNE** znaczenie niż dokumentacja ET. `Force Discharge` (value=4) powoduje **ŁADOWANIE** z sieci. | Używaj `goodwe.set_parameter` zamiast surowego Modbus write     |
| **Natywna integracja HA**      | Nadpisuje komendy Modbus co ~30s                                                                                                          | Migracja na HACS mletenay (experimental)                        |
| **eco_charge / eco_discharge** | Oba powodują ładowanie z sieci (~770 W) bo sloty Eco Mode są domyślnie na charge                                                          | Używaj `battery_charge_current = "0"/"18.5"` w trybie `general` |
| **Export limit = 0**           | Blokuje cały eksport niezależnie od trybu                                                                                                 | Ustawiony na 16000 W                                            |
| **set_parameter wartości**     | Muszą być **STRING**: `value: "0"` nie `value: 0`                                                                                         | Zawsze używaj cudzysłowów                                       |
| **Sensory energii BŁĘDNE**     | `today_energy_export` = Produkcja AC (NIE eksport!). `today_energy_import` 27× za mały.                                                   | Riemann sum integration + diagnostyka Modbus                    |

---

## 2. Kompletna mapa sensorów

### 2.1 PV (Fotowoltaika)

| Encja HA                       | Opis                                | Jedn. | Źródło            |
| ------------------------------ | ----------------------------------- | ----- | ----------------- |
| `sensor.pv_power`              | Całkowita moc PV (oba MPPT)         | W     | Integracja GoodWe |
| `sensor.pv1_power`             | Moc PV string 1                     | W     | Integracja GoodWe |
| `sensor.pv2_power`             | Moc PV string 2                     | W     | Integracja GoodWe |
| `sensor.pv1_voltage`           | Napięcie string 1                   | V     | Integracja GoodWe |
| `sensor.pv2_voltage`           | Napięcie string 2                   | V     | Integracja GoodWe |
| `sensor.pv1_current`           | Prąd string 1                       | A     | Integracja GoodWe |
| `sensor.pv2_current`           | Prąd string 2                       | A     | Integracja GoodWe |
| `sensor.today_s_pv_generation` | Produkcja PV dziś                   | kWh   | Integracja GoodWe |
| `sensor.total_pv_generation`   | Produkcja PV lifetime (kumulatywny) | kWh   | Integracja GoodWe |

### 2.2 Sieć — sensory z integracji (BŁĘDNE)

> ⚠️ **UWAGA:** Sensory `today_energy_export` i `today_energy_import` z integracji HACS mletenay dają **błędne** wartości dla modelu GoodWe BT. Patrz [sekcja 10](#10-znane-problemy-i-bugi).

**Konwencja znaku `meter_active_power_total`:** POZYTYWNA = import z sieci, NEGATYWNA = eksport do sieci.

| Encja HA                          | Opis                                                 | Jedn. | Status |
| --------------------------------- | ---------------------------------------------------- | ----- | ------ |
| `sensor.meter_active_power_total` | Moc sieci total (3 fazy)                             | W     | ✅ OK  |
| `sensor.meter_active_power_l1`    | Moc sieci faza L1                                    | W     | ✅ OK  |
| `sensor.meter_active_power_l2`    | Moc sieci faza L2                                    | W     | ✅ OK  |
| `sensor.meter_active_power_l3`    | Moc sieci faza L3                                    | W     | ✅ OK  |
| `sensor.on_grid_l1_voltage`       | Napięcie fazy L1                                     | V     | ✅ OK  |
| `sensor.on_grid_l2_voltage`       | Napięcie fazy L2                                     | V     | ✅ OK  |
| `sensor.on_grid_l3_voltage`       | Napięcie fazy L3                                     | V     | ✅ OK  |
| `sensor.on_grid_l1_frequency`     | Częstotliwość L1                                     | Hz    | ✅ OK  |
| `sensor.on_grid_l2_frequency`     | Częstotliwość L2                                     | Hz    | ✅ OK  |
| `sensor.on_grid_l3_frequency`     | Częstotliwość L3                                     | Hz    | ✅ OK  |
| `sensor.today_energy_export`      | ❌ **BŁĘDNY:** Produkcja AC falownika (NIE eksport!) | kWh   | 🐛 BUG |
| `sensor.today_energy_import`      | ❌ **BŁĘDNY:** ~27× za mały import                   | kWh   | 🐛 BUG |
| `sensor.total_energy_export`      | ❌ **BŁĘDNY:** = total PV AC output (~9003 kWh)      | kWh   | 🐛 BUG |
| `sensor.total_energy_import`      | ❌ **BŁĘDNY:** ~103 kWh lifetime (absurd)            | kWh   | 🐛 BUG |

### 2.3 Sieć — sensory NAPRAWIONE (Riemann sum)

Nowe sensory bazują na całkowaniu `meter_active_power_total` w czasie (metoda trapezoidalna).

| Encja HA                     | Opis                           | Jedn. | Źródło                     |
| ---------------------------- | ------------------------------ | ----- | -------------------------- |
| `sensor.grid_import_power`   | Moc importu (kierunkowa, ≥0)   | W     | Template: `max(meter, 0)`  |
| `sensor.grid_export_power`   | Moc eksportu (kierunkowa, ≥0)  | W     | Template: `max(-meter, 0)` |
| `sensor.grid_import_energy`  | Energia importu (Riemann sum)  | kWh   | `platform: integration`    |
| `sensor.grid_export_energy`  | Energia eksportu (Riemann sum) | kWh   | `platform: integration`    |
| `sensor.grid_import_daily`   | Import dziś                    | kWh   | `utility_meter: daily`     |
| `sensor.grid_export_daily`   | Eksport dziś                   | kWh   | `utility_meter: daily`     |
| `sensor.grid_import_weekly`  | Import tygodniowy              | kWh   | `utility_meter: weekly`    |
| `sensor.grid_export_weekly`  | Eksport tygodniowy             | kWh   | `utility_meter: weekly`    |
| `sensor.grid_import_monthly` | Import miesięczny              | kWh   | `utility_meter: monthly`   |
| `sensor.grid_export_monthly` | Eksport miesięczny             | kWh   | `utility_meter: monthly`   |
| `sensor.grid_import_yearly`  | Import roczny                  | kWh   | `utility_meter: yearly`    |
| `sensor.grid_export_yearly`  | Eksport roczny                 | kWh   | `utility_meter: yearly`    |

```yaml
# configuration.yaml — Riemann sum integration
sensor:
  - platform: integration
    source: sensor.grid_import_power
    name: grid_import_energy
    unique_id: grid_import_energy_riemann
    unit_prefix: k
    round: 2
    method: trapezoidal

  - platform: integration
    source: sensor.grid_export_power
    name: grid_export_energy
    unique_id: grid_export_energy_riemann
    unit_prefix: k
    round: 2
    method: trapezoidal
```

### 2.4 Bateria

| Encja HA                         | Opis                              | Jedn. |
| -------------------------------- | --------------------------------- | ----- |
| `sensor.battery_state_of_charge` | SOC baterii                       | %     |
| `sensor.battery_power`           | Moc baterii (+charge, −discharge) | W     |
| `sensor.battery_voltage`         | Napięcie baterii                  | V     |
| `sensor.battery_current`         | Prąd baterii                      | A     |
| `sensor.battery_temperature`     | Temperatura baterii               | °C    |
| `sensor.battery_mode`            | Tryb baterii (tekst)              | —     |
| `sensor.today_battery_charge`    | Energia ładowania dziś            | kWh   |
| `sensor.today_battery_discharge` | Energia rozładowania dziś         | kWh   |

### 2.5 Obciążenie (Load)

| Encja HA            | Opis                          | Jedn. |
| ------------------- | ----------------------------- | ----- |
| `sensor.load`       | Całkowite obciążenie domu     | W     |
| `sensor.load_l1`    | Obciążenie faza L1 (Bojler)   | W     |
| `sensor.load_l2`    | Obciążenie faza L2 (Klima)    | W     |
| `sensor.load_l3`    | Obciążenie faza L3 (Gniazdko) | W     |
| `sensor.today_load` | Zużycie dziś total            | kWh   |

### 2.6 Falownik

| Encja HA                          | Opis                  | Jedn. |
| --------------------------------- | --------------------- | ----- |
| `sensor.work_mode`                | Tryb pracy falownika  | —     |
| `sensor.warning_code`             | Kod ostrzeżenia       | —     |
| `sensor.ups_load`                 | Obciążenie UPS        | %     |
| `sensor.inverter_temperature_air` | Temperatura falownika | °C    |

### 2.7 Sterowanie falownikiem

| Encja / Komenda                                | Opis                                | Opcje / Zakres                                                          |
| ---------------------------------------------- | ----------------------------------- | ----------------------------------------------------------------------- |
| `select.goodwe_tryb_pracy_falownika`           | Tryb pracy                          | general, off_grid, backup, eco, peak_shaving, eco_charge, eco_discharge |
| `number.goodwe_grid_export_limit`              | Limit eksportu (max 10000 w HA GUI) | 0–10000 W                                                               |
| `number.goodwe_depth_of_discharge_on_grid`     | DOD on-grid                         | 0–99%                                                                   |
| `goodwe.set_parameter: battery_charge_current` | Prąd ładowania baterii              | `"0"` = blokada, `"18.5"` = max                                         |
| `goodwe.set_parameter: grid_export_limit`      | Export limit (poza GUI)             | `"0"`–`"16000"` W (string!)                                             |

> **Device ID falownika:** `02592f41265ac022d0c8b8aa99728b3e`

### 2.8 Rejestry Modbus RS485

**Połączenie:** `/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_BG02YUXJ-if00-port0` | Slave 247 | 9600 baud | 8N1

| Rejestr     | Encja HA                           | Opis               | Format                  | Uwagi                       |
| ----------- | ---------------------------------- | ------------------ | ----------------------- | --------------------------- |
| 35137       | `sensor.gw_pv_power`               | PV Power           | uint32, byte swap, ×0.1 | Moc PV (W)                  |
| 35139       | `sensor.gw_grid_power`             | Grid Power         | int32                   | Moc sieci (W)               |
| 35183       | `sensor.gw_battery_soc`            | Battery SOC        | uint16, ×0.01           | SOC (%)                     |
| 35191       | `gw_diag_meter_export_day_a`       | Eksport dzienny?   | uint32, ×0.1            | 🔍 DIAGNOSTYKA              |
| 35193       | `gw_diag_meter_import_day_a`       | Import dzienny?    | uint32, ×0.1            | 🔍 DIAGNOSTYKA              |
| 35195       | `gw_diag_meter_export_total_a`     | Eksport total?     | uint32, ×0.1            | ⚠️ = PV AC output!          |
| 35197       | `gw_diag_meter_import_total_a`     | Import total?      | uint32, ×0.1            | 🔍 DIAGNOSTYKA              |
| 35199       | `gw_diag_meter_export_total_b`     | Eksport total alt? | uint32, ×0.1            | 🔍 DIAGNOSTYKA              |
| 35201       | `gw_diag_meter_import_total_b`     | Import total alt?  | uint32, ×0.1            | 🔍 DIAGNOSTYKA              |
| 47500       | `gw_modbus_battery_soc_protection` | SOC Protection     | uint16                  | %                           |
| 47501       | `gw_modbus_dod_on_grid`            | DOD On Grid        | uint16                  | Packed data — NIE dodawaj % |
| 47502       | `gw_modbus_dod_off_grid`           | DOD Off Grid       | uint16                  | %                           |
| 47509       | `gw_modbus_grid_export_enabled`    | Export Enabled     | uint16                  | 0/1                         |
| 47510       | `gw_modbus_grid_export_limit`      | Export Limit       | uint16                  | W                           |
| 47511       | `gw_modbus_ems_mode`               | EMS Mode           | uint16                  | ⚠️ BT≠ET! 4=charge!         |
| 47512       | `gw_modbus_ems_power_limit`        | EMS Power Limit    | uint16                  | W                           |
| 47518–47530 | `gw_modbus_eco_mode_1–4_switch`    | Eco Mode sloty     | uint16                  | Nie działają na BT          |

### 2.9 Prognoza PV (Forecast.Solar)

| Encja HA                                     | Opis                                  | Jedn. |
| -------------------------------------------- | ------------------------------------- | ----- |
| `sensor.power_production_now`                | Prognoza mocy teraz (dach 1)          | W     |
| `sensor.power_production_now_2`              | Prognoza mocy teraz (dach 2)          | W     |
| `sensor.energy_production_today`             | Prognoza produkcji dziś (dach 1)      | kWh   |
| `sensor.energy_production_today_2`           | Prognoza produkcji dziś (dach 2)      | kWh   |
| `sensor.energy_production_today_remaining`   | Pozostała produkcja dziś (dach 1)     | kWh   |
| `sensor.energy_production_today_remaining_2` | Pozostała produkcja dziś (dach 2)     | kWh   |
| `sensor.energy_production_tomorrow`          | Prognoza produkcji jutro (dach 1)     | kWh   |
| `sensor.energy_production_tomorrow_2`        | Prognoza produkcji jutro (dach 2)     | kWh   |
| `sensor.energy_current_hour` / `_2`          | Produkcja ta godzina (dach 1/2)       | kWh   |
| `sensor.energy_next_hour` / `_2`             | Produkcja następna godzina (dach 1/2) | kWh   |

**Template sensory sumujące (oba dachy):**

| Encja HA                                   | Opis                            | Jedn. |
| ------------------------------------------ | ------------------------------- | ----- |
| `sensor.pv_forecast_power_now_total`       | Suma prognoz mocy obu dachów    | W     |
| `sensor.pv_forecast_today_total`           | Suma prognoz energii obu dachów | kWh   |
| `sensor.pv_forecast_remaining_today_total` | Pozostała suma obu dachów       | kWh   |
| `sensor.pv_forecast_tomorrow_total`        | Prognoza jutro suma obu dachów  | kWh   |
| `sensor.pv_forecast_accuracy_today`        | Dokładność prognozy dziś        | %     |

### 2.10 RCE PSE (ceny energii)

> **Integracja:** ha-rce-pse v2 (HACS) | **Źródło:** API PSE (Polskie Sieci Elektroenergetyczne) | **Ceny bazowe:** PLN/MWh

#### Ceny bazowe

| Encja HA                                  | Opis                          | Jedn.   |
| ----------------------------------------- | ----------------------------- | ------- |
| `sensor.rce_pse_cena`                     | Bieżąca cena RCE              | PLN/MWh |
| `sensor.rce_pse_cena_za_kwh`              | Bieżąca cena prosumenta z VAT | PLN/kWh |
| `sensor.rce_pse_cena_sprzedazy_prosument` | Cena sprzedaży prosumenta     | PLN/MWh |
| `sensor.rce_pse_cena_nastepnej_godziny`   | Cena +1h                      | PLN/MWh |
| `sensor.rce_pse_cena_za_2_godziny`        | Cena +2h                      | PLN/MWh |
| `sensor.rce_pse_cena_za_3_godziny`        | Cena +3h                      | PLN/MWh |
| `sensor.rce_pse_cena_poprzedniej_godziny` | Cena −1h                      | PLN/MWh |
| `sensor.rce_pse_cena_jutro`               | Cena jutro                    | PLN/MWh |

#### Statystyki dzienne

| Encja HA                                     | Opis                 | Jedn.   |
| -------------------------------------------- | -------------------- | ------- |
| `sensor.rce_pse_srednia_cena_dzisiaj`        | Średnia cena dziś    | PLN/MWh |
| `sensor.rce_pse_minimalna_cena_dzisiaj`      | Minimalna cena dziś  | PLN/MWh |
| `sensor.rce_pse_maksymalna_cena_dzisiaj`     | Maksymalna cena dziś | PLN/MWh |
| `sensor.rce_pse_mediana_cen_dzisiaj`         | Mediana cen dziś     | PLN/MWh |
| `sensor.rce_pse_aktualna_vs_srednia_dzisiaj` | % różnica vs średnia | %       |
| `sensor.rce_pse_srednia_cena_jutro`          | Średnia cena jutro   | PLN/MWh |
| `sensor.rce_pse_minimalna_cena_jutro`        | Min jutro            | PLN/MWh |
| `sensor.rce_pse_maksymalna_cena_jutro`       | Max jutro            | PLN/MWh |
| `sensor.rce_pse_jutro_vs_dzisiaj_srednia`    | Jutro vs dziś (%)    | %       |

#### Okna czasowe

| Encja HA                                                     | Opis                                       |
| ------------------------------------------------------------ | ------------------------------------------ |
| `sensor.rce_pse_najtansze_okno_czasowe_dzisiaj`              | Najtańsze okno dziś (np. `12:00 - 13:00`)  |
| `sensor.rce_pse_najdrozsze_okno_czasowe_dzisiaj`             | Najdroższe okno dziś (np. `18:00 - 19:00`) |
| `sensor.rce_pse_najtansze_okno_czasowe_jutro`                | Najtańsze okno jutro                       |
| `sensor.rce_pse_najdrozsze_okno_czasowe_jutro`               | Najdroższe okno jutro                      |
| `sensor.rce_pse_konfigurowalne_najtansze_okno_dzisiaj`       | Konfigurowalne najtańsze (zakres)          |
| `sensor.rce_pse_konfigurowalne_najdrozsze_okno_dzisiaj`      | Konfigurowalne najdroższe (zakres)         |
| `sensor.rce_pse_konfigurowalne_najtansze_okno_jutro`         | Konfigurowalne najtańsze jutro             |
| `sensor.rce_pse_konfigurowalne_najdrozsze_okno_jutro`        | Konfigurowalne najdroższe jutro            |
| `sensor.rce_pse_kompas_energetyczny_dzisiaj`                 | Kompas PSE (zalecenie)                     |
| `sensor.rce_pse_poczatek_godziny_min_ceny_dzisiaj`           | Początek min ceny dziś                     |
| `sensor.rce_pse_koniec_godziny_min_ceny_dzisiaj`             | Koniec min ceny dziś                       |
| `sensor.rce_pse_poczatek_godziny_maks_ceny_dzisiaj`          | Początek max ceny dziś                     |
| `sensor.rce_pse_koniec_godziny_maks_ceny_dzisiaj`            | Koniec max ceny dziś                       |
| `sensor.rce_pse_poczatek_drugiego_najdrozszego_okna_dzisiaj` | Drugie najdroższe okno start               |
| `sensor.rce_pse_koniec_drugiego_najdrozszego_okna_dzisiaj`   | Drugie najdroższe okno koniec              |
| `sensor.rce_pse_cena_ponizej_progu_poczatek_dzisiaj`         | Poniżej progu start dziś                   |
| `sensor.rce_pse_cena_ponizej_progu_koniec_dzisiaj`           | Poniżej progu koniec dziś                  |

#### Binary sensory (on/off)

| Encja HA                                                               | Opis                        | Użycie w HEMS                  |
| ---------------------------------------------------------------------- | --------------------------- | ------------------------------ |
| `binary_sensor.rce_pse_aktywne_najtansze_okno_dzisiaj`                 | Czy teraz najtańsze okno?   | Trigger: ładuj baterię         |
| `binary_sensor.rce_pse_aktywne_najdrozsze_okno_dzisiaj`                | Czy teraz najdroższe okno?  | Trigger: alert max oszczędność |
| `binary_sensor.rce_pse_aktywne_konfigurowalne_najtansze_okno_dzisiaj`  | Konfig. najtańsze aktywne?  | Dodatkowy trigger              |
| `binary_sensor.rce_pse_aktywne_konfigurowalne_najdrozsze_okno_dzisiaj` | Konfig. najdroższe aktywne? | Dodatkowy trigger              |
| `binary_sensor.rce_pse_aktywne_drugie_najdrozsze_okno_dzisiaj`         | Drugie najdroższe aktywne?  | —                              |
| `binary_sensor.rce_pse_cena_ponizej_progu`                             | Cena poniżej progu?         | —                              |

### 2.11 Template sensory (custom)

#### Moc kierunkowa i walidacja

| Encja HA                              | Opis                          | Jedn. |
| ------------------------------------- | ----------------------------- | ----- |
| `sensor.grid_import_power`            | Moc importu z sieci (≥0)      | W     |
| `sensor.grid_export_power`            | Moc eksportu do sieci (≥0)    | W     |
| `sensor.energy_balance_check`         | Walidacja bilansu energii     | kWh   |
| `sensor.energy_fix_validation_export` | Porównanie OLD vs NEW eksport | —     |
| `sensor.energy_fix_validation_import` | Porównanie OLD vs NEW import  | —     |

#### Obliczenia HEMS

| Encja HA                                 | Opis                       | Jedn. |
| ---------------------------------------- | -------------------------- | ----- |
| `sensor.hems_pv_surplus_power`           | Nadwyżka PV                | W     |
| `sensor.goodwe_minimum_soc_on_grid`      | Min SOC on-grid (100−DOD)  | %     |
| `sensor.goodwe_grid_frequency_average`   | Średnia częstotliwość faz  | Hz    |
| `sensor.goodwe_load_balance_difference`  | Nierównowaga faz           | W     |
| `sensor.goodwe_battery_runtime`          | Runtime baterii            | h     |
| `sensor.goodwe_battery_energy_available` | Energia dostępna w baterii | kWh   |
| `sensor.goodwe_system_status`            | Status systemu (tekst)     | —     |
| `sensor.goodwe_export_status`            | Status eksportu            | —     |
| `sensor.hems_active_loads`               | Aktywne odbiorniki (N/3)   | —     |

#### Statystyki dzienne (NAPRAWIONE)

| Encja HA                                       | Opis                | Jedn. |
| ---------------------------------------------- | ------------------- | ----- |
| `sensor.goodwe_autarky_today`                  | Autarkia dziś       | %     |
| `sensor.goodwe_self_consumption_today`         | Autokonsumpcja dziś | %     |
| `sensor.goodwe_home_consumption_from_pv_today` | Zużycie z PV dziś   | kWh   |
| `sensor.goodwe_net_grid_today`                 | Bilans sieci dziś   | kWh   |

#### G13 Taryfa

| Encja HA                       | Opis                                                    | Jedn.  |
| ------------------------------ | ------------------------------------------------------- | ------ |
| `sensor.g13_current_zone`      | Strefa G13 (pozaszczytowa/przedpołudniowa/popołudniowa) | —      |
| `sensor.g13_buy_price`         | Cena kupna G13                                          | zł/kWh |
| `sensor.g13_is_afternoon_peak` | Czy teraz szczyt popołudniowy                           | on/off |
| `sensor.g13_is_off_peak`       | Czy teraz off-peak                                      | on/off |

#### RCE przeliczone (zł/kWh)

| Encja HA                          | Opis                                     | Jedn.  |
| --------------------------------- | ---------------------------------------- | ------ |
| `sensor.rce_sell_price`           | Cena sprzedaży RCE prosument             | zł/kWh |
| `sensor.rce_sell_price_next_hour` | Cena RCE +1h                             | zł/kWh |
| `sensor.rce_sell_price_2h`        | Cena RCE +2h                             | zł/kWh |
| `sensor.rce_sell_price_3h`        | Cena RCE +3h                             | zł/kWh |
| `sensor.rce_average_today`        | Średnia RCE dziś                         | zł/kWh |
| `sensor.rce_min_today`            | Min RCE dziś                             | zł/kWh |
| `sensor.rce_max_today`            | Max RCE dziś                             | zł/kWh |
| `sensor.g13_rce_spread`           | Spread G13 kupno vs RCE sprzedaż         | zł/kWh |
| `sensor.rce_price_trend`          | Trend cenowy (rośnie/spada/stabilna)     | —      |
| `sensor.rce_good_sell`            | Ocena RCE (excellent/good/poor/terrible) | —      |

#### Rekomendacje i tryby

| Encja HA                              | Opis                                           |
| ------------------------------------- | ---------------------------------------------- |
| `sensor.hems_rce_recommendation`      | Rekomendacja HEMS (tekst z logiką RCE+G13+SOC) |
| `sensor.hems_mode`                    | Tryb HEMS (tekst)                              |
| `sensor.goodwe_ems_mode`              | EMS Mode (tekst z Modbus)                      |
| `sensor.goodwe_export_limit_watchdog` | Watchdog: HA vs Modbus export limit            |

#### Ekonomia (finanse)

| Encja HA                                      | Okres   | Opis                              |
| --------------------------------------------- | ------- | --------------------------------- |
| `sensor.g13_import_cost_today`                | Dziś    | Koszt importu (zł)                |
| `sensor.g13_export_revenue_today`             | Dziś    | Przychód z eksportu (zł)          |
| `sensor.g13_self_consumption_savings_today`   | Dziś    | Oszczędność z autokonsumpcji (zł) |
| `sensor.g13_net_balance_today`                | Dziś    | Bilans netto (zł)                 |
| `sensor.g13_battery_arbitrage_potential`      | —       | Potencjał arbitrażu baterii (zł)  |
| `sensor.g13_import_cost_weekly`               | Tydzień | Koszt importu                     |
| `sensor.g13_export_revenue_weekly`            | Tydzień | Przychód                          |
| `sensor.g13_self_consumption_savings_weekly`  | Tydzień | Oszczędność                       |
| `sensor.g13_net_balance_weekly`               | Tydzień | Bilans                            |
| `sensor.g13_import_cost_monthly`              | Miesiąc | Koszt importu                     |
| `sensor.g13_export_revenue_monthly`           | Miesiąc | Przychód                          |
| `sensor.g13_self_consumption_savings_monthly` | Miesiąc | Oszczędność                       |
| `sensor.g13_net_balance_monthly`              | Miesiąc | Bilans                            |
| `sensor.g13_import_cost_yearly`               | Rok     | Koszt importu                     |
| `sensor.g13_export_revenue_yearly`            | Rok     | Przychód                          |
| `sensor.g13_self_consumption_savings_yearly`  | Rok     | Oszczędność                       |
| `sensor.g13_net_balance_yearly`               | Rok     | Bilans                            |

#### Autarkia i self-consumption (okresy)

| Encja HA                          | Opis                            |
| --------------------------------- | ------------------------------- |
| `sensor.autarky_weekly`           | Autarkia tygodniowa (%)         |
| `sensor.autarky_monthly`          | Autarkia miesięczna (%)         |
| `sensor.self_consumption_weekly`  | Self-consumption tygodniowe (%) |
| `sensor.self_consumption_monthly` | Self-consumption miesięczne (%) |

### 2.12 Utility meters

| Encja HA                     | Źródło                       | Cykl    |
| ---------------------------- | ---------------------------- | ------- |
| `sensor.pv_weekly`           | `sensor.total_pv_generation` | weekly  |
| `sensor.pv_monthly`          | `sensor.total_pv_generation` | monthly |
| `sensor.pv_yearly`           | `sensor.total_pv_generation` | yearly  |
| `sensor.grid_export_daily`   | `sensor.grid_export_energy`  | daily   |
| `sensor.grid_export_weekly`  | `sensor.grid_export_energy`  | weekly  |
| `sensor.grid_export_monthly` | `sensor.grid_export_energy`  | monthly |
| `sensor.grid_export_yearly`  | `sensor.grid_export_energy`  | yearly  |
| `sensor.grid_import_daily`   | `sensor.grid_import_energy`  | daily   |
| `sensor.grid_import_weekly`  | `sensor.grid_import_energy`  | weekly  |
| `sensor.grid_import_monthly` | `sensor.grid_import_energy`  | monthly |
| `sensor.grid_import_yearly`  | `sensor.grid_import_energy`  | yearly  |

### 2.13 HomeKit Live (opcjonalne)

| Encja HA                           | Opis                    |
| ---------------------------------- | ----------------------- |
| `sensor.goodwe_pv_power_live`      | PV Power z HomeKit      |
| `sensor.goodwe_grid_power_live`    | Grid Power z HomeKit    |
| `sensor.goodwe_load_power_live`    | Load Power z HomeKit    |
| `sensor.goodwe_battery_soc_live`   | Battery SOC z HomeKit   |
| `sensor.goodwe_battery_power_live` | Battery Power z HomeKit |

---

## 3. Taryfa G13 Tauron 2026

### ZIMA (październik–marzec)

| Strefa                 | Godziny (pon–pt)         | Cena brutto     |
| ---------------------- | ------------------------ | --------------- |
| Szczyt przedpołudniowy | 07:00–13:00              | **0.91 zł/kWh** |
| Szczyt popołudniowy    | 16:00–21:00              | **1.50 zł/kWh** |
| Pozaszczytowa          | 13:00–16:00, 21:00–07:00 | **0.63 zł/kWh** |

### LATO (kwiecień–wrzesień)

| Strefa                 | Godziny (pon–pt)         | Cena brutto     |
| ---------------------- | ------------------------ | --------------- |
| Szczyt przedpołudniowy | 07:00–13:00              | **0.91 zł/kWh** |
| Szczyt popołudniowy    | 19:00–22:00              | **1.50 zł/kWh** |
| Pozaszczytowa          | 13:00–19:00, 22:00–07:00 | **0.63 zł/kWh** |

> **WEEKENDY + ŚWIĘTA:** Cała doba → 0.63 zł/kWh (pozaszczytowa)

### Składowe rachunku G13 (4000 kWh/rok, 2026)

| Opłata                    | Kwota roczna                        |
| ------------------------- | ----------------------------------- |
| Energia czynna (3 strefy) | ~2 283 zł                           |
| Przesył (3 strefy)        | ~410 zł                             |
| Opłata jakościowa         | 163 zł                              |
| Opłata kogeneracyjna      | 21 zł                               |
| Opłata OZE                | 36 zł                               |
| Opłata mocowa             | 355 zł                              |
| Opłata abonamentowa       | 67 zł                               |
| Opłata sieciowa stała     | 160 zł                              |
| **SUMA**                  | **~3 496 zł (0.87 zł/kWh średnio)** |

---

## 4. Net-billing (sprzedaż do sieci)

Od 2026 cena sprzedaży = **RCE × 1.23** (współczynnik). Środki trafiają do depozytu prosumenckiego.

| Pora     | Godziny     | Typowa cena RCE  | Strategia                               |
| -------- | ----------- | ---------------- | --------------------------------------- |
| Południe | 10:00–16:00 | 0.10–0.30 zł/kWh | ❌ NIE sprzedawaj — ładuj baterię       |
| Wieczór  | 17:00–22:00 | 0.60–1.20 zł/kWh | ✅ SPRZEDAWAJ (najlepsza pora)          |
| Rano     | 06:00–09:00 | 0.50–0.90 zł/kWh | ✅ Sprzedawaj jeśli masz baterię        |
| Noc      | 22:00–06:00 | 0.30–0.70 zł/kWh | 🔋 Ładuj baterię z sieci (G13 off-peak) |

---

## 5. Strategia HEMS G13+RCE

### 5.1 Harmonogram dobowy (dni robocze)

| Godzina     | Tryb         | Akcja                                        | Cena G13 |
| ----------- | ------------ | -------------------------------------------- | -------- |
| 07:00–13:00 | ☀️ SPRZEDAŻ  | `battery_charge_current="0"`, export 16 kW   | 0.91 zł  |
| 13:00–16/19 | 🔋 ŁADOWANIE | `battery_charge_current="18.5"`, PV→bateria  | 0.63 zł  |
| 16/19–21/22 | 💰 SZCZYT    | Bateria zasila dom (oszczędność 1.50 zł/kWh) | 1.50 zł  |
| 22:00–07:00 | 🌙 NOC       | Bateria→dom, opcjonalnie arbitraż nocny      | 0.63 zł  |

### 5.2 Weekendy

Cała doba off-peak (0.63 zł) → ładowanie baterii + autokonsumpcja.

### 5.3 Kaskada ochrony napięcia PV

Działa sunrise–sunset, chroni PV przed odcięciem przez sieć:

| Napięcie        | Reakcja                          | Czas  |
| --------------- | -------------------------------- | ----- |
| > 252 V         | Bojler ON (3.8 kW)               | 30 s  |
| > 253 V         | + Klima ON                       | 30 s  |
| > 254 V         | + Przywróć ładowanie baterii     | 15 s  |
| < 248 V (5 min) | Wyłącz odbiorniki, przywróć tryb | 5 min |

### 5.4 Kaskada nadwyżki PV

Uruchamia się po napełnieniu baterii:

| Warunek          | Akcja                 | SOC min |
| ---------------- | --------------------- | ------- |
| Nadwyżka > 2 kW  | Bojler ON             | 80%     |
| Nadwyżka > 3 kW  | + Klima ON            | 85%     |
| Nadwyżka > 4 kW  | + Gniazdko 2 ON       | 90%     |
| Nadwyżka < 500 W | Bojler OFF            | —       |
| SOC < 50%        | Awaryjne OFF wszystko | —       |

### 5.5 Arbitraż nocny

Warunek: prognoza jutro < 8 kWh **i** SOC < 50%.

1. 23:00 → ładuj z sieci (G13 off-peak 0.63 zł/kWh)
2. Stop gdy SOC > 90% lub o 6:00
3. Rozładuj w szczycie popołudniowym (1.50 zł/kWh)

> **Zysk: 10.2 kWh × (1.50 − 0.63) = ~8.87 zł/cykl ≈ 175 zł/miesiąc**

### 5.6 Ochrona baterii

| Warunek                   | Akcja                           |
| ------------------------- | ------------------------------- |
| SOC < 20%                 | Przywróć ładowanie natychmiast  |
| Prognoza jutro < 5 kWh    | DOD → 70% (zachowaj 30% na noc) |
| PV > 500 W (po wschodzie) | DOD → 95% (przywróć normalny)   |

---

## 6. Automatyzacje

System HEMS G13+RCE v4 działa w **3 warstwach:**

- **W1 — G13 harmonogram:** 07:00 sprzedaj → 13:00 ładuj → 16–21 szczyt → noc
- **W2 — RCE dynamiczna:** binary_sensor okna + progi cenowe
- **W3 — SOC bezpieczeństwo:** 11:00/12:00 SOC check + awaryjne

| ID                                | Alias                   | Warstwa | Trigger                     | Akcja                                   |
| --------------------------------- | ----------------------- | ------- | --------------------------- | --------------------------------------- |
| `hems_morning_sell_mode`          | 07:00 sprzedaż          | W1      | time: 07:00 (pon–pt)        | `charge_current="0"`, `export=16000`    |
| `hems_midday_charge_mode`         | 13:00 ładowanie         | W1      | time: 13:00 (pon–pt)        | `charge_current="18.5"`                 |
| `hems_evening_peak_start`         | Szczyt wieczorny        | W1      | `g13_is_afternoon_peak=on`  | Powiadomienie, bateria→dom              |
| `hems_weekend_mode`               | Weekend                 | W1      | time: 07:00 (sob–nd)        | `charge_current="18.5"`, autokonsumpcja |
| `hems_rce_cheapest_window_charge` | RCE najtańsze→ładuj     | W2      | `binary najtansze=on`       | `charge_current="18.5"`                 |
| `hems_rce_cheapest_window_end`    | RCE koniec taniego      | W2      | `binary najtansze=off`      | `charge_current="0"` (przed 13:00)      |
| `hems_rce_expensive_window_alert` | RCE najdroższe→alert    | W2      | `binary najdrozsze=on`      | Powiadomienie push                      |
| `hems_rce_low_price_charge`       | RCE < 150→ładuj         | W2      | `rce_pse_cena < 150`        | `charge_current="18.5"`                 |
| `hems_rce_good_sell_price`        | RCE > 300→sprzedawaj    | W2      | `rce_pse_cena > 300`        | `charge_current="0"`                    |
| `hems_rce_peak_sell`              | RCE > 500 wieczór       | W2      | `rce_pse_cena > 500, h>=16` | Max eksport                             |
| `hems_rce_negative_price`         | RCE ujemna→ładuj+bojler | W2      | `rce_pse_cena < 0`          | Ładuj + bojler ON                       |
| `hems_soc_check_11`               | 11:00 SOC < 50%         | W3      | time: 11:00, SOC < 50%      | `charge_current="18.5"`                 |
| `hems_soc_check_12`               | 12:00 SOC < 70%         | W3      | time: 12:00, SOC < 70%      | `charge_current="18.5"`                 |
| `hems_night_arbitrage`            | Arbitraż nocny          | W1      | time: 23:00, prognoza<8kWh  | eco_charge z sieci                      |
| `hems_stop_night_charge`          | Stop nocnego            | W1      | SOC>90% lub time:06:00      | Przywróć tryb                           |
| `hems_soc_emergency`              | Ochrona SOC<20%         | W3      | SOC < 20%                   | `charge_current="18.5"` NATYCHMIAST     |

---

## 7. Komendy sterujące

### Działające (potwierdzone testami)

```yaml
# Blokada ładowania baterii (tryb sprzedaży)
action: goodwe.set_parameter
data:
  device_id: 02592f41265ac022d0c8b8aa99728b3e
  parameter: battery_charge_current
  value: "0"
```

```yaml
# Przywrócenie ładowania baterii
action: goodwe.set_parameter
data:
  device_id: 02592f41265ac022d0c8b8aa99728b3e
  parameter: battery_charge_current
  value: "18.5"
```

```yaml
# Export limit (powyżej 10000 W — wymaga set_parameter)
action: goodwe.set_parameter
data:
  device_id: 02592f41265ac022d0c8b8aa99728b3e
  parameter: grid_export_limit
  value: "16000"
```

```yaml
# Zmiana trybu pracy
action: select.select_option
target:
  entity_id: select.goodwe_tryb_pracy_falownika
data:
  option: "general"
```

```yaml
# Zmiana DOD
action: number.set_value
target:
  entity_id: number.goodwe_depth_of_discharge_on_grid
data:
  value: 95
```

### NIE działające / niebezpieczne

| Komenda                                           | Problem                                        |
| ------------------------------------------------- | ---------------------------------------------- |
| `modbus.write_register 47511=4` (Force Discharge) | Na BT powoduje **ŁADOWANIE** z sieci           |
| `modbus.write_register 47511=2` (Force Charge)    | Niespodziewane zachowanie                      |
| `eco_mode_1` via set_parameter                    | Zwraca "Unknown error" — nie obsługiwane na BT |
| `select: eco_charge / eco_discharge`              | Oba powodują ładowanie z sieci (~770 W)        |
| Dowolny zapis Modbus przy włączonej integracji HA | Integracja nadpisuje wartości                  |

---

## 8. Dashboard

### Widoki (5 zakładek)

| #   | Widok           | Opis                                                           |
| --- | --------------- | -------------------------------------------------------------- |
| 1   | ⚡ Przepływ     | Sunsynk Power Flow Card (full, wide, 3-phase)                  |
| 2   | 🏠 Lumina       | Lumina Energy Card (shimmer, popupy)                           |
| 3   | 📈 Analityka    | ApexCharts, wykresy historyczne, prognozy, Modbus, diagnostyka |
| 4   | 🤖 HEMS G13     | Sterowanie, automatyzacje, RCE, ekonomia                       |
| 5   | 💲 Ekonomia G13 | Kalkulator arbitrażu G13 (iframe HTML z live data)             |

### Sunsynk Power Flow Card — kluczowe ustawienia

```yaml
cardstyle: full
wide: true
large_font: true
invert_grid: true # meter_active_power_total ma odwróconą konwencję
inverter:
  model: goodwe_gridmode
  three_phase: true
  autarky: energy
battery:
  energy: 10200
  shutdown_soc: 5
load:
  additional_loads: 3
  load1_name: Bojler
  load2_name: Klima
  load3_name: Gniazdko 2
```

---

## 9. Pliki konfiguracyjne

### Struktura

```
/config/
├── configuration.yaml          # Modbus, template sensors (80+), integration, utility_meter, helpers
├── automations.yaml            # 18+ automatyzacji HEMS G13+RCE (3 warstwy)
├── scripts.yaml
├── scenes.yaml
├── themes/                     # Motywy dashboardu
└── www/
    ├── g13_dashboard.html      # Karta ekonomii G13 (iframe, live data, token HA)
    └── community/
        ├── lumina-energy-card/
        └── sunsynk-power-flow-card/
```

### Integracje HACS

| Integracja                     | Typ         | Repo                                   |
| ------------------------------ | ----------- | -------------------------------------- |
| GoodWe (mletenay experimental) | Integration | HACS — mletenay/goodwe                 |
| ha-rce-pse v2                  | Integration | HACS — RCE PSE                         |
| Sunsynk Power Flow Card        | Lovelace    | HACS — slipx06/sunsynk-power-flow-card |
| Lumina Energy Card             | Lovelace    | HACS                                   |
| ApexCharts Card                | Lovelace    | HACS                                   |

### Integracje natywne HA

| Integracja     | Konfiguracja                  |
| -------------- | ----------------------------- |
| Forecast.Solar | 2 instancje (dach 1 + dach 2) |
| AccuWeather    | Pogoda                        |

### Helpers (UI)

| Helper                                      | Typ     | Opis                                                      |
| ------------------------------------------- | ------- | --------------------------------------------------------- |
| `input_boolean.hems_battery_export_enabled` | Boolean | Toggle eksportu baterii do sieci                          |
| `input_select.goodwe_ems_mode_select`       | Select  | Tryb EMS: Auto/Self-use/Force charge/Force discharge/Hold |
| `input_number.goodwe_ems_power_target`      | Number  | EMS moc docelowa (0–5000 W)                               |

---

## 10. Znane problemy i bugi

| Problem                        | Status               | Opis                                                                                                                                      | Workaround                                             |
| ------------------------------ | -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------ |
| `today_energy_export` BŁĘDNY   | 🔧 W TRAKCIE NAPRAWY | Integracja mletenay czyta rejestr Produkcji AC zamiast eksportu metra. Wartość 3× za duża. Potwierdzone: wartość = "Produkcja AC" z SEMS. | Riemann sum na `meter_active_power_total`              |
| `today_energy_import` BŁĘDNY   | 🔧 W TRAKCIE NAPRAWY | 27× za mała wartość importu.                                                                                                              | Riemann sum na `meter_active_power_total`              |
| Riemann sum niedokładny        | 🧪 TESTOWANE         | Pierwszy dzień po restarcie + awaria prądu zaburzył wyniki. Potrzebny czysty dzień bez restart/awarii.                                    | Diagnostyka Modbus 35191+ z `swap:word` i `scale:0.01` |
| Rejestry Modbus metra          | 🔍 DIAGNOSTYKA       | Skan 35191–36006 ujawnił potencjalne rejestry ale wymaga testów swap/scale wariantów.                                                     | Dodano warianty word_swap + scale 0.01                 |
| Force Discharge na BT          | 🐛 ZNANY BUG         | Rejestr 47511=4 powoduje ładowanie z sieci zamiast rozładowania.                                                                          | `battery_charge_current="0"` wymusza rozładowanie      |
| Eco mode na BT                 | ❌ NIE DZIAŁA        | `eco_charge` i `eco_discharge` oba ładują z sieci                                                                                         | Nie używaj trybów eco na BT                            |
| Agregat off-grid               | ⚡ ZNANY LIMIT       | GoodWe BT blokuje ładowanie przy f > 51.5 Hz. Typowy agregat daje 51.7 Hz.                                                                | Agregat inwerterowy (Honda EU22i) lub AVR              |
| `total_energy_export` lifetime | 🐛 BUG               | 9003 kWh przy PV total 9182 kWh — fizycznie niemożliwe (97.8% eksportu). To jest PV AC output, nie eksport metra.                         | Nowe utility meters z Riemann sum                      |

---

## 11. Historia zmian

| Data       | Zmiana                                                                |
| ---------- | --------------------------------------------------------------------- |
| 18.03.2026 | Budowa dashboardu: Sunsynk + Lumina + Analityka + HEMS                |
| 18.03.2026 | Konfiguracja Modbus RS485, template sensors                           |
| 18.03.2026 | Pierwsza wersja automatyzacji HEMS                                    |
| 19.03.2026 | Test Force Discharge — odkrycie że 47511=4 ładuje z sieci na BT       |
| 19.03.2026 | Migracja na integrację HACS mletenay                                  |
| 19.03.2026 | Odkrycie `battery_charge_current = "0"/"18.5"` jako metody sterowania |
| 19.03.2026 | Odkrycie że `export_limit = 0` blokował eksport                       |
| 19.03.2026 | Kaskada ochrony napięcia (252/253/254 V)                              |
| 19.03.2026 | Wdrożenie strategii G13 z arbitrażem nocnym                           |
| 19.03.2026 | Dashboard Ekonomia G13 (iframe HTML z live data)                      |
| 20.03.2026 | Integracja RCE PSE v2 — pełne wykorzystanie sensorów cenowych         |
| 20.03.2026 | Automatyzacje W2 (RCE) — binary sensor okna, progi cenowe             |
| 20.03.2026 | Odkrycie błędu sensorów energii (HA vs SEMS)                          |
| 20.03.2026 | Diagnostyka Modbus rejestrów metra (35191–36006)                      |
| 20.03.2026 | Wdrożenie Riemann sum integration jako workaround                     |
| 20.03.2026 | Naprawa 18 template sensorów (export/import references)               |
| 20.03.2026 | Nowe utility meters (grid_export/import_daily/weekly/monthly/yearly)  |
| 21.03.2026 | Awaria prądu — test off-grid z agregatem (51.73 Hz blokada)           |
| 21.03.2026 | Walidacja Riemann sum vs SEMS (pierwszy dzień, zaburzony)             |
| 21.03.2026 | Diagnostyka Modbus v2 — dodanie swap:word + scale:0.01 wariantów      |
| 21.03.2026 | Raport systemu v2.0                                                   |

---

> **Dokument przygotowany do użycia jako baza wiedzy dla agenta budującego repozytorium HACS.**
>
> Zawiera: kompletną mapę 100+ sensorów, konfigurację Modbus RS485, strategie taryfowe G13+RCE, 18+ automatyzacji, znane bugi i workaoundy dla GoodWe BT.
