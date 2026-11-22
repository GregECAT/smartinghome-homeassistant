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
| Sterowanie zaworem i pompą CO: krzywa pogodowa, tryby, temperatura dzienna i nocna, zrzut ciepła | ✅ | ✅ |
| Pompa ładująca CWU: sterowanie różnicowe, dogrzanie CWU przy gorącym kotle | ✅ | ✅ |
| Skrypt bezpieczeństwa na Shelly pompy | ✅ | ✅ |
| Korekta krzywej według czujnika odniesienia w domu | | ✅ |
| Doradca: model strat domu, „bufor wystarczy do”, „rozpal do”, „załaduj X kg”, kalibracja ważeniem, koszt sezonu | | ✅ |

## Sprzęt (przykładowy zestaw)

| Element | Rola |
|---|---|
| Shelly 1PM Gen3 + **Shelly Plus Add-on** + do 5× DS18B20 | sondy temperatury; przekaźnik steruje pompą CO (z pomiarem mocy) |
| Shelly 2PM Gen3 w trybie **Cover** + siłownik 3-punktowy 230 V (np. ESBE ARA661, 120 s) na zaworze 3-drogowym (np. ESBE VRG132) | zawór mieszający obiegu grzejników |
| Shelly 1PM Mini Gen3 (opcjonalnie) | przekaźnik pompy ładującej CWU (z pomiarem mocy) |
| Przełącznik modułowy 1-0-2 na każdą pompę (opcjonalnie) | AUTO–0–RĘKA: pompa ruszy ręcznie nawet bez Shelly |
| Shelly H&T (lub inny czujnik) | czujnik odniesienia: temperatura i wilgotność w domu — według niego moduł grzeje |
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

Każdą sondę prowadź osobnym przewodem do Add-onu i łącz je w jednym miejscu, przy samym Shelly.
Łączenie kilku sond w jeden przewód przy kotle albo buforze to najczęstsza przyczyna
„zamrożonych” odczytów i stałych 85,0 °C. Add-on obsługuje do 5 sond DS18B20 na jednej linii (VCC,
DATA, GND). Zaciski ANALOG IN i VREF OUT nie nadają się do sond DS18B20. Drugą linię sond daje
dopiero drugi Shelly z Add-onem.

#### Praca z niepełnym zestawem sond

Moduł działa też bez części sond. Nieprzypisaną sondę po prostu pomija, a alarm
„Brak odczytu czujnika” dotyczy tylko sond, które są przypisane.

| Brak sondy | Co dalej działa | Czego nie ma |
|---|---|---|
| Kocioł — wyjście | krzywa grzewcza, zawór, pompa CO według domu, CWU z bufora | stanu kotła i sesji palenia, alarmów przegrzania kotła, wymuszenia pompy przy gorącym kotle |
| Bufor — góra | CWU ładowana według **dołu** bufora (zaczyna trochę później); dół pilnuje też progu przegrzania bufora; zapas ciepła liczony z samego dołu (szacunek, zaniżony) | alarmu „bufor pełny”, limitu zasilania od góry bufora |
| Bufor — dół | zapas ciepła liczony z samej góry (szacunek) | dokładnego zapasu ciepła |

Bezpieczeństwa kotła nie zapewnia Home Assistant, tylko termostat kotła, zawór termiczny (TZB)
i Laddomat. Bez sondy kotła moduł nie pomoże przy przegrzaniu, więc taki układ traktuj jako
tymczasowy.

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
5. Ustaw krzywą grzewczą (podgląd na wykresie), granicę grzania, temperaturę zadaną w domu na dzień i ewentualnie na noc, potem tryb **Automatyczne**.
6. (Shelly) **Skrypt bezpieczeństwa → Sprawdź → Zainstaluj.**
7. Wykonaj testy odbiorowe (niżej).

## Temperatura zadana w domu — dzień i noc

Karta **Sterowanie** ma blok „Temperatura zadana w domu”: **☀️ Dzień** i **🌙 Noc** z przyciskami −/+,
godziny nocy (od – do) i przełącznik **obniżenie nocne**. Obowiązująca teraz wartość jest podświetlona,
a kafel „Zadana w domu” pokazuje ją z porą („noc, do 05:00”). Te same pola są w Ustawieniach → „Krzywa grzewcza”.

