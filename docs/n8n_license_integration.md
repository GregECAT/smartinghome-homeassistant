# n8n — Instrukcja integracji z serwerem licencji Smarting HOME

## Endpointy

### Supabase RPC (wymaga Service Role Key)

| Operacja | Metoda | URL |
|----------|--------|-----|
| Tworzenie licencji | POST | `https://mslvyiimjevhvujojfax.supabase.co/rest/v1/rpc/sh_create_license` |
| Walidacja licencji | POST | `https://mslvyiimjevhvujojfax.supabase.co/rest/v1/rpc/sh_validate_license` |
| Sprawdzenie statusu | POST | `https://mslvyiimjevhvujojfax.supabase.co/rest/v1/rpc/sh_check_license_status` |
| Revokacja licencji | POST | `https://mslvyiimjevhvujojfax.supabase.co/rest/v1/rpc/sh_revoke_license` |
| Rejestracja FREE | POST | `https://mslvyiimjevhvujojfax.supabase.co/rest/v1/rpc/sh_register_free` |

### Edge Function API (publiczne, bez JWT)

| Operacja | Metoda | URL |
|----------|--------|-----|
| Walidacja licencji | POST | `https://mslvyiimjevhvujojfax.supabase.co/functions/v1/sh-license-api/validate` |
| Sprawdzenie statusu | GET | `https://mslvyiimjevhvujojfax.supabase.co/functions/v1/sh-license-api/status` |
| Rejestracja FREE | POST | `https://mslvyiimjevhvujojfax.supabase.co/functions/v1/sh-license-api/register-free` |

### Webhook powiadomień o nowych instalacjach

| Operacja | Metoda | URL |
|----------|--------|-----|
| Nowa instalacja FREE | POST | `https://a.gregciupek.com/webhook/7cb899c0-5f2c-40f2-8d65-7dcbbbb28f01` |

---

## 1. Tworzenie licencji PRO/ENTERPRISE po płatności

### n8n Node: HTTP Request

Po webhook płatności (Stripe, WooCommerce, itp.) dodaj **HTTP Request** node:

```
Metoda: POST
URL: https://mslvyiimjevhvujojfax.supabase.co/rest/v1/rpc/sh_create_license

Headers:
  apikey: <SUPABASE_ANON_KEY>
  Authorization: Bearer <SUPABASE_SERVICE_KEY>
  Content-Type: application/json

Body (JSON):
{
  "p_email": "{{ $json.email }}",
  "p_tier": "pro",
  "p_months": 12,
  "p_payment_ref": "{{ $json.payment_id }}"
}
```

### Odpowiedź:
```json
{
  "success": true,
  "license_key": "SH-PRO-A1B2C3D-E4F5-G6H7-I8J9",
  "tier": "pro",
  "lifetime": false,
  "expires_at": "2027-03-21 20:57:00+00",
  "email": "klient@example.com"
}
```

> Klucz `license_key` wyślij klientowi mailem (kolejny node w n8n: Send Email).

---

## 2. Parametry `sh_create_license`

| Parametr | Typ | Wymagany | Opis |
|----------|-----|----------|------|
| `p_email` | string | ✅ | Email klienta |
| `p_tier` | string | ❌ | `pro` (domyślny) lub `enterprise` |
| `p_months` | int | ❌ | Ile miesięcy (domyślnie: 12). **`0` = licencja dożywotnia** |
| `p_payment_ref` | string | ❌ | ID płatności z systemu |
| `p_notes` | string | ❌ | Notatki admina |

### Przykłady:

**PRO 12 miesięcy:**
```json
{
  "p_email": "jan@example.pl",
  "p_tier": "pro",
  "p_months": 12,
  "p_payment_ref": "stripe_pi_xxx123"
}
```

**PRO dożywotnia (lifetime):**
```json
{
  "p_email": "jan@example.pl",
  "p_tier": "pro",
  "p_months": 0,
  "p_payment_ref": "stripe_pi_xxx456",
  "p_notes": "Licencja dożywotnia - early adopter"
}
```

Odpowiedź dla licencji dożywotniej:
```json
{
  "success": true,
  "license_key": "SH-PRO-X1Y2Z3W-A4B5-C6D7-E8F9",
  "tier": "pro",
  "lifetime": true,
  "expires_at": "lifetime",
  "email": "jan@example.pl"
}
```

**ENTERPRISE 24 miesiące:**
```json
{
  "p_email": "firma@example.pl",
  "p_tier": "enterprise",
  "p_months": 24,
  "p_payment_ref": "woo_order_456"
}
```

**ENTERPRISE dożywotnia:**
```json
{
  "p_email": "firma@example.pl",
  "p_tier": "enterprise",
  "p_months": 0,
  "p_payment_ref": "woo_order_789"
}
```

