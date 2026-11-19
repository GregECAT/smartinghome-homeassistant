# Smarting HOME — Ogrzewanie: kocioł zgazowujący drewno, bufor ciepła, CWU

Moduł „Ogrzewanie” (od v1.81.0) mierzy kotłownię z kotłem na drewno i buforem, ostrzega przed
przegrzaniem, steruje zaworem mieszającym i pompą CO według krzywej pogodowej, ładuje CWU pompą
sterowaną różnicowo (od v1.82.0), a w wersji PRO
podpowiada, **do której rozpalić** i **ile drewna załadować**, żeby nie przegrzać bufora.

> **Zasada nadrzędna.** Home Assistant nigdy nie jest zabezpieczeniem kotła. Kocioł na drewno
> grzeje dalej po zaniku prądu, Wi-Fi albo awarii HA. Bezpieczeństwo zapewniają: termiczny zawór
> bezpieczeństwa (TZB, np. 95 °C) na wężownicy schładzającej kotła, zawór bezpieczeństwa,
> naczynie wzbiorcze, termostaty kotła i Laddomat (obieg grawitacyjny). Integracja może tylko
> **odebrać** ciepło (zrzut do grzejników, pompa CO, dogrzanie CWU), nigdy go nie blokuje. Pompą Laddomatu
> i wentylatorem kotła steruje wyłącznie kocioł.

## Co jest w FREE, a co w PRO

| Funkcja | FREE | PRO |
|---|:-:|:-:|
| Temperatury, energia bufora i CWU, „bufor przyjmie jeszcze X kg drewna” | ✅ | ✅ |
| Stan kotła, sesje palenia, wykres 24 h | ✅ | ✅ |
| **Wszystkie alarmy bezpieczeństwa** (przegrzanie, awaria sondy, zanik prądu w trakcie palenia…) | ✅ | ✅ |
| Sterowanie zaworem i pompą CO: krzywa pogodowa, tryby, obniżenie nocne, zrzut ciepła | ✅ | ✅ |
| Pompa ładująca CWU: sterowanie różnicowe, dogrzanie CWU przy gorącym kotle | ✅ | ✅ |
| Skrypt bezpieczeństwa na Shelly pompy | ✅ | ✅ |
| Korekta krzywej według czujnika w pokoju | | ✅ |
| Doradca: model strat domu, „bufor wystarczy do”, „rozpal do”, „załaduj X kg”, kalibracja ważeniem, koszt sezonu | | ✅ |

## Sprzęt (przykładowy zestaw)

| Element | Rola |
|---|---|
| Shelly 1PM Gen3 + **Shelly Plus Add-on** + do 5× DS18B20 | sondy temperatury; przekaźnik steruje pompą CO (z pomiarem mocy) |
| Shelly 2PM Gen3 w trybie **Cover** + siłownik 3-punktowy 230 V (np. ESBE ARA661, 120 s) na zaworze 3-drogowym (np. ESBE VRG132) | zawór mieszający obiegu grzejników |
| Shelly 1PM Mini Gen3 (opcjonalnie) | przekaźnik pompy ładującej CWU (z pomiarem mocy) |
| Przełącznik modułowy 1-0-2 na każdą pompę (opcjonalnie) | AUTO–0–RĘKA: pompa ruszy ręcznie nawet bez Shelly |
| Shelly H&T (lub inny czujnik) | temperatura i wilgotność w pokoju referencyjnym |
| Encja pogody w HA (np. Met.no) | temperatura zewnętrzna i prognoza godzinowa (doradca) |

Działają też inne urządzenia: dowolne czujniki °C w HA, siłownik jako encja `cover` albo dwa
przekaźniki (otwórz / zamknij). Najdokładniejsze impulsy daje Shelly Gen2/Gen3 w trybie Cover:
czas impulsu odmierza sam Shelly, więc restart HA w trakcie impulsu nie zostawi zaworu w ruchu.

### Punkty pomiarowe

| Sonda | Miejsce | Po co |
|---|---|---|
| Kocioł — wyjście | wyjście z kotła na wejściu do Laddomatu | stan kotła, alarm przegrzania, sesje palenia |
| Bufor — góra | tuleja ~80–90 % wysokości | energia bufora, „bufor pełny”, limit zasilania |
| Bufor — dół | tuleja ~15–20 % wysokości | energia bufora, ładowanie |
| Zasobnik CWU | tuleja ~2/3 wysokości | temperatura i energia CWU, sterowanie pompą CWU, limit grzałki z nadwyżki PV |
| Zasilanie CO | ≥ 0,5 m za zaworem i pompą | sprzężenie zwrotne zaworu |

