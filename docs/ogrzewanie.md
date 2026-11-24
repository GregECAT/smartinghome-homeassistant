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
| Sterowanie zaworem i pompą CO: krzywa pogodowa, tryby, temperatura dzienna i nocna, obniżenia w ciągu dnia, zrzut ciepła | ✅ | ✅ |
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
| Czujnik spalin (opcjonalnie): PT100 / termopara do 400–500 °C + przetwornik 0–10 V na wejście ANALOG IN Shelly Plus Add-on | rozpalenie i wygaśnięcie widać od razu, nie dopiero po temperaturze wody |
| Gniazdko Wi-Fi (opcjonalnie) | wentylator nagrzewnicy na obiegu CO za pompą (np. garaż): rusza 2 min po pompie CO, gaśnie 2 min po niej |
| Przełącznik modułowy 1-0-2 na każdą pompę (opcjonalnie) | AUTO–0–RĘKA: pompa ruszy ręcznie nawet bez Shelly |
| Shelly H&T (lub inny czujnik) | czujnik odniesienia: temperatura i wilgotność w domu — według niego moduł grzeje |
| Encja pogody w HA (np. Met.no) | temperatura zewnętrzna i prognoza godzinowa (doradca) |
| Czujnik zewnętrzny (opcjonalnie, np. na północnej ścianie, poza słońcem) | gdy jest, krzywa grzewcza i granica grzania liczą się według niego, a prognoza zostaje do porównania (schemat pokazuje obie) i jako zapas, gdy czujnik zniknie |

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
| Spaliny (opcjonalnie) | czopuch, ~20–30 cm za kotłem, czujnik w osi przewodu | początek i koniec palenia (niżej: „Palenie według spalin”) |
| Zasilanie wężownicy CWU (opcjonalnie) | rura zasilania wężownicy tuż przy zasobniku, pod otuliną | wykrywa krążenie grawitacyjne do CWU przy stojącej pompie |

Czujnik spalin nie jest sondą DS18B20: ma własny zakres (−20…500 °C), a odczyt 85 °C jest dla niego
zwykłą temperaturą. Encja w HA musi podawać °C — przelicz sygnał przetwornika w ustawieniach wejścia
analogowego Shelly (np. przetwornik 0–400 °C na 0–10 V: 0–100 % × 4 = °C). Pozostałe sondy (także zasobnik CWU
i wężownica, które stoją w domu, daleko od kotłowni) to zwykłe DS18B20 — przy długim przewodzie najlepiej
na osobnym Shelly z Add-onem przy zasobniku.

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
| Spaliny | palenie rozpoznawane po temperaturze wody w kotle | szybkiego wykrycia rozpalenia i końca palenia |
| Zasilanie wężownicy CWU | wszystko | alarmu „Ciepło płynie do CWU grawitacyjnie” |

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
- **Pompa CO według domu:** domyślnie rusza, gdy w domu jest poniżej zadanej + 0,5 K, i staje przy zadanej
  + 1 K — pomiędzy tymi progami temperaturę w domu trzyma zawór, przez zasilanie z krzywej i korektę.
  **Histereza pompy CO** (Ustawienia → Krzywa grzewcza) zamienia to na pasmo **wokół zadanej**: 0,2 K przy
  24 °C = rusza poniżej 23,9 °C, staje przy 24,1 °C. Wąskie pasmo ma sens z czujnikiem odniesienia zasilanym
  z sieci, który podaje każdą zmianę o 0,1 K (na baterii czujnik raportuje rzadziej); pompa i zawór
  przełączają się wtedy częściej. Puste pole przywraca ustawienie domyślne.
- **Korekta od temperatury w domu (PRO)** ma pamięć: gdy dom długo jest za chłodny, dokłada zasilania, gdy za
  ciepły — ujmuje (do ±4 K). Każda zmiana zadanej (ręczna albo dzień ↔ noc) zaczyna tę pamięć od zera, a pamięć
  pchająca w złą stronę (np. dokłada ciepła, choć w domu już jest za ciepło) zanika kilka razy szybciej —
  także wtedy, gdy pompa stoi.
