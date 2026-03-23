# n8n — Instrukcja integracji z serwerem licencji Smarting HOME

## Endpointy

| Operacja | Metoda | URL |
|----------|--------|-----|
| Tworzenie licencji | POST | `https://mslvyiimjevhvujojfax.supabase.co/rest/v1/rpc/sh_create_license` |
| Walidacja licencji | POST | `https://mslvyiimjevhvujojfax.supabase.co/rest/v1/rpc/sh_validate_license` |
| Sprawdzenie statusu | POST | `https://mslvyiimjevhvujojfax.supabase.co/rest/v1/rpc/sh_check_license_status` |
| Revokacja licencji | POST | `https://mslvyiimjevhvujojfax.supabase.co/rest/v1/rpc/sh_revoke_license` |

---

## 1. Tworzenie licencji po płatności

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

## 3. Revokacja licencji

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

## 4. Sprawdzenie statusu

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

## 5. Supabase Credentials w n8n

W n8n utwórz **Supabase Credentials** lub użyj **HTTP Request** z nagłówkami:

```
apikey: <wartość z .env → VITE_SUPABASE_ANON_KEY>
Authorization: Bearer <Service Role Key z Supabase Dashboard → Settings → API>
```

> ⚠️ **Service Role Key** jest potrzebny bo tabele `sh_*` mają RLS z polityką `service_role` only.  
> Znajdziesz go w: Supabase Dashboard → Project Settings → API → `service_role` key.

---

## 6. Przykładowy flow n8n

```
Webhook (Stripe/WC) → HTTP Request (sh_create_license) → Send Email (license_key do klienta)
```

Opcjonalnie:
```
Webhook (Refund) → HTTP Request (sh_revoke_license) → Send Email (powiadomienie o anulowaniu)
```

---

## 7. Podsumowanie typów licencji

| Typ | `p_tier` | `p_months` | Opis |
|-----|----------|------------|------|
| PRO miesięczna | `pro` | `1` | 1 miesiąc |
| PRO roczna | `pro` | `12` | 12 miesięcy |
| PRO dożywotnia | `pro` | `0` | Nigdy nie wygasa |
| ENTERPRISE roczna | `enterprise` | `12` | 12 miesięcy, do 5 instalacji |
| ENTERPRISE dożywotnia | `enterprise` | `0` | Nigdy nie wygasa, do 5 instalacji |