- Temperatura jest mierzona **czujnikiem odniesienia** (Ustawienia → „Czujniki”; przy kilku kondygnacjach —
  „Czujnik odniesienia jest na”). Według niej liczą się krzywa grzewcza, korekta (PRO) i decyzja, czy grzać.
- Noc to osobna, **bezwzględna** temperatura (np. dzień 22 °C, noc 20 °C) — zmiana dziennej jej nie przesuwa.
  Nocna nigdy nie jest wyższa od dziennej. Wyłączenie obniżenia zapamiętuje ustawioną wartość.
- Okno nocy może przechodzić przez północ (np. 22:00–05:00). **Koniec nocy ustaw z zapasem** — o tej godzinie
  dom zaczyna się dogrzewać, przy grzejnikach zwykle 1–2 h przed wstaniem.
- O początku i końcu nocy moduł zapisuje wpis w dzienniku („🌙 Noc: zadana w domu 20,0 °C…”).
- **Pompa CO według domu:** rusza, gdy w domu jest poniżej zadanej + 0,5 K, staje przy zadanej + 1 K.
  Pomiędzy tymi progami temperaturę w domu trzyma zawór — przez zasilanie z krzywej i korektę.
- **Korekta od temperatury w domu (PRO)** ma pamięć: gdy dom długo jest za chłodny, dokłada zasilania, gdy za
  ciepły — ujmuje (do ±4 K). Każda zmiana zadanej (ręczna albo dzień ↔ noc) zaczyna tę pamięć od zera, a pamięć
  pchająca w złą stronę (np. dokłada ciepła, choć w domu już jest za ciepło) zanika kilka razy szybciej —
  także wtedy, gdy pompa stoi.
- Doradca liczy „bufor wystarczy do” i zapotrzebowanie na 24 h z rozkładem dzień / noc.
- Ustawienia sprzed v1.87 („Obniżenie nocne (K)”) przechodzą same: noc = dzień − obniżenie.

## Tryby

| Tryb | Zawór | Pompa CO | Pompa CWU |
|---|---|---|---|
| Wyłączone | nic | nic (również bez zrzutu ciepła — tylko pomiary i alarmy) | nic |
| Ręczne | przyciski w panelu | przyciski w panelu | przyciski w panelu |
| Automatyczne | krzywa pogodowa (+ korekta od temperatury w domu, PRO), limit: góra bufora − 2 K; gdy pompa CO stoi ≥ 10 min — zamknięty (bez przepływu grawitacyjnego do grzejników) | pracuje, gdy jest zapotrzebowanie (średnia temp. zewnętrzna < granicy grzania, ciepło w buforze, w domu poniżej zadanej + 0,5 K; wyłącza przy zadanej + 1 K); min. 5 min pracy / postoju | ładowanie różnicowe albo według godzin (niżej); raz w tygodniu 30 s przepłukania |
| Lato | zamknięty | stoi; raz w tygodniu 60 s przepłukania | jak w trybie automatycznym |

We wszystkich trybach poza „Wyłączone” działa bezpieczeństwo — z tymi samymi progami co skrypt na Shelly,
więc Home Assistant nigdy nie działa wbrew niemu:
- **kocioł gorący** (kocioł ≥ próg alarmu − 2 K, góra bufora ≥ „bufor pełny” + 4 K, albo kocioł ≥ ostrzeżenie
  i bufor pełny; koniec 3 K niżej) → **pompa CO włączona**, a przy grzejnikach także **zrzut ciepła**
  (zawór jedzie do pełnego otwarcia i ponawia to co 5 min); przy podłogówce zrzutu nie ma nigdy,
- **kocioł gorący** → pompa CWU dogrzewa zasobnik do zadanej (z opcją „zrzut ciepła do CWU” — do górnego
  limitu), o ile źródło jest cieplejsze od CWU,
- **brak odczytu sondy kotła lub bufora** przez 60 s → pompa CO włączona (bez odczytu kotła moduł nie wie,
  czy się pali). Panel pokazuje wtedy pasek „Pompa CO pracuje awaryjnie” z powodem,
- **ogranicznik zasilania** (zasilanie > maks. + 3 K) → zawór przymykany.

### Pompa CWU według godzin

