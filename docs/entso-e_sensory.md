# 📊 ENTSO-E Ceny Energii — Dokumentacja sensorów

> Pełna instrukcja działania wszystkich sensorów integracji ENTSO-E Ceny Energii by **Smarting HOME**

---

## Spis treści

1. [Architektura danych](#architektura-danych)
2. [Sensory cenowe (podstawowe)](#sensory-cenowe-podstawowe)
3. [Sensory kosztów all-in](#sensory-kosztów-all-in)
4. [Sensory okien cenowych](#sensory-okien-cenowych)
5. [Sensory analityczne (rank, delta, trend)](#sensory-analityczne)
6. [Sensory binarne (aktywne okna, trendy)](#sensory-binarne)
7. [Sensory RCE Spread](#sensory-rce-spread)
8. [Instrukcja konfiguracji RCE](#instrukcja-konfiguracji-rce)
9. [Wzory i obliczenia](#wzory-i-obliczenia)
10. [Przykłady automatyzacji](#przykłady-automatyzacji)

---

## Architektura danych

```
ENTSO-E API (XML A44)
       │
       ▼
┌─────────────────────┐
│  EntsoeApiClient    │  ← pobiera dane co 30 min
│  - fetch A44 XML    │
│  - konwersja walut  │
│  - kurs NBP         │
│  - oblicz all-in    │
│  - okna, ranki      │
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐     ┌──────────────────┐
│  EntsoeCoordinator  │────▶│  sensor.rce_pse  │
│  (DataUpdate 30min) │     │  (odczyt z HA)   │
│  + compute_spread() │◀────│                  │
└────────┬────────────┘     └──────────────────┘
         │
    ┌────┴─────┐
    ▼          ▼
 Sensors   Binary Sensors
 (22 szt)  (4 szt)
```

**Źródło danych:** ENTSO-E Transparency Platform → endpoint A44 (Day-Ahead Prices)  
**Odświeżanie:** co 30 minut (ENTSO-E API) + real-time (RCE state listener)  
**Waluta bazowa:** EUR/MWh → konwersja na PLN/kWh (lub inna konfiguracja)

---

## Sensory cenowe (podstawowe)

| Entity ID                                 | Nazwa PL                    | Opis                             | Jednostka |
| ----------------------------------------- | --------------------------- | -------------------------------- | --------- |
| `sensor.entso_e_aktualna_cena_energii`    | ⚡ Aktualna cena energii    | Cena hurtowa w bieżącej godzinie | PLN/kWh   |
| `sensor.entso_e_cena_za_nastepna_godzine` | ⏭️ Cena za następną godzinę | Cena hurtowa na kolejną godzinę  | PLN/kWh   |
| `sensor.entso_e_minimum_dzisiaj`          | 📉 Minimum dzisiaj          | Najniższa cena dnia              | PLN/kWh   |
| `sensor.entso_e_maksimum_dzisiaj`         | 📈 Maksimum dzisiaj         | Najwyższa cena dnia              | PLN/kWh   |
| `sensor.entso_e_srednia_dzisiaj`          | 📊 Średnia dzisiaj          | Średnia arytmetyczna cen dnia    | PLN/kWh   |

### Atrybuty `aktualna_cena_energii`

Sensor aktualnej ceny posiada rozbudowane atrybuty:

```yaml
prices_today: # Lista wszystkich cen na dziś
  - start: "2026-01-15T00:00:00+01:00"
    end: "2026-01-15T01:00:00+01:00"
    start_local: "00:00"
    value: 0.4523 # PLN/kWh (z VAT)
    raw_eur_mwh: 78.50 # Oryginalna cena EUR/MWh
prices_tomorrow: [...] # Dostępne ok. 13:00 CET
tomorrow_available: true
exchange_rate: 4.3215 # Kurs NBP EUR/PLN
updated_at: "2026-01-15T12:30:00+00:00"
today_min: 0.2105
today_max: 0.8734
today_avg: 0.4521
today_min_hour: "03:00"
today_max_hour: "18:00"
cheapest_hours_start: "02:00"
cheapest_hours_end: "05:00"
cheapest_hours_avg: 0.2234
cheapest_2h_start: "08:00"
cheapest_2h_end: "10:00"
cheapest_4h_start: "08:00"
cheapest_4h_end: "12:00"
most_expensive_3h_start: "18:00"
most_expensive_3h_end: "21:00"
most_expensive_2h_start: "18:00"
most_expensive_2h_end: "20:00"
rank_current_hour: 6
total_hours_today: 24
percentile_current_hour: 21.7
```

> ℹ️ **Cena hurtowa** to cena z ENTSO-E day-ahead przeliczona na PLN/kWh z uwzględnieniem VAT i kursu NBP. **NIE** zawiera marży sprzedawcy, akcyzy ani dystrybucji — do tego służą sensory all-in.

---

## Sensory kosztów all-in

Koszt „all-in" to realny koszt zakupu 1 kWh energii z sieci, uwzględniający **wszystkie składniki**.

| Entity ID                                 | Nazwa PL                    | Opis                                   |
| ----------------------------------------- | --------------------------- | -------------------------------------- |
| `sensor.entso_e_koszt_all_in_teraz`       | 💵 Koszt all-in teraz       | Pełny koszt zakupu w bieżącej godzinie |
| `sensor.entso_e_koszt_all_in_nastepna_h`  | 💵 Koszt all-in następna h  | Pełny koszt na kolejną godzinę         |
| `sensor.entso_e_koszt_all_in_min_dzisiaj` | ⬇️ Koszt all-in min dzisiaj | Najniższy pełny koszt dnia             |
| `sensor.entso_e_koszt_all_in_max_dzisiaj` | ⬆️ Koszt all-in max dzisiaj | Najwyższy pełny koszt dnia             |

### Wzór all-in

```
all_in_cost = cena_hurtowa_z_VAT + (marża + akcyza + dystrybucja) × (1 + VAT/100)
```

**Przykład** (domyślne wartości PL):

```
cena_hurtowa = 0.3500 PLN/kWh (już z 23% VAT)
marża        = 0.0500 PLN/kWh
akcyza       = 0.0050 PLN/kWh
dystrybucja  = 0.2000 PLN/kWh

all-in = 0.3500 + (0.0500 + 0.0050 + 0.2000) × 1.23
       = 0.3500 + 0.3137
       = 0.6637 PLN/kWh
```

> ⚠️ **Konfiguracja kosztów** — ustaw marżę, akcyzę i dystrybucję w ustawieniach integracji. Bez tego sensory all-in będą zbliżone do ceny hurtowej.

---

## Sensory okien cenowych

Okna cenowe to najlepsze/najgorsze **N kolejnych godzin** w ciągu dnia — kluczowe dla planowania ładowania/rozładowania baterii.

### Najtańsze okna (ładowanie)

| Entity ID                             | Nazwa PL        | Opis                               |
| ------------------------------------- | --------------- | ---------------------------------- |
| `sensor.entso_e_najtansze_2h_srednia` | 💰 Najtańsze 2h | Średnia cena w najtańszym 2h oknie |
| `sensor.entso_e_najtansze_3h_srednia` | 💰 Najtańsze 3h | Średnia cena w najtańszym 3h oknie |
| `sensor.entso_e_najtansze_4h_srednia` | 💰 Najtańsze 4h | Średnia cena w najtańszym 4h oknie |

### Najdroższe okna (sprzedaż)

| Entity ID                              | Nazwa PL         | Opis                                |
| -------------------------------------- | ---------------- | ----------------------------------- |
| `sensor.entso_e_najdrozsze_2h_srednia` | 📈 Najdroższe 2h | Średnia cena w najdroższym 2h oknie |
| `sensor.entso_e_najdrozsze_3h_srednia` | 📈 Najdroższe 3h | Średnia cena w najdroższym 3h oknie |

> Godziny start/end dla każdego okna są widoczne w atrybutach sensora `aktualna_cena_energii`.

### Algorytm

Przesuwane okno (sliding window) po wszystkich godzinach dnia:

```
for i in range(len(prices) - n + 1):
    window = prices[i:i+n]
    avg = mean(window.values)
    if avg < best_avg:  # cheapest
        best = window
```

---

## Sensory analityczne

### Ranking i percentyl

| Entity ID                                   | Nazwa PL             | Opis                                                | Jednostka |
| ------------------------------------------- | -------------------- | --------------------------------------------------- | --------- |
| `sensor.entso_e_ranking_biezacej_godziny`   | 🔢 Ranking godziny   | Pozycja bieżącej ceny (1 = najtańsza)               | / 24      |
| `sensor.entso_e_percentyl_biezacej_godziny` | 📊 Percentyl godziny | Percentyl cenowy (0% = najtaniej, 100% = najdrożej) | %         |

**Interpretacja:**

- Rank `3/24` = trzecia najtańsza godzina dnia
- Percentyl `12.5%` = tańsza niż 87.5% godzin dnia

### Delty cenowe

| Entity ID                       | Nazwa PL          | Opis                                    |
| ------------------------------- | ----------------- | --------------------------------------- |
| `sensor.entso_e_zmiana_ceny_1h` | Δ Zmiana ceny +1h | Różnica: cena za godzinę − cena teraz   |
| `sensor.entso_e_zmiana_ceny_3h` | Δ Zmiana ceny +3h | Różnica: cena za 3 godziny − cena teraz |

**Interpretacja:**

- `delta_1h = +0.15` → cena wzrośnie o 0.15 PLN/kWh za godzinę
- `delta_3h = -0.20` → cena spadnie o 0.20 PLN/kWh za 3 godziny

---

## Sensory binarne

| Entity ID                                      | Nazwa PL             | ON gdy...                    | Ikona ON           | Ikona OFF          |
| ---------------------------------------------- | -------------------- | ---------------------------- | ------------------ | ------------------ |
| `binary_sensor.entso_e_trend_rosnacy_3h`       | 📈 Trend rosnący 3h  | Ceny rosną przez 3h          | `trending-up`      | `trending-neutral` |
| `binary_sensor.entso_e_trend_malejacy_3h`      | 📉 Trend malejący 3h | Ceny maleją przez 3h         | `trending-down`    | `trending-neutral` |
| `binary_sensor.entso_e_okno_ladowania_aktywne` | 🔋 Okno ładowania    | Teraz w najtańszym 3h oknie  | `battery-charging` | `battery-outline`  |
| `binary_sensor.entso_e_okno_sprzedazy_aktywne` | 💰 Okno sprzedaży    | Teraz w najdroższym 3h oknie | `cash-check`       | `cash-remove`      |

**Zastosowania w automatyzacjach:**

- `okno_ladowania_aktywne = ON` → włącz ładowanie baterii z sieci
- `okno_sprzedazy_aktywne = ON` → eksportuj z baterii do sieci
- `trend_rosnacy_3h = ON` → kupuj teraz, bo będzie drożej
- `trend_malejacy_3h = ON` → poczekaj, bo będzie taniej

---

## Sensory RCE Spread

> ⚡ **Wymagane:** Integracja RCE PSE (sensor `sensor.rce_pse_cena`) musi być zainstalowana i aktywna.

Spread to różnica między ceną sprzedaży (RCE) a kosztem zakupu (ENTSO-E all-in). Pozytywny spread = zysk na arbitrażu energii.

| Entity ID                                 | Nazwa PL                    | Opis                                  |
| ----------------------------------------- | --------------------------- | ------------------------------------- |
| `sensor.entso_e_cena_rce_teraz`           | 💱 Cena RCE teraz           | Bieżąca cena RCE w PLN/kWh            |
| `sensor.entso_e_spread_kupno_vs_sprzedaz` | 📊 Spread kupno vs sprzedaż | RCE − all-in (zysk na handlu)         |
| `sensor.entso_e_spread_peak_dzisiaj`      | 📈 Spread peak dzisiaj      | Max RCE − Min all-in (potencjał dnia) |
| `sensor.entso_e_spread_arbitraz_baterii`  | 🔋 Spread arbitraż baterii  | Spread z uwzgl. strat baterii         |

### Wzory spread

```
spread_kupno_vs_sprzedaz = RCE_teraz − all_in_cost_teraz
```

```
spread_peak_dzisiaj = max(RCE_dzisiaj) − min(all_in_dzisiaj)
```

```
spread_arbitraz_baterii = RCE_teraz − (all_in_cost_teraz / 0.90)
                                                          ^^^^
                                             sprawność baterii (round-trip)
```

### Interpretacja spreadów

| Wartość                     | Sygnał                | Akcja                                                  |
| --------------------------- | --------------------- | ------------------------------------------------------ |
| spread > 0.10               | 🟢 **Opłacalny**      | Warto sprzedawać / eksportować                         |
| 0 < spread < 0.10           | 🟡 **Marginalny**     | Niewielki zysk, warunkowo                              |
| spread < 0                  | 🔴 **Nieopłacalny**   | NIE sprzedawaj — ładuj baterię                         |
| spread_arbitraz_baterii > 0 | 🟢 **Arbitraż OK**    | Opłaca się kupić i odsprzedać nawet z stratami baterii |
| spread_peak > 0.30          | ⚡ **Duży potencjał** | Planuj ładowanie na minimum i sprzedaż na peak         |

### Przykład liczbowy

```
Godzina 10:00 (ładowanie):
  ENTSO-E cena hurtowa:  0.05 PLN/kWh
  all-in cost:           0.32 PLN/kWh  (z marżą, akcyzą, dystrybucją)

Godzina 18:00 (sprzedaż):
  RCE cena:              0.88 PLN/kWh

Spread = 0.88 - 0.32 = 0.56 PLN/kWh  ← zysk na kWh
Spread (bateria 90%) = 0.88 - (0.32 / 0.90) = 0.88 - 0.356 = 0.524 PLN/kWh

Bateria 10 kWh × 0.524 = 5.24 PLN zysku na cykl ładowanie/rozładowanie
```

---

## Instrukcja konfiguracji RCE

### Wymagania

1. **Integracja RCE PSE** — musi być zainstalowana w Home Assistant i dostarczać sensor:
   - `sensor.rce_pse_cena` — bieżąca cena RCE w PLN/MWh
   - `sensor.rce_max_today` — (opcjonalny) max RCE dzisiaj

2. **ENTSO-E Ceny Energii** — ta integracja z poprawnie skonfigurowanymi kosztami all-in

### Krok po kroku

#### 1. Sprawdź czy masz RCE

W **Developer Tools → States** wyszukaj `rce_pse_cena`:

```
sensor.rce_pse_cena = -1.28
unit_of_measurement: PLN/MWh
```

> ⚠️ Wartość musi być w **PLN/MWh** — integracja automatycznie przelicza na PLN/kWh.

#### 2. Skonfiguruj ENTSO-E

W **Settings → Devices & Services → ENTSO-E Ceny Energii → Configure**:

| Pole                 | Wartość               | Uwagi                 |
| -------------------- | --------------------- | --------------------- |
| Marża sprzedawcy     | np. `0.05`            | Twoja marża w PLN/kWh |
| Akcyza               | `0.005`               | Polska domyślnie      |
| Stawka dystrybucyjna | np. `0.20`            | Sprawdź u swojego OSD |
| **Encja RCE**        | `sensor.rce_pse_cena` | Domyślna wartość ✅   |

#### 3. Sprawdź sensory spread

Po save + restart, w **Developer Tools → States** powinny pojawić się:

```
sensor.entso_e_cena_rce_teraz = -0.0013
sensor.entso_e_spread_kupno_vs_sprzedaz = -0.3213
sensor.entso_e_spread_arbitraz_baterii = -0.3569
```

> Ujemny spread = nie opłaca się sprzedawać w tej godzinie (cena RCE jest ujemna!).

#### 4. Wyłączenie spreadu

Aby wyłączyć sensory spread, wyczyść pole **Encja RCE** w konfiguracji (ustaw puste). Sensory spread pokażą `None`.

---

## Wzory i obliczenia

### Konwersja waluty

```
cena_PLN_kWh = (cena_EUR_MWh / 1000) × kurs_NBP × (1 + VAT/100)
```

### Kurs NBP

Automatycznie pobierany z API Narodowego Banku Polskiego:

```
https://api.nbp.pl/api/exchangerates/rates/a/EUR/?format=json
```

Fallback: kurs z poprzedniego dnia roboczego.

### Sliding Window (N godzin)

```python
for i in range(total_hours - N + 1):
    window_avg = mean(prices[i:i+N])
    # wybierz min lub max avg
```

### Percentyl

```
percentile = ((rank - 1) / (total - 1)) × 100
```

### Delta

```
delta_Nh = cena(teraz + N godzin) − cena(teraz)
```

### Trend 3h

```
trend_up   = cena(t) < cena(t+1) < cena(t+2) < cena(t+3)
trend_down = cena(t) > cena(t+1) > cena(t+2) > cena(t+3)
```

---

## Przykłady automatyzacji

### 🔋 Automatyczne ładowanie baterii

```yaml
automation:
  - alias: "ENTSO-E: Ładuj baterię w najtańszym oknie"
    trigger:
      - platform: state
        entity_id: binary_sensor.entso_e_okno_ladowania_aktywne
        to: "on"
    condition:
      - condition: numeric_state
        entity_id: sensor.battery_soc
        below: 80
    action:
      - service: switch.turn_on
        target:
          entity_id: switch.battery_grid_charge
```

### 💰 Eksport przy pozytywnym spread

```yaml
automation:
  - alias: "ENTSO-E: Eksportuj gdy spread opłacalny"
    trigger:
      - platform: numeric_state
        entity_id: sensor.entso_e_spread_kupno_vs_sprzedaz
        above: 0.10
    condition:
      - condition: numeric_state
        entity_id: sensor.battery_soc
        above: 30
    action:
      - service: switch.turn_on
        target:
          entity_id: switch.battery_discharge_to_grid
```

### 📉 Stop eksport gdy spread ujemny

```yaml
automation:
  - alias: "ENTSO-E: Stop eksport — spread ujemny"
    trigger:
      - platform: numeric_state
        entity_id: sensor.entso_e_spread_kupno_vs_sprzedaz
        below: 0
    action:
      - service: switch.turn_off
        target:
          entity_id: switch.battery_discharge_to_grid
```

### 📈 Alert: wysoki spread peak

```yaml
automation:
  - alias: "ENTSO-E: Powiadomienie o dużym potencjale"
    trigger:
      - platform: numeric_state
        entity_id: sensor.entso_e_spread_peak_dzisiaj
        above: 0.40
    action:
      - service: notify.mobile_app
        data:
          message: >
            ⚡ Potencjał arbitrażu: {{ states('sensor.entso_e_spread_peak_dzisiaj') }} PLN/kWh!
            Ładuj o {{ state_attr('sensor.entso_e_aktualna_cena_energii', 'cheapest_hours_start') }},
            sprzedaj o {{ state_attr('sensor.entso_e_aktualna_cena_energii', 'most_expensive_3h_start') }}.
```

---

## Podsumowanie — mapa sensorów

```
ENTSO-E Ceny Energii
├── Sensors (22)
│   ├── Cenowe:  entso_e_aktualna_cena_energii, _cena_za_nastepna_godzine,
│   │            _minimum_dzisiaj, _maksimum_dzisiaj, _srednia_dzisiaj
│   ├── All-in:  entso_e_koszt_all_in_teraz, _nastepna_h, _min_dzisiaj, _max_dzisiaj
│   ├── Okna:    entso_e_najtansze_2h/3h/4h_srednia, _najdrozsze_2h/3h_srednia
│   ├── Rank:    entso_e_ranking_biezacej_godziny, _percentyl_biezacej_godziny
│   ├── Delta:   entso_e_zmiana_ceny_1h, _zmiana_ceny_3h
│   └── Spread:  entso_e_cena_rce_teraz, _spread_kupno_vs_sprzedaz,
│                _spread_peak_dzisiaj, _spread_arbitraz_baterii
├── Binary Sensors (4)
│   ├── entso_e_trend_rosnacy_3h, _trend_malejacy_3h
│   └── entso_e_okno_ladowania_aktywne, _okno_sprzedazy_aktywne
└── Config
    ├── API token, strefa, waluta, VAT, kurs NBP
    ├── Koszty: marża, akcyza, dystrybucja
    └── RCE entity (opcjonalne)
```

---

_Dokumentacja wygenerowana dla ENTSO-E Ceny Energii v1.1.1 by Smarting HOME_
