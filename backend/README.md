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
│   │   ├── database.py    SQLAlchemy engine/session
│   │   ├── security.py    Password hashing, JWT, device key generation
│   │   ├── deps.py        Auth dependencies (get_current_user, get_current_device)
│   │   └── errors.py      Consistent error envelope + exception handlers
│   ├── models/
│   │   └── models.py       SQLAlchemy models: User, Device, TelemetryReading, Command
│   ├── schemas/            Pydantic request/response schemas, by resource
│   └── routers/            One router per resource: auth, devices, telemetry, commands, health
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

## Device provisioning
New devices must be registered once before they can send telemetry:
```bash
curl -X POST http://localhost:8000/api/v1/devices/register \
  -H "Content-Type: application/json" \
  -H "X-Provision-Key: <DEVICE_PROVISION_KEY from .env>" \
  -d '{"device_id":"ESP32_001","name":"Home Energy Monitor"}'
```
The response's `device_key` is shown once — paste it into the ESP32
firmware's `config.h`.