- Doradca liczy „bufor wystarczy do” i zapotrzebowanie na 24 h z rozkładem dzień / noc.
- Ustawienia sprzed v1.87 („Obniżenie nocne (K)”) przechodzą same: noc = dzień − obniżenie.

### Obniżenie w ciągu dnia

Pod nocą jest przycisk **„+ Dodaj obniżenie w ciągu dnia”** — niższa temperatura w wybrane godziny i dni,
na przykład **7:00–15:00 od poniedziałku do piątku, gdy nikogo nie ma w domu**. Każde obniżenie ma własną
temperaturę (−/+), godziny od – do, dni tygodnia (Pn … Nd) i przełącznik „włączone”; zmiany zapisują się od razu.
Można dodać do czterech.

- W tych godzinach krzywa grzewcza i włączanie grzania liczą się od temperatury obniżenia: pompa CO staje,
  gdy w domu jest cieplej niż obniżenie + 1 K, a bufor zostaje na później.
- **Koniec ustaw z zapasem, przed powrotem domowników** — o tej godzinie dom zaczyna się dogrzewać
  (przy grzejnikach zwykle 1–2 h). Przykład: wracacie o 16:00 → obniżenie 7–14 albo 7–15.
- Gdy nakładają się noc i obniżenie albo dwa obniżenia, obowiązuje **niższa** temperatura. Obniżenie nigdy
  nie podnosi temperatury ponad dzienną — takie panel oznacza jako „bez skutku”.
- Obniżenie, które przechodzi przez północ (np. 22–6), liczy się do dnia, w którym się zaczęło: zaznaczony piątek
  obejmuje też sobotni ranek.
- Obowiązujące obniżenie jest podświetlone, kafel „Zadana w domu” pokazuje „obniżenie, do 15:00”, a dziennik
  — „🏠 Obniżenie 7–15: zadana w domu 22,0 °C”. Doradca liczy zapotrzebowanie z obniżeniami (według dni tygodnia).

## Tryby

| Tryb | Zawór | Pompa CO | Pompa CWU |
|---|---|---|---|
| Wyłączone | nic | nic (również bez zrzutu ciepła — tylko pomiary i alarmy) | nic |
| Ręczne | przyciski w panelu albo suwak „Otwarcie zaworu” (0–100 %) | przyciski w panelu | przyciski w panelu |
| Automatyczne | krzywa pogodowa (+ korekta od temperatury w domu, PRO), limit: góra bufora − 2 K; gdy pompa CO stoi ≥ 2 min — zamknięty (bez przepływu grawitacyjnego do grzejników) | pracuje, gdy jest zapotrzebowanie (średnia temp. zewnętrzna < granicy grzania, ciepło w buforze, w domu poniżej zadanej + 0,5 K; wyłącza przy zadanej + 1 K — albo pasmo „Histereza pompy CO” wokół zadanej); min. 5 min pracy / postoju | ładowanie różnicowe albo według godzin (niżej); raz w tygodniu 30 s przepłukania |
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
- „zrzut ciepła do CWU” (opcja w karcie Pompa ładująca CWU) działa też bez sondy CWU: gdy kocioł jest
  gorący (ten sam warunek, który włącza zrzut ciepła do domu), pompa CWU pracuje o każdej porze, aż kocioł
  przestanie być gorący. Bez sondy moduł nie pilnuje górnego limitu CWU, więc woda w zasobniku może dojść do
  temperatury bufora (ok. 90 °C). Dlatego ta opcja wymaga termostatycznego zaworu mieszającego na wyjściu
  ciepłej wody. Bez tej opcji i bez sondy gorący kocioł nie dogrzewa CWU, bo nic nie zatrzymałoby jej
  przy zadanej.

Ręcznie pompę CWU włączasz przełącznikiem **RĘKA** w karcie Sterowanie, który działa niezależnie od godzin.
Pozycja **AUTO** wraca do harmonogramu.

### CWU tylko przy paleniu

Opcja **Ładuj CWU tylko przy paleniu w kotle** (karta Pompa ładująca CWU) działa w obu sposobach sterowania,
według sondy i według godzin. Gdy w kotle się nie pali, pompa CWU stoi, także w oknach godzinowych, a ciepło
z bufora zostaje dla domu. Duży zasobnik i tak wytrzymuje dobę lub dwie między paleniami. Palenie trwa od
rozpalania do końca wygasania, czyli do spadku kotła poniżej 60 °C, a z czujnikiem spalin do chwili, gdy
spaliny przez 15 minut są chłodniejsze niż 80 °C. Ładowanie przerwane końcem palenia zatrzymuje się od razu.

