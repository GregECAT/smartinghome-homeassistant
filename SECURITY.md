# Bezpieczeństwo — Smarting HOME

**PL** · [EN below](#security-policy-english)

**Producent oprogramowania:** JG INVEST P.S.A., ul. Jana Matejki 2, 32-600 Oświęcim, KRS 0000915798,
NIP 5492463888 · kontakt w sprawach bezpieczeństwa: kontakt@smartinghome.pl

## Jak zgłosić podatność

Nie zakładaj publicznego zgłoszenia (issue) dla podatności. Zgłoś ją prywatnie:

1. **GitHub:** zakładka *Security* → *Report a vulnerability* w tym repozytorium (prywatne zgłoszenie), albo
2. **e-mail:** kontakt@smartinghome.pl z tematem „SECURITY”.

Podaj: wersję integracji (Ustawienia → Informacje), wersję Home Assistant, opis problemu, kroki do odtworzenia
i — jeśli wiesz — możliwy wpływ. Nie wysyłaj kluczy API ani danych osobowych.

## Co robimy ze zgłoszeniem

| Krok | Termin |
|---|---|
| Potwierdzenie otrzymania | do 3 dni roboczych |
| Wstępna ocena (czy to podatność, jaki wpływ) | do 7 dni |
| Poprawka w wydaniu | zależnie od wagi; krytyczne — najszybciej, jak się da |
| Informacja dla zgłaszającego o wydaniu poprawki | przy publikacji wydania |

Podatności aktywnie wykorzystywane i poważne incydenty zgłaszamy w terminach rozporządzenia (UE) 2024/2847
(Cyber Resilience Act, art. 14). Zgłaszającego wymieniamy w opisie poprawki, jeśli tego chce.

Prosimy o skoordynowane ujawnienie: nie publikuj szczegółów, zanim nie wydamy poprawki albo nie minie 90 dni
od zgłoszenia — co nastąpi wcześniej. Działania w dobrej wierze, w granicach własnej instalacji, bez szkody dla
innych i bez dostępu do cudzych danych nie będą przez nas ścigane.

## Wspierane wersje

Poprawki bezpieczeństwa wydajemy w najnowszej wersji integracji, bezpłatnie dla wszystkich użytkowników
(FREE i PRO). Aktualizuj integrację przez HACS; w wersji PRO może to robić moduł automatycznych aktualizacji.

## Zakres

Integracja `custom_components/smartinghome`, jej panele oraz usługi Smarting HOME, z którymi się łączy
(weryfikacja licencji, powiadomienia). Podatności Home Assistant, HACS, integracji falowników i firmware
urządzeń zgłaszaj ich twórcom.

---

## Security policy (English)

Software manufacturer: JG INVEST P.S.A., ul. Jana Matejki 2, 32-600 Oświęcim, Poland (KRS 0000915798).

Please do **not** open a public issue for a vulnerability. Report it privately via GitHub (*Security* →
*Report a vulnerability*) or by e-mail to kontakt@smartinghome.pl with the subject "SECURITY". Include the
integration and Home Assistant versions, a description, reproduction steps and the possible impact.

We acknowledge reports within 3 working days and give a first assessment within 7 days. Actively exploited
vulnerabilities and severe incidents are reported within the deadlines of Regulation (EU) 2024/2847 (Cyber
Resilience Act, Art. 14). We ask for coordinated disclosure (until a fix is released or 90 days after the report,
whichever comes first). Good-faith research limited to your own installation will not be pursued.

Security fixes are released free of charge for all users (FREE and PRO) in the latest version.