Wskazówki montażowe: tuleje 150 mm pasują do zbiorników; na rury DN25–32 lepsze są tuleje
50–60 mm. Sondę wsuń do dna z pastą termoprzewodzącą i uszczelnij wylot. Przewód PVC nie może
leżeć na rurze zasilania kotła. Przed montażem skalibruj wszystkie sondy w jednym naczyniu
z wodą (np. ~20 °C i ~60 °C), a różnice wpisz jako korekty w panelu.

### Ustawienia Shelly

- **1PM (pompa CO):** stan po zaniku zasilania `ON`, próg raportowania temperatury Add-on
  **0,1–0,2 °C** (domyślne 0,5 °C to za mało do regulacji). Montuj z dala od kotła (Shelly pracuje do ~40 °C).
- **1PM Mini (pompa CWU):** stan po zaniku zasilania `OFF` — bez Home Assistanta pompa nie powinna
  pracować, bo przy chłodniejszym buforze CWU oddawałaby ciepło z powrotem.
- **2PM (zawór):** tryb Cover, wykrywanie przeszkód wyłączone; zalecane `maxtime_open` = `maxtime_close` =
  czas przejazdu + 10 s (dłuższe ruchy integracja i tak dzieli na odcinki w granicach `maxtime`, każdy odmierzany
  przez Shelly). Kalibracja po mocy nie jest potrzebna — położenie liczy integracja.
- Rezerwacje DHCP, aktualny firmware, integracja Shelly w HA **lokalnie** (nie przez chmurę), bez hasła
  na urządzeniu, jeśli integracja ma sama wgrać skrypt i wysyłać impulsy z czasem trwania.

## Uruchomienie krok po kroku

1. **Smarting HOME → ⚙️ Ustawienia → „Ogrzewanie” → Włącz moduł.** W menu bocznym pojawi się panel
   **Ogrzewanie**. Tryb startowy to **Wyłączone**: moduł tylko mierzy.
2. **Ogrzewanie → Ustawienia → Czujniki.** Przypisz sondy. Odczyt widać na żywo: ogrzej sondę w dłoni
   i patrz, która wartość rośnie. Uzupełnij pojemności bufora i CWU, profil kotła i rodzaj odbiorników.
3. Zbieraj dane 1–2 tygodnie: sprawdź, czy każde palenie jest wykrywane jako sesja, a alarmy nie są fałszywe.
4. **Sterowanie:** wybierz siłownik (roleta albo dwa przekaźniki), czas przejazdu i przekaźniki pomp
   (CO oraz CWU w sekcji „Czujniki”). W karcie **Pompa ładująca CWU** wybierz, skąd grzana jest wężownica
   (z bufora czy z kotła); progi startowe zwykle nie wymagają zmian.
   Przełącz tryb na **Ręczne** i sprawdź kierunek: „Cieplej 5 s” musi podnosić temperaturę zasilania
   (jeśli nie — zaznacz „Odwróć kierunek”). Potem **Kalibruj zawór** (dojazd do pełnego zamknięcia).
5. Ustaw krzywą grzewczą (podgląd na wykresie), granicę grzania i ewentualne obniżenie nocne, potem tryb **Automatyczne**.
6. (Shelly) **Skrypt bezpieczeństwa → Sprawdź → Zainstaluj.**
7. Wykonaj testy odbiorowe (niżej).

## Tryby

| Tryb | Zawór | Pompa CO | Pompa CWU |
|---|---|---|---|
| Wyłączone | nic | nic (również bez zrzutu ciepła — tylko pomiary i alarmy) | nic |
| Ręczne | przyciski w panelu | przyciski w panelu | przyciski w panelu |
| Automatyczne | krzywa pogodowa (+ korekta pokojowa PRO), limit: góra bufora − 2 K | pracuje, gdy jest zapotrzebowanie (średnia temp. zewnętrzna < granicy grzania, ciepło w buforze, pokój niewychłodzony nadmiernie); min. 5 min pracy / postoju | ładowanie różnicowe (niżej); raz w tygodniu 30 s przepłukania |
| Lato | zamknięty | stoi; raz w tygodniu 60 s przepłukania | jak w trybie automatycznym |

We wszystkich trybach poza „Wyłączone” działa bezpieczeństwo — z tymi samymi progami co skrypt na Shelly,
więc Home Assistant nigdy nie działa wbrew niemu:
- **kocioł gorący** (kocioł ≥ próg alarmu − 2 K, góra bufora ≥ „bufor pełny” + 4 K, albo kocioł ≥ ostrzeżenie
  i bufor pełny; koniec 3 K niżej) → **pompa CO włączona**, a przy grzejnikach także **zrzut ciepła**
  (zawór jedzie do pełnego otwarcia i ponawia to co 5 min); przy podłogówce zrzutu nie ma nigdy,