- **Zrzut ciepła z gorącego kotła działa zawsze**, bo jest zabezpieczeniem.
- Gdy stan kotła jest nieznany (brak odczytu sondy kotła), moduł nie ładuje CWU.
- Przepłukanie pompy CWU (30 s raz w tygodniu) zostaje.
- W trybie według godzin ładowanie wymaga, by okno wypadło w czasie palenia. Jeśli palisz o różnych porach,
  ciepło do CWU trafia głównie ze zrzutu przy gorącym kotle. Pewniejsze jest sterowanie według sondy CWU:
  pompa ładuje wtedy zasobnik w trakcie każdego palenia, aż do zadanej.

### Krążenie grawitacyjne do CWU

Gorąca woda z bufora potrafi sama, bez pompy, wznosić się do wężownicy zasobnika. Bufor oddaje wtedy ciepło do
CWU przez całą dobę, a ustawienia pompy CWU (godziny, „tylko przy paleniu”) nic nie dają. Z samych sond bufora
i zasobnika tego nie widać, bo w trakcie palenia i tak wszystko jest gorące. Pokazuje to dopiero czujnik
**Zasilanie wężownicy CWU (przy zasobniku)**: rura bez przepływu stygnie do temperatury otoczenia.

Moduł podnosi ostrzeżenie **„Ciepło płynie do CWU grawitacyjnie”**, gdy jednocześnie:
- pompa CWU stoi co najmniej **60 min** (krótki zanik Wi-Fi przekaźnika nie zeruje tego czasu, praca pompy — tak),
- nie trwa zrzut ciepła (do domu ani do CWU),
- źródło wężownicy (góra bufora albo kocioł, zależnie od ustawienia „skąd grzana jest wężownica”; bez sondy
  góry bufora jej dół) ma co najmniej **50 °C**,
- rura przy zasobniku ma co najmniej **35 °C** i jest **cieplejsza od zasobnika o ≥ 3 K** — ciepło przewodzone
  z samego zasobnika nie ogrzeje jej ponad zasobnik, więc to ciepło musi płynąć ze źródła. Bez sondy zasobnika
  rura musi mieć temperaturę najwyżej 15 K niższą od źródła,
- i tak jest przez **20 minut**.

Alarm znika przy 2 K histerezie albo gdy pompa ruszy. Rozwiązanie: **zawór zwrotny** (grawitacyjny, z klapką)
na zasilaniu wężownicy albo **syfon termiczny** (pętla rury w dół przy buforze). Uwaga: pompa w pozycji RĘKA
fizycznego przełącznika w skrzynce pracuje mimo wyłączonego przekaźnika — wtedy alarm nie oznacza grawitacji.
Na schemacie odczyt rury jest na zasilaniu wężownicy przy zasobniku (z napisem „⚠ grawitacja”, gdy alarm trwa),
a karta CWU pokazuje, od kiedy pompa stoi.

### Palenie według spalin

Bez czujnika spalin moduł rozpoznaje palenie po temperaturze wody na wyjściu z kotła. Woda reaguje jednak
z opóźnieniem: od rozpalenia do 70 °C mija nawet półtorej godziny, a ocieplony kocioł po wygaśnięciu trzyma
50–70 °C przez wiele godzin, więc palenie kończyło się dopiero po spadku kotła poniżej 60 °C.

Z czujnikiem **Spaliny**:
- **rozpalanie / praca** — spaliny ≥ **100 °C** i rosną (≥ 2 K/min w oknie 10 min) albo stoją na ≥ 120 °C;
  dopóki woda w kotle ma mniej niż 65 °C, stan to „rozpalanie”, potem „praca”. Woda, która sama się ociepla
  (np. od bufora), przy zimnych spalinach nie jest już paleniem;
- **wygasanie** — spaliny spadają (≤ −1 K/min) poniżej 100 °C; w trakcie palenia przejście praca ↔ wygasanie
  pilnuje też temperatura wody, jak dotąd. Ponowny wzrost spalin ponad 100 °C (dołożone drewno) to znów praca;