W karcie **Pompa ładująca CWU** (Ustawienia) wybierz **Sterowanie pompą CWU → Według godzin**, jeśli
zasobnik nie ma sondy albo jej odczyty są niepewne. Wtedy:
- pompa ładuje CWU tylko w **dwóch oknach godzinowych** (domyślnie 5–6 i 17–18). Takie samo „od” i „do”
  wyłącza okno, a okno może przechodzić przez północ (np. 23–1),
- ładuje tylko wtedy, gdy źródło ma co najmniej **zadaną CWU + różnicę startu** (domyślnie 55 + 5 = 60 °C).
  Bez sondy CWU moduł nie zna temperatury zasobnika, a ten warunek gwarantuje, że wężownica go nie wychłodzi,
- przy braku sondy góry bufora źródłem jest dół bufora,
- jeśli sonda CWU jest przypisana i działa, pompa staje też przy zadanej CWU i przy górnym limicie,
- „zrzut ciepła do CWU” przy gorącym kotle działa tylko z sondą CWU, bo bez niej nic nie ograniczałoby
  temperatury w zasobniku.

Ręcznie pompę CWU włączasz przełącznikiem **RĘKA** w karcie Sterowanie, który działa niezależnie od godzin.
Pozycja **AUTO** wraca do harmonogramu.

**Zawór przy postoju pompy CO.** Gdy pompa CO stoi co najmniej 10 minut (np. w domu jest ciepło albo trwa
obniżenie nocne), moduł zamyka zawór mieszający do końca. Otwarty zawór pozwala gorącej wodzie z bufora
płynąć grawitacyjnie do grzejników także bez pompy. W jednej z pierwszych nocy było to ok. 17 kWh, czyli
ok. 5 kg drewna, poza kontrolą. Po ponownym starcie pompy zawór otwiera się krokami, a nie od razu na 100 %.
Jeśli grupa pompowa ma hamulec grawitacyjny (zawór zwrotny), zamykanie zaworu dodatkowo zabezpiecza.

**Przy montażu, serwisie albo gdy w instalacji nie ma wody** przełącz tryb na **Wyłączone** albo zablokuj
pompę (pozycja 0). Inaczej, dopóki sondy nie czytają, pompa CO pracuje (także na sucho).
W trybie „Wyłączone” panel informuje, że zabezpieczenie pompy CO nie działa.

## Przełączniki AUTO / 0 / RĘKA i wyłączniki awaryjne

W karcie **Sterowanie** każda przypisana pompa (CO i CWU) ma przełącznik z trzema pozycjami, jak fizyczny
przełącznik w skrzynce:
- **AUTO** — pompą steruje moduł według trybu (Wyłączone / Ręczne / Automatyczne / Lato) i zabezpieczeń;
- **0 (wyłącznik awaryjny)** — pompa jest wyłączana i **nic jej nie włączy**: ani automatyka, ani przyciski
  trybu Ręczne, ani zabezpieczenia (przegrzanie, brak sond, zrzut ciepła do CWU). Jeśli ktoś włączy przekaźnik
  poza modułem, moduł od razu go wyłączy. Przy pompie CO **skrypt bezpieczeństwa na Shelly** jest zatrzymywany
  (i nie startuje po restarcie Shelly), a po wyjściu z pozycji 0 uruchamiany ponownie;
- **RĘKA** — pompa **pracuje na stałe**, niezależnie od trybu modułu i automatyki, aż do przełączenia na AUTO
  albo 0. Przydaje się do odpowietrzania, testów i awaryjnej pracy, gdy automatyka zawodzi. Pompa CWU w pozycji
  RĘKA wychładza zasobnik, gdy bufor jest od niego chłodniejszy.

**Siłownik zaworu** ma sam wyłącznik awaryjny: zablokowany zatrzymuje się i nie jest poruszany (także przy
zrzucie ciepła i ograniczniku).

Pozycje działają w każdym trybie i przetrwają restart Home Assistanta. Każda zmiana wymaga potwierdzenia
w oknie dialogowym; przejście na 0 i na RĘKA — dodatkowo zaznaczenia „Rozumiem…”. Pozycja inna niż AUTO jest
widoczna na górze ekranu i na schemacie kotłowni. Gdy zabezpieczenie chce włączyć element w pozycji 0, pojawia
się alarm „Wyłącznik awaryjny blokuje zabezpieczenie” (krytyczny, gdy kocioł jest gorący). Fizyczny
przełącznik AUTO–0–RĘKA w skrzynce działa niezależnie od panelu.

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
| Wyłącznik awaryjny blokuje zabezpieczenie (zabezpieczenie chce włączyć zablokowany element) | ostrzeżenie; krytyczny, gdy kocioł gorący |
| Czas rozpalić (w domu 1,5 K poniżej obowiązującej zadanej, bufor < 15 %), wilgotność | informacja |

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