- **kocioł gorący** → pompa CWU dogrzewa zasobnik do zadanej (z opcją „zrzut ciepła do CWU” — do górnego
  limitu), o ile źródło jest cieplejsze od CWU,
- **brak odczytu sondy kotła lub bufora** przez 60 s → pompa CO włączona,
- **ogranicznik zasilania** (zasilanie > maks. + 3 K) → zawór przymykany.

### Pompa ładująca CWU

Pompa tłoczy wodę ze źródła (góra bufora albo wyjście kotła) przez wężownicę zasobnika. Sterowanie jest
różnicowe — pompa pracuje tylko wtedy, gdy źródło jest wyraźnie cieplejsze od wody w zasobniku:

- **start:** źródło ≥ CWU + 5 K **i** CWU ≤ zadana − 5 K (domyślnie 50 °C przy zadanej 55 °C),
- **stop:** CWU osiągnęła zadaną **albo** różnica spadła poniżej 2 K (min. 5 min pracy),
- **stop od razu** (bez minimalnego czasu pracy): źródło chłodniejsze od CWU, brak odczytu sondy CWU
  lub źródła, CWU ≥ górny limit — zasobnik nigdy nie grzeje bufora z powrotem,
- **gorący kocioł:** CWU dogrzewana do zadanej bez czekania na histerezę. Opcja **„Zrzut ciepła do CWU”**
  pozwala grzać ją aż do górnego limitu (domyślnie 80 °C) — zasobnik 500 L przyjmuje wtedy dodatkowe
  ~15 kWh. Włączaj ją **tylko z termostatycznym zaworem mieszającym na wyjściu ciepłej wody**
  (ochrona przed poparzeniem). Przy podłogówce, gdzie zrzutu do domu nie ma, to jedyny dodatkowy odbiór ciepła.

Godziny ładowania CWU z bufora nie trafiają do modelu domu doradcy (to nie jest zużycie domu).

### Krzywa grzewcza

`zasilanie = pokój + nachylenie × (pokój − zewnątrz) + przesunięcie`, w granicach min/maks.
Startowo: grzejniki nachylenie 1,4 (maks. 75 °C), podłoga 0,5 (maks. 45 °C). W domu jest za zimno
przy mrozie, a dobrze w okresie przejściowym → zwiększ nachylenie; za zimno zawsze → przesunięcie w górę.
Głowica termostatyczna na grzejniku w pokoju referencyjnym powinna być w pełni otwarta.

Regulator zaworu to PI w formie przyrostowej dla siłownika 3-punktowego: impuls 2–15 s proporcjonalny
do uchybu, strefa martwa ±0,8 K, przerwa 60 s na opóźnienie transportowe i bezwładność sondy w tulei,
limit 15 min pracy siłownika na godzinę.

## Alarmy

| Alarm | Poziom |
|---|---|
| Przegrzanie kotła (≥ próg alarmu, Atmos: 92 °C) | krytyczny |
| Kocioł gorący (≥ próg ostrzeżenia, 88 °C) | ostrzeżenie |
| Bufor pełny (góra ≥ 88 °C) | ostrzeżenie |
| Ciepło nie płynie do bufora (kocioł ≥ 85 °C, góra bufora o 15 K niżej i nie rośnie od 20 min) | ostrzeżenie |
| Brak odczytu czujnika (−127 °C, nagłe 85,0 °C, niedostępny > 2 min; skok > 10 K przyjmowany po 3 nowych odczytach lub 60 s) | ostrzeżenie; krytyczny w trakcie palenia |
| Zanik prądu w trakcie palenia (falownik widzi 0 V w sieci) | krytyczny |
| Pompa CO / pompa CWU nie pobiera prądu (przekaźnik włączony, moc < 3 W przez 2 min) | ostrzeżenie |
| Zasilanie CO za gorące (ogranicznik) | ostrzeżenie |
| Czas rozpalić (zimno w pokoju, bufor < 15 %), wilgotność | informacja |

Alarmy trafiają kanałami z panelu Smarting HOME (Awarie → powiadomienia). Krytyczne zawsze tworzą
też powiadomienie w Home Assistant — niezależnie od ustawień i licencji.

## Doradca (PRO)

- **Model domu:** w godzinach bez palenia (z pracującą pompą CO, bez ładowania CWU z bufora) spadek ciepła
  w buforze to zużycie domu.
  Po ok. dobie danych przy różnej pogodzie integracja zna straty domu (W/K).
