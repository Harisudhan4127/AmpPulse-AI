# AmpPulse AI — Backend

FastAPI + SQLite local backend. See `../docs/API_SPEC.md` for the full API
reference and `../docs/DEPLOYMENT.md` for setup steps.

## Structure
```
backend/
├── app/
│   ├── main.py           FastAPI app, CORS, startup seeding, exception handlers
│   ├── core/
│   │   ├── config.py      Settings loaded from .env
│   │   ├── database.py    SQLAlchemy engine/session + schema auto-migration
│   │   ├── security.py    Password hashing, JWT, device key generation
│   │   ├── deps.py        Auth dependencies (get_current_user, get_current_device)
│   │   └── errors.py      Consistent error envelope + exception handlers
│   ├── models/
│   │   └── models.py       SQLAlchemy models: User (+ multi-ESP32 table), Device,
│   │                        TelemetryReading, Command
│   ├── schemas/            Pydantic request/response schemas, by resource
│   └── routers/            One router per resource:
│      auth, devices, telemetry, commands, health, user_settings
├── requirements.txt
└── .env.example
```

## Run
```bash
pip install -r requirements.txt --break-system-packages
cp .env.example .env   # edit values
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
Swagger UI: `http://localhost:8000/docs`

## Per-user ESP32 direct connections (dashboard ↔ device)

The dashboard can talk straight to each ESP32's own web server (port 80) by IP.
A user can save **multiple** ESP32s and switch between them; the list is
remembered **per login** so it auto-reconnects on the next visit:

```bash
# Register a new account (auto-login, returns a JWT) — optional; demo@amppulse.ai already exists
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"jane@example.com","password":"secret1","name":"Jane"}'

# Save/Add a direct ESP32 target (probes reachability + latency)
curl -X POST http://localhost:8000/api/v1/user/esp32 \
  -H "Authorization: Bearer <jwt>" -H "Content-Type: application/json" \
  -d '{"ip":"192.168.1.8","port":80,"name":"Living Room"}'

# List all saved ESP32s (each with a live probe: connected / latency_ms / error)
curl http://localhost:8000/api/v1/user/esp32s \
  -H "Authorization: Bearer <jwt>"

# Update one (ip / port / name, partial)
curl -X PUT http://localhost:8000/api/v1/user/esp32/1 \
  -H "Authorization: Bearer <jwt>" -H "Content-Type: application/json" \
  -d '{"name":"Main Hall"}'

# Forget one (back to backend-only mode for that device)
curl -X DELETE http://localhost:8000/api/v1/user/esp32/1 \
  -H "Authorization: Bearer <jwt>"
```

The `user_esp32s` table is created automatically on startup. A legacy
single-IP (`esp32_ip` on `users`) is migrated into `user_esp32s` the first
time the backend boots against an old database.

## Device provisioning (backend telemetry mode)

The standalone sketch in `arduino/` doesn't post telemetry — it serves it
directly. If you instead want backend analytics (bill prediction, energy
usage, monthly reports), use the modular `esp32-firmware` (PlatformIO), which
registers once and then POSTs telemetry:

```bash
curl -X POST http://localhost:8000/api/v1/devices/register \
  -H "Content-Type: application/json" \
  -H "X-Provision-Key: <DEVICE_PROVISION_KEY from .env>" \
  -d '{"device_id":"ESP32_001","name":"Home Energy Monitor"}'
```
The response's `device_key` is shown once — paste it into the ESP32
firmware's `config.h`. See `../docs/DEPLOYMENT.md`.