- **koniec palenia** — spaliny poniżej **80 °C** i nie rosną przez **15 min** (krótka przerwa wentylatora
  wyciągowego przy termostacie kotła nie kończy palenia), niezależnie od tego, jak ciepła jest woda;
- przegrzanie nadal liczy się z temperatury wody.

Gdy czujnik spalin nie ma odczytu, moduł wraca do reguł według wody. Jeśli czujnik czyta, ale za nisko (np. źle
przeliczony sygnał), wyraźne palenie widoczne w wodzie (≥ 65 °C i szybki wzrost) i tak otworzy sesję, a skończy ją
temperatura wody. Odczyt spalin i jego trend są w karcie kotła i na schemacie (czopuch i komin nad kotłem).

**Zawór przy postoju pompy CO.** Gdy pompa CO stoi co najmniej 2 minuty (np. w domu jest ciepło albo trwa
obniżenie nocne), moduł zamyka zawór mieszający do końca. Otwarty zawór pozwala gorącej wodzie z bufora
płynąć grawitacyjnie do grzejników także bez pompy. W jednej z pierwszych nocy było to ok. 17 kWh, czyli
ok. 5 kg drewna, poza kontrolą. Już po 10 minutach postoju zasilanie podskoczyło z 49 do 62 °C, dlatego
zawór zamyka się szybko. Po ponownym starcie pompy zawór otwiera się krokami, a nie od razu na 100 %.
Jeśli grupa pompowa ma hamulec grawitacyjny (zawór zwrotny), zamykanie zaworu dodatkowo zabezpiecza.

**Zawór w trybie Ręczne.** Suwak „Otwarcie zaworu” ustawia zawór na wybrany procent (krok 5 %). Moduł
liczy czas ruchu z szacowanego położenia: z 0 na 40 % przy siłowniku 127 s to ok. 51 s otwierania. Przy 0 %
i 100 % siłownik dojeżdża do krańcówki z zapasem, więc położenie jest znów pewne. Gdy położenie nie jest
znane, pierwszy ruch to kalibracja (pełne zamknięcie), a potem ustawiasz suwak jeszcze raz. W trybie Ręczne
zawór stoi tam, gdzie go ustawisz, także przy wyłączonej pompie. Zamykanie przy postoju pompy działa tylko
w trybie Automatyczne.

**Nagrzewnica za pompą CO (np. garaż).** Nagrzewnica wpięta w obieg CO za pompą dostaje gorącą wodę
zawsze, gdy pracuje pompa CO, ale grzeje dopiero wtedy, gdy kręci się jej wentylator. Wybierz gniazdko
(przekaźnik) wentylatora w sekcji „Czujniki” jako **Nagrzewnica — wentylator**, a w ustawieniach sterowania
nadaj jej nazwę, np. „Garaż”. Moduł włącza wentylator **2 minuty po starcie pompy CO**, gdy nagrzewnica
jest już ciepła i nie dmucha zimnym powietrzem. Wyłącza go **2 minuty po zatrzymaniu pompy**, bo woda
w nagrzewnicy jest jeszcze gorąca. Przy **zrzucie ciepła** (kocioł przegrzany) wentylator rusza **od razu**
razem z pompą, bo woda jest gorąca, a nadmiar ciepła trzeba odebrać jak najszybciej. Liczy się stan samej pompy, czyli to, czy ją włączył moduł, pozycja RĘKA,
zrzut ciepła czy skrypt bezpieczeństwa na Shelly. Wentylator przełączony ręcznie moduł zaraz ustawi z powrotem
tak jak pompę, chyba że pompa zmieniła stan mniej niż 2 minuty temu. Gdy gniazdko jest poza siecią,
moduł nic do niego nie wysyła; gdy wróci, dostaje stan pompy. W trybie „Wyłączone” wentylator nie jest
przełączany. Stan wentylatora widać na schemacie kotłowni i w karcie „Sterowanie”. Z encją mocy gniazdka
(**Nagrzewnica — moc wentylatora**) pod garażem jest zawsze pobór „⚡ … W”, a wirnik kręci się tylko wtedy, gdy wentylator
naprawdę pobiera prąd (≥ 5 W). Gdy gniazdko jest włączone od minuty, a poboru nie ma, schemat pisze
„włączony, nie chodzi” i „⚡ 0 W — wypięty?”, a karta „Sterowanie” — „wypięta wtyczka albo wyłączona
nagrzewnica?”.

