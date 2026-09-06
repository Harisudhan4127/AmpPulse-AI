# AmpPulse AI — MVP Implementation

IoT smart energy monitoring: **ESP32 → Wi-Fi → Laptop (Backend + DB) → Frontend**.

This repository extends the original static demo site with a real, working
local backend and ESP32 firmware, following the architecture:

```
ESP32 (sensors + relay)
   │  Wi-Fi, HTTP, JSON, X-Device-Key auth
   ▼
Laptop Backend (FastAPI + SQLite)
   │  REST, JSON, JWT auth
   ▼
Frontend (browser)
```

## Folder structure

```
amppulse/
├── backend/            FastAPI backend - the local host / API server / database
├── esp32-firmware/      PlatformIO ESP32 firmware (modular: wifi, api, sensors, relay)
├── frontend/            Updated static frontend, wired to the real backend
└── docs/                Architecture, API spec, DB schema, deployment, security, testing
```

## Quick start (see docs/DEPLOYMENT.md for full detail)

### ⚡ One command (interactive menu) — recommended
```bash
python3 setup_esp32.py
```
Opens a menu that can: register the ESP32 (auto-generates ID + key), paste the
key into the upload code, flash the board, and launch the full GUI (backend +
frontend). Also available: `--gui`, `--status`, `--ssid/#`+`--password` one-shot.

### Manual quick start

```bash
# 1. Backend
cd backend
cp .env.example .env          # edit values (JWT secret, provisioning key)
pip install -r requirements.txt --break-system-packages
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 2. Register a device (once per physical ESP32)
curl -X POST http://localhost:8000/api/v1/devices/register \
  -H "Content-Type: application/json" \
  -H "X-Provision-Key: <your DEVICE_PROVISION_KEY from .env>" \
  -d '{"device_id":"ESP32_001","name":"Home Energy Monitor"}'
# -> copy the returned device_key into esp32-firmware/include/config.h

# 3. ESP32 firmware
cd ../esp32-firmware
cp include/config.example.h include/config.h   # fill in Wi-Fi + backend IP + device_key
pio run -t upload

# 4. Frontend
cd ../frontend
python3 -m http.server 5500
# open http://localhost:5500, login with demo@amppulse.ai / Demo@12345
```

## Documentation index
- `docs/ARCHITECTURE.md` — MVP + production architecture, data flow, evolution stages
- `docs/API_SPEC.md` — full REST API reference
- `docs/DATABASE.md` — schema and relationships
- `docs/SECURITY.md` — auth model, MVP simplifications vs production requirements
- `docs/DEPLOYMENT.md` — step-by-step laptop + ESP32 setup, troubleshooting
- `docs/TESTING.md` — test strategy for ESP32, backend, frontend, end-to-end
- `docs/PRODUCTION.md` — migration plan and production-readiness checklist

## What's demo vs what's real
- **Real, backend-driven**: Home dashboard (live power/voltage/energy/cost,
  appliance ON/OFF control via relay commands, bill projection from actual
  telemetry).
- **Still demo/illustrative**: Organization, Industrial, and Government
  dashboards — the current hardware (one ESP32, 2-channel relay) doesn't yet
  support multi-building/multi-machine scenarios. See docs/ARCHITECTURE.md
  for how these would be extended.
- **Simulated pending hardware**: energy readings (current, power, energy_kwh,
  frequency), because PZEM-004T isn't wired yet per the project notes. Voltage
  (ZMPT101B) and temperature/humidity (DHT22) use simplified real-sensor reads.