Pompą Laddomatu steruje termostat kotła, więc moduł jej nie widzi: schemat pokazuje ją jako pracującą,
gdy wyjście z kotła osiągnie próg włączenia (Ustawienia → „Kocioł i drewno” → „Pompa Laddomatu rusza od”;
Atmos: 75 °C), i jako stojącą 3 K poniżej progu. Wcześniej, w trakcie rozpalania, schemat pokazuje „od 75°”.

**Dom piętro po piętrze** (Ustawienia → „Dom — kondygnacje”): do 3 kondygnacji (np. parter, 1. piętro,
poddasze) z nazwą, powierzchnią i własnym czujnikiem temperatury / wilgotności. Kondygnacja z czujnikiem
odniesienia bierze jego odczyt, dopóki nie ma własnego czujnika. Na schemacie dom dzieli się na piętra
(najwyższe pod dachem), a **średnia domu** — ważona powierzchnią, bez powierzchni zwykła — trafia do
encji „Dom — średnia temperatura” i do modelu strat ciepła doradcy (straty zależą od całego domu, nie
jednego pomieszczenia). Grzanie (krzywa, korekta, dzień i noc) działa według czujnika odniesienia.
Po wpisaniu powierzchni wszystkich kondygnacji karta pokazuje sumę i udział każdej w średniej (np. 105 /
105 / 40 m² → 42% / 42% / 16%). Kondygnacja bez odczytu jest pomijana, a udziały pozostałych rosną
proporcjonalnie; gdy brakuje którejś powierzchni, kondygnacje liczą się po równo.
Opcja **„tylko podgląd”** przy kondygnacji pokazuje jej czujnik w panelu, ale nie wlicza go do średniej domu
ani do doradcy. Przydaje się, gdy temperaturę daje głowica termostatyczna przy kaloryferze: mierzy tuż przy
gorącym zaworze, więc zawyża temperaturę pomieszczenia. Głowica sama reguluje swój kaloryfer, a moduł tylko
pokazuje jej odczyt. Atrybut `current_temperature` encji `climate` udostępnia się jako czujnik pomocnikiem
„Szablon” (Ustawienia → Urządzenia → Pomocnicy), np. `{{ state_attr('climate.glowica', 'current_temperature') }}`.

Przycisk **⛶** w nagłówku włącza **pełny ekran**: panel zasłania pasek boczny Home Assistanta,
a ekran nie gaśnie (np. tablet na ścianie w kotłowni albo telewizor). **✕** albo Esc wraca do
zwykłego widoku. W aplikacji Home Assistant na iPadzie / iPhonie (i w przeglądarkach bez trybu
pełnoekranowego) panel sam rozciąga się na całe okno — pasek stanu systemu zostaje. Na telefonie schemat
i wykres układają się jeden pod drugim.

Po aktualizacji Smarting HOME otwarty panel (telefon, tablet na ścianie) sam się przeładowuje, żeby nie
działał na starej wersji — z zachowaniem pełnego ekranu. Gdy akurat edytujesz ustawienia, zamiast tego
pojawia się pasek „Jest nowsza wersja panelu” z przyciskiem **Odśwież**.

## Encje (urządzenie „Ogrzewanie”)

Stan kotła · energia, naładowanie, średnia temperatura i miejsce w buforze · maks. załadunek drewna ·
energia CWU · bufor wystarczy na (h) · energia ostatniego palenia · zadana temperatura zasilania ·
położenie zaworu · (PRO) bufor wystarczy do, rozpal do, zalecany załadunek, zapotrzebowanie 24 h,
straty budynku · binarne: kocioł pracuje, przegrzanie, awaria czujnika, bufor pełny, zrzut ciepła,
pompa CWU pracuje ·
**select** tryb ogrzewania · **number** temperatura zadana w domu — dzień, temperatura zadana w domu — noc, przesunięcie krzywej.

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