**Nagrzewnica w nocy.** Gdy w nocy nagrzewnica nie jest potrzebna (np. w garażu), włącz w karcie „Sterowanie”
ramkę **„… — cisza nocna wentylatora”** (przełącznik „wyłączaj w nocy”) i wybierz godziny z list — zapisują się
od razu, jak godziny obniżenia nocnego domu (domyślnie **23–7**; to samo ustawisz też w ustawieniach sterowania). O pierwszej godzinie moduł
od razu wyłącza wentylator i do końca nocy nie włącza go razem z pompą CO. Od drugiej godziny wentylator
znów chodzi z pompą: jeśli pompa pracuje już dłużej niż 2 minuty, rusza od razu. Wyjątkiem jest **zrzut ciepła**
z przegrzanego kotła: wtedy wentylator pracuje także w nocy, bo nadmiar ciepła trzeba odebrać.

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
| Brak odczytu czujnika (−127 °C, nagłe 85,0 °C, niedostępny > 2 min; skok > 10 K przyjmowany po 3 nowych odczytach lub 60 s; czujnik spalin: poza −20…500 °C, skok > 80 K) | ostrzeżenie; krytyczny w trakcie palenia (sonda kotła / góry bufora) |
| Ciepło płynie do CWU grawitacyjnie (pompa CWU stoi ≥ 60 min, a rura wężownicy przy zasobniku zostaje ciepła — szczegóły wyżej) | ostrzeżenie (po 20 min) |
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
- **Drewno spalone w paleniu** (tabela paleń, sezon) to bilans ciepła: o ile więcej ciepła mają na koniec
  bufor i CWU niż na początku, plus to, co w tym czasie wziął dom (jego straty ciepła razem z temperaturą
  na zewnątrz). Dzieli się to przez ciepło z kilograma drewna. Samo „ile przybyło w buforze” zaniżało wynik
  2–3 razy przy długich paleniach, w których bufor pełny zostaje dociążany kolejnymi załadunkami.
  Straty domu biorą się z modelu domu, a zanim on się nauczy — z tempa rozładowania bufora w godzinach
  bez palenia (wystarczą ok. 2 h takich danych przy co najmniej 5 K różnicy dom – zewnątrz). Palenia
  zarejestrowane wcześniej, i te bez tych danych, liczą tylko bufor i CWU (panel pisze wtedy „bufor i CWU”).

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

Bufor jest narysowany tak, jak zaleca się go podłączać: zasilanie wężownicy CWU wychodzi z samej góry (najgorętsza
warstwa), zasilanie CO — trochę niżej, przez zawór mieszający i pompę CO; powrót z grzejników wchodzi na dół.
Nad kotłem stoi komin, z którego w trakcie palenia unosi się dym. Czujnik spalin, gdy jest przypisany, ma
punkt na czopuchu i ramkę „spaliny” z temperaturą i strzałką trendu. Zasobnik CWU ma wężownicę w dolnej części,
jak prawdziwy zasobnik, a temperaturę wody (z procentem naładowania i kWh) w górnej. Rura zasilania wężownicy,
gdy ma czujnik, pokazuje „zasilanie” z temperaturą w punkcie tuż przy wejściu do zasobnika (na wykresie doby
jako „Wężownica CWU”).

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
8. Czujnik spalin: przy rozpalaniu odczyt rośnie w ciągu kilku minut, a stan kotła zmienia się na
   „rozpalanie” zanim woda w kotle zacznie rosnąć; po wygaśnięciu palenie kończy się ok. 15 min po spadku
   spalin poniżej 80 °C.
9. Rura wężownicy CWU: wieczorem, przy gorącym buforze, pompa CWU w pozycji 0 na ≥ 1 h. Rura stygnie do
   temperatury otoczenia — brak krążenia; zostaje cieplejsza od zasobnika — krążenie grawitacyjne (alarm).