- **Bufor wystarczy do / Rozpal do:** godzinowa prognoza temperatury z encji pogody → zapotrzebowanie
  godzina po godzinie → moment wyczerpania ciepła użytecznego, minus czas rozpalania. Powiadomienie
  „Rozpal w kotle do…” przychodzi 30 min wcześniej.
- **Załaduj X kg:** miejsce w buforze (z marginesem 3 K) + miejsce w CWU + to, co dom zużyje w czasie palenia,
  podzielone przez ciepło z kilograma drewna. Zważ 2–3 załadunki i wpisz je w panelu — doradca nauczy się,
  ile naprawdę daje Twoje drewno w Twoim kotle.
- **Sezon:** palenia, godziny, ciepło i drewno miesiąc po miesiącu, z kosztem przy podanej cenie drewna.

## Skrypt bezpieczeństwa na Shelly

Skrypt działa na Shelly z przekaźnikiem pompy CO, co 10 s czyta sondy kotła i bufora podłączone do tego
samego Shelly (Add-on). Gdy kocioł ≥ próg alarmu − 2 K albo góra bufora ≥ próg „bufor pełny” + 4 K,
włącza pompę CO i jednorazowo otwiera zawór (przy grzejnikach). Gdy sonda nie odpowiada 60 s — włącza pompę.
Pompy nigdy nie wyłącza. Działa bez Home Assistanta i bez internetu; nie zastępuje TZB ani termostatów kotła.
Integracja wgrywa go jednym przyciskiem, a kod można też skopiować i wgrać ręcznie w panelu Shelly.

## Panel „Ogrzewanie” — kotłownia na jednym ekranie

Pierwszy ekran panelu to cała kotłownia na żywo, bez przewijania: pasek stanu (tryb, kocioł, bufor,
„rozpal do”, alarmy), animowany schemat i wykres temperatur z ostatniej doby. Na schemacie płomień
pali się w trakcie palenia, w rurach płynie woda, gdy pracuje pompa Laddomatu, pompa CO albo pompa
CWU, wirniki pomp się kręcą, a bufor ma kolory warstw od dołu do góry. Karty ze sterowaniem,
doradcą, paleniami i dziennikiem są niżej, po przewinięciu.

Przycisk **⛶** w nagłówku włącza **pełny ekran**: panel zasłania pasek boczny Home Assistanta,
a ekran nie gaśnie (np. tablet na ścianie w kotłowni albo telewizor). **✕** albo Esc wraca do
zwykłego widoku. Na telefonie schemat i wykres układają się jeden pod drugim.

## Encje (urządzenie „Ogrzewanie”)

Stan kotła · energia, naładowanie, średnia temperatura i miejsce w buforze · maks. załadunek drewna ·
energia CWU · bufor wystarczy na (h) · energia ostatniego palenia · zadana temperatura zasilania ·
położenie zaworu · (PRO) bufor wystarczy do, rozpal do, zalecany załadunek, zapotrzebowanie 24 h,
straty budynku · binarne: kocioł pracuje, przegrzanie, awaria czujnika, bufor pełny, zrzut ciepła,
pompa CWU pracuje ·
**select** tryb ogrzewania · **number** temperatura pokojowa zadana, przesunięcie krzywej.

## Grzałka z nadwyżki PV a CWU

W karcie „Bojler z nadwyżki PV” (HEMS) można ustawić **CWU maks. (°C)**: gdy sonda CWU z modułu
Ogrzewanie pokazuje tę temperaturę, autopilot nie włącza grzałki, a włączoną przez siebie wyłącza.

## Testy odbiorowe (zalecane po montażu i po zmianach)

1. Zatrzymanie HA w trakcie palenia → pompa CO pracuje, zawór stoi, Laddomat ładuje bufor.
2. Wyłączenie Wi-Fi → skrypt na Shelly reaguje na przegrzanie (próg tymczasowo obniżony do testu).
3. Odłączenie sondy zasilania CO → alarm, zawór wstrzymany.
4. Sonda kotła w gorącej wodzie (~93 °C) → alarm krytyczny, pompa CO, zawór otwarty.
5. Restart HA w trakcie impulsu otwierania → impuls kończy się na Shelly.
6. Pompa CWU w trybie Ręczne: „Pompa CWU ON” → moc > 3 W, CWU rośnie, góra bufora spada. W trybie
   Automatyczne przy buforze cieplejszym od CWU o ≥ 5 K i CWU ≤ zadana − 5 K pompa rusza, przy CWU = zadana staje.
7. Raz w roku: test TZB według instrukcji producenta — woda płynie do odpływu.