---

## 3. Rejestracja licencji FREE (nowa instalacja)

Licencja FREE nie wymaga klucza — rejestracja odbywa się automatycznie przy starcie integracji
w trybie FREE. Dane trafiają do tabeli `sh_free_registrations`.

### Metoda A: Edge Function API (używana przez integrację HA)

```
Metoda: POST
URL: https://mslvyiimjevhvujojfax.supabase.co/functions/v1/sh-license-api/register-free

Headers:
  Content-Type: application/json

Body (JSON):
{
  "device_id": "7611369d4456498faf570feaf7bdc35d",
  "ha_version": "2026.3.4",
  "integration_version": "1.47.5"
}
```

> ℹ️ JWT nie jest wymagany (`verify_jwt: false`). Integracja HA korzysta z tego endpointu.

### Metoda B: Supabase RPC (wymaga Service Role Key)

```
Metoda: POST
URL: https://mslvyiimjevhvujojfax.supabase.co/rest/v1/rpc/sh_register_free

Headers:
  apikey: <SUPABASE_ANON_KEY>
  Authorization: Bearer <SUPABASE_SERVICE_KEY>
  Content-Type: application/json

Body (JSON):
{
  "p_device_id": "manual-device-001",
  "p_ha_version": "2026.3.4",
  "p_integration_version": "1.47.5"
}
```

### Parametry `sh_register_free`

| Parametr | Typ | Wymagany | Opis |
|----------|-----|----------|------|
| `p_device_id` | string | ✅ | UUID instancji Home Assistant (unique) |
| `p_ha_version` | string | ❌ | Wersja Home Assistant (np. `2026.3.4`) |
| `p_integration_version` | string | ❌ | Wersja integracji Smarting HOME (np. `1.47.5`) |

### Logika UPSERT

Funkcja `sh_register_free` robi **UPSERT** na `device_id`:
- **Nowy device** → INSERT z `ping_count = 1`, `first_seen = now()`
- **Istniejący device** → UPDATE `last_seen = now()`, `ha_version`, `integration_version`, `ping_count + 1`

### Odpowiedź:
```json
{"ok": true}
```

### Schemat tabeli `sh_free_registrations`

| Kolumna | Typ | Opis |
|---------|-----|------|
| `id` | uuid | PK, auto-generated |
| `device_id` | text | UUID instancji HA (**unique**) |
| `ha_version` | text | Wersja Home Assistant |
| `integration_version` | text | Wersja Smarting HOME |
| `ip_address` | text | IP (nullable, nie wypełniane automatycznie) |
| `country` | text | Kraj (nullable, nie wypełniane automatycznie) |
| `first_seen` | timestamptz | Data pierwszej rejestracji |
| `last_seen` | timestamptz | Data ostatniego pingu |
| `ping_count` | integer | Licznik pingów (ile razy uruchomiono integrację) |

### Przykład cURL:
```bash
curl -X POST \
  "https://mslvyiimjevhvujojfax.supabase.co/functions/v1/sh-license-api/register-free" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "test-manual-001",
    "ha_version": "2026.3.4",
    "integration_version": "1.47.5"
  }'
```

---

## 4. Webhook — powiadomienie o nowej instalacji FREE

Przy każdym starcie integracji w trybie FREE, oprócz rejestracji w bazie, wysyłany jest
webhook z informacjami o instalacji. Służy do monitorowania nowych użytkowników.

### Endpoint

```
POST https://a.gregciupek.com/webhook/7cb899c0-5f2c-40f2-8d65-7dcbbbb28f01
```

### Payload wysyłany automatycznie przez integrację:

```json
{
  "event": "new_free_installation",
  "device_id": "7611369d4456498faf570feaf7bdc35d",
  "ha_version": "2026.3.4",
  "integration_version": "1.47.5",
  "inverter_brand": "goodwe",
  "tariff": "g13",
  "energy_provider": "tauron",
  "license_tier": "free"
}
```

### Pola payloadu:

| Pole | Typ | Opis |
|------|-----|------|
| `event` | string | Zawsze `"new_free_installation"` |
| `device_id` | string | UUID instancji Home Assistant |
| `ha_version` | string | Wersja HA |
| `integration_version` | string | Wersja Smarting HOME |
| `inverter_brand` | string | Marka falownika: `goodwe`, `deye`, `growatt`, `sofar`, `other` |
| `tariff` | string | Taryfa: `g11`, `g12`, `g12w`, `g12n`, `g13`, `dynamic` |
| `energy_provider` | string | Sprzedawca: `tauron`, `pge` |
| `license_tier` | string | Zawsze `"free"` |

