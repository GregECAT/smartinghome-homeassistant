# Deye w Smarting HOME — tylko odczyt i podgląd sterowania

Stan: Smarting HOME **odczytuje** dane z falowników Deye i **nie zmienia ich nastaw**. To samo dotyczy Growatt.
Autopilot liczy plan i pokazuje decyzje, ale żadne polecenie nie trafia do falownika: wszystkie zapisy w
`energy_manager.py` przechodzą przez jedną bramkę (`_skip_write`), a test `tests/test_read_only_inverter.py`
sprawdza, że żadna ścieżka nie zapisuje.

## Integracja falownika

- [ha-solarman](https://github.com/davidrapan/ha-solarman) z HACS, logger Solarman (LSW-3, LSE-3) albo bramka Modbus TCP.
- Profil `deye_p3`: falowniki 3-fazowe SG04LP3 i SG01HP3.
- Smarting HOME sam odnajduje encje ha-solarman po `unique_id` (niezależnie od nazwy urządzenia): moc PV, zużycie,
  SOC, napięcia faz, częstotliwość, liczniki dzienne, temperatury.
- **Moc sieci i baterii przypisz ręcznie** i sprawdź znak. Smarting HOME oczekuje dla sieci `+ eksport / − pobór`.
  Znaki Deye nie zostały jeszcze sprawdzone na prawdziwej instalacji, więc nie są zgadywane.

## Podgląd sterowania (zakładka Autopilot)

Dla Deye Smarting HOME liczy, co zapisałby do harmonogramu **Time of Use** (6 programów: godzina, SOC, ładowanie z
sieci), i pokazuje to w zakładce Autopilot oraz w dzienniku decyzji („Deye — podgląd, nic nie zapisano”).
Zmieniany byłby tylko program, który trwa teraz; pozostałe zostają jak u instalatora.

| Decyzja Autopilota | Program, który trwa teraz |
|---|---|
| autokonsumpcja | bez zmian (jak u instalatora) |
| bateria zasila dom | bez ładowania z sieci, SOC = rezerwa instalatora |
| ładowanie z sieci | ładowanie z sieci, SOC 100 % |
| trzymanie baterii | bez ładowania z sieci, SOC = SOC w chwili rozpoczęcia (bateria się nie rozładowuje, PV dalej ładuje) |
| sprzedaż z magazynu, eksport PV z odłożeniem ładowania | niedostępne dla Deye → jak „bateria zasila dom” |

Podgląd liczy też zapisy na dobę (budżet: 48 na dobę, 6 na godzinę), bo falownik zapisuje nastawy w pamięci
trwałej. Zatrzymuje się, gdy Time of Use jest wyłączony, godzin programów nie da się odczytać albo zegar falownika
różni się od zegara Home Assistant o ponad 5 minut.

Logika: `deye_tou.py` (bez zależności od Home Assistant, testy `tests/test_deye_tou.py`), odczyt encji:
`deye_ha.py`.

## Czego tu nie ma

Zapisu do falownika. Zanim powstanie, potrzebny jest test na prawdziwym falowniku Deye (połączenie z loggerem,
pierwszy zapis, bity rejestrów programów, ładowanie z sieci, trzymanie, przywracanie, awaria, budżet zapisów,
zmiana z aplikacji Solarman), z powrotem do ustawień instalatora jako stanem awaryjnym.
