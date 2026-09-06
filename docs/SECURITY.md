# Security Design

## Identity chain
```
Device identity (device_id)
      ↓
Device authentication (X-Device-Key, bcrypt-hashed at rest, checked per request)
      ↓
API authorization (device may only act on ITS OWN device_id — enforced by
                    get_current_device comparing the path device_id against
                    the key's owning row)
      ↓
Validated request (Pydantic schema: types, ranges, enums)
      ↓
Database (only the backend process touches it — SQLAlchemy ORM, parameterized
          queries, no raw string SQL)
```

Users follow a parallel, separate chain: email/password → bcrypt-verified →
JWT (HS256, expires in `JWT_EXPIRE_MINUTES`) → `Authorization: Bearer` checked
on every user-facing endpoint via `get_current_user`.

**Why two separate schemes**: a compromised device key only ever grants
control over that one device's telemetry/commands — it cannot log in as a
user, list other devices, or reach any endpoint gated by `get_current_user`.
Symmetrically, a stolen user JWT cannot post telemetry or be used to
impersonate a device, because device endpoints require `X-Device-Key`, not
a bearer token.

## What is NEVER done
- Database credentials are not present in ESP32 firmware or frontend code —
  only the backend process holds `DATABASE_URL`.
- Server secrets (`JWT_SECRET`, `DEVICE_PROVISION_KEY`) live only in the
  backend's `.env`, which is gitignored and never shipped to firmware or
  frontend.
- ESP32 firmware never talks to the database directly — only to the REST API.
- Frontend never talks to the database directly — only to the REST API.
- Device-submitted telemetry is never trusted blindly: every field is
  range-validated (voltage 0–400V, current 0–100A, etc.) before being stored.

## MVP simplifications (local development) — and what changes for production

| Area | MVP (local) | Production requirement |
|---|---|---|
| Transport | Plain HTTP on local Wi-Fi | HTTPS/TLS via reverse proxy (Nginx/Caddy) or direct TLS on ESP32 (heavier, usually proxy is simpler) |
| Device provisioning | Shared `DEVICE_PROVISION_KEY` in `.env`, callable by anyone who has it | Authenticated installer/admin flow; provisioning key rotated per install or replaced by short-lived provisioning tokens |
| Device→user assignment | Manual/implicit (unowned devices shown to any logged-in user) | Explicit device claiming flow (e.g. QR code/pairing code tied to a user account) |
| Rate limiting | None | Add per-device and per-user rate limits (e.g. slowapi/starlette-limiter) to prevent abuse/DoS |
| Secrets storage | Plaintext `.env` file on the laptop | Managed secret store (AWS Secrets Manager, Vault, or platform env vars), never in source control |
| JWT secret | Static value in `.env` | Rotated periodically; consider short-lived access tokens + refresh tokens |
| Password policy | None enforced | Minimum length/complexity, breach-list checking, rate-limited login attempts |
| Device key rotation | Not implemented (key issued once, forever) | Support key rotation/revocation endpoint; ESP32 firmware should handle "key rejected" by entering a re-provisioning mode |
| Input validation | Pydantic type/range checks | Same, plus anomaly detection (e.g. reject physically implausible jumps) as a second layer |
| CORS | Wide-open localhost origins in `.env.example` | Restrict to exact production frontend origin(s) |
| Logging | Console/stdout only | Centralized structured logging + alerting on repeated auth failures |
| OTA firmware | Manual USB flashing | Signed OTA updates so devices can't be tricked into running malicious firmware |

## Consistent error responses
Every error follows:
```json
{"success": false, "error": {"code": "DEVICE_NOT_FOUND", "message": "Device does not exist."}}
```
This means error-handling code (both in ESP32 firmware and the frontend's
`Api._request`) has exactly one shape to parse, in dev and in production
alike — nothing changes when the transport becomes HTTPS.

## Handling specific failure modes
| Situation | Backend behavior | ESP32 behavior | Frontend behavior |
|---|---|---|---|
| Wi-Fi disconnected | n/a | `WiFiManager` retries every `WIFI_RECONNECT_DELAY_MS`; telemetry/commands skipped until reconnected | n/a |
| Backend unavailable | n/a | Telemetry POST fails, error code `BACKEND_UNREACHABLE`, exponential backoff (capped 6×) | `Api._request` catches fetch failure → `BACKEND_UNREACHABLE`; dashboard shows "● OFFLINE" |
| Invalid JSON from ESP32 | 422 `VALIDATION_ERROR` with field-level message | n/a (firmware always sends well-formed JSON via ArduinoJson) | n/a |
| Invalid sensor data (out of range) | 422 `VALIDATION_ERROR` | Logged to Serial, reading dropped, next cycle tried | n/a |
| Auth failure (bad device key) | 401 `UNAUTHORIZED` | Logged; firmware currently does not auto re-provision — flagged as a production TODO | n/a |
| Timeout | n/a | `HTTPClient` timeout set to `HTTP_TIMEOUT_MS` (4s); treated as failure, triggers backoff | Standard fetch has no explicit timeout in MVP — production should add `AbortController` |
| Device offline | Derived from `last_seen_at` vs `DEVICE_OFFLINE_AFTER_SECONDS` | n/a | Dashboard shows "Device offline" banner and stale-data indicators |
| Duplicate device registration | 409 `DEVICE_ALREADY_EXISTS` | n/a — one-time manual step | n/a |