> ℹ️ Webhook jest fire-and-forget — błędy nie blokują startu integracji.
> Kod źródłowy: `license.py` → `_register_free_ping()`

### Przykładowy flow n8n (webhook → powiadomienie):
```
Webhook (nowa instalacja) → IF (event == "new_free_installation")
  → Slack/Email/Telegram (powiadomienie admina)
  → Google Sheets (log instalacji)
```

---

## 5. Revokacja licencji

```
Metoda: POST
URL: https://mslvyiimjevhvujojfax.supabase.co/rest/v1/rpc/sh_revoke_license

Body:
{
  "p_key": "SH-PRO-A1B2C3D-E4F5-G6H7-I8J9",
  "p_reason": "Refund requested"
}
```

---

## 6. Sprawdzenie statusu

```
Metoda: POST
URL: https://mslvyiimjevhvujojfax.supabase.co/rest/v1/rpc/sh_check_license_status

Body:
{
  "p_key": "SH-PRO-A1B2C3D-E4F5-G6H7-I8J9"
}
```

Odpowiedź (dla licencji dożywotniej):
```json
{
  "valid": true,
  "tier": "pro",
  "lifetime": true,
  "expires": "lifetime",
  "email": "jan@example.pl",
  "max_installations": 1,
  "features": ["sensors", "binary_sensors", "..."],
  "message": "Lifetime license active"
}
```

---

## 7. Supabase Credentials w n8n

W n8n utwórz **Supabase Credentials** lub użyj **HTTP Request** z nagłówkami:

```
apikey: <wartość z .env → VITE_SUPABASE_ANON_KEY>
Authorization: Bearer <Service Role Key z Supabase Dashboard → Settings → API>
```

> ⚠️ **Service Role Key** jest potrzebny bo tabele `sh_*` mają RLS z polityką `service_role` only.  
> Znajdziesz go w: Supabase Dashboard → Project Settings → API → `service_role` key.
>
> ℹ️ Edge Function API (`/functions/v1/sh-license-api/*`) **nie wymaga JWT** — używa wewnętrznie Service Role Key.

---

## 8. Przykładowe flow n8n

### Nowa licencja PRO (po płatności):
```
Webhook (Stripe/WC) → HTTP Request (sh_create_license) → Send Email (license_key do klienta)
```

### Refund:
```
Webhook (Refund) → HTTP Request (sh_revoke_license) → Send Email (powiadomienie o anulowaniu)
```

### Monitoring nowych instalacji FREE:
```
Webhook (7cb899c0...) → IF (event == "new_free_installation")
  → Slack (🆕 Nowa instalacja: {{ $json.inverter_brand }} / {{ $json.tariff }})
  → Google Sheets (log)
```

---

## 9. Podsumowanie typów licencji

| Typ | Tabela | `p_tier` | `p_months` | Opis |
|-----|--------|----------|------------|------|
| **FREE** | `sh_free_registrations` | — | — | Bez klucza, auto-rejestracja, podstawowe funkcje |
| PRO miesięczna | `sh_licenses` | `pro` | `1` | 1 miesiąc |
| PRO roczna | `sh_licenses` | `pro` | `12` | 12 miesięcy |
| PRO dożywotnia | `sh_licenses` | `pro` | `0` | Nigdy nie wygasa |
| ENTERPRISE roczna | `sh_licenses` | `enterprise` | `12` | 12 miesięcy, do 5 instalacji |
| ENTERPRISE dożywotnia | `sh_licenses` | `enterprise` | `0` | Nigdy nie wygasa, do 5 instalacji |

---

## 10. Architektura bazy licencji

```
sh_licenses                    ← PRO / ENTERPRISE (klucz licencyjny)
  ├── sh_license_activations   ← Aktywne urządzenia przypisane do licencji
  └── sh_license_events        ← Log zdarzeń (created, validated, revoked...)

sh_free_registrations          ← FREE (rejestracja po device_id, bez klucza)
```

### Dostępne RPC:

| Funkcja | Parametry | Opis |
|---------|-----------|------|
| `sh_create_license` | `p_email`, `p_tier`, `p_months`, `p_payment_ref`, `p_notes` | Tworzy nową licencję PRO/ENTERPRISE |
| `sh_validate_license` | `p_key`, `p_device_id`, `p_ha_version` | Pełna walidacja z aktywacją urządzenia |
| `sh_check_license_status` | `p_key` | Lekkie sprawdzenie statusu (periodic check) |
| `sh_revoke_license` | `p_key`, `p_reason` | Revokacja licencji |
| `sh_register_free` | `p_device_id`, `p_ha_version`, `p_integration_version` | Rejestracja/ping urządzenia FREE |
