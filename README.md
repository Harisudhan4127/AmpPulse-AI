# AmpPulse AI — MVP Implementation

IoT smart energy monitoring with an **ESP32** and a **laptop backend + dashboard**.
Two complementary connection modes:

```
Direct mode (live + control):           Backend mode (analytics):
  Dashboard ──fetch──▶ ESP32 web server     ESP32 ──POST telemetry──▶ Backend (FastAPI + SQLite)
             http://<esp32-ip>:80                (modular esp32-firmware path)
             /data, /relay1/on|off,        ──▶ Dashboard ──JWT──▶ Backend
             /relay2/on|off                      login, history, bill prediction,
              (IP saved per login)               energy usage, monthly reports
```

The standalone sketch (`arduino/amppulse_esp32/amppulse_esp32.ino`) runs its
**own web server on port 80** — no device registration, no keys. The dashboard
connects to it directly by IP for live voltage/current/power/temp/humidity and
instant relay control, while the FastAPI backend (port 8000) handles login,
history, analytics and cost prediction.

## Folder structure

```
amppulse/
├── arduino/amppulse_esp32/   Standalone ESP32 sketch - own web server on :80
│                              (/data JSON + relay endpoints), CORS enabled
├── backend/                   FastAPI + SQLite backend (outh, analytics, reports)
├── frontend/                  Dashboard (index.html + app.js + styles.css)
├── esp32-firmware/            Optional modular PlatformIO firmware that POSTs
│                              telemetry to the backend (backend-mode path)
├── docs/                      Architecture, API spec, DB schema, deployment, testing
└── setup_esp32.py             One-shot setup + flashing + verification script
```

## Quick start

### ⚡ One command (interactive menu) — recommended
```bash
python3 setup_esp32.py
```
Menu: (1) configure Wi-Fi → upload → verify direct connection, (2) set Wi-Fi
creds only, (3) upload, (4) run GUI (backend + frontend + browser),
(5) verify ESP32 `/data` by IP, (6) status, (7) **stop backend + frontend**,
(8) install arduino-cli.

CLI flags: `--gui` starts the servers now, `--stop` stops the backend and
frontend, `--status` shows current state.

### Manual quick start

**1. Backend**
```bash
cd backend
cp .env.example .env          # edit values (JWT secret, CORS origins)
pip install -r requirements.txt --break-system-packages
uvicorn app.main:app --host 0.0.0.0 --port 8000
# Swagger UI: http://localhost:8000/docs
```

**2. Frontend**
```bash
cd frontend
python3 -m http.server 5500
# open http://localhost:5500, login with demo@amppulse.ai / Demo@12345
```

**3. ESP32 — flash the standalone sketch**
Open `arduino/amppulse_esp32/amppulse_esp32.ino` in the Arduino IDE
(Board: ESP32 Dev Module), set your Wi-Fi in `WIFI_SSID` / `WIFI_PASSWORD`,
and press Upload — or use `setup_esp32.py` to do it automatically.
The Serial Monitor (115200 baud) prints the assigned IP.

**4. Connect the dashboard to your ESP32(s)**
In the dashboard, open the **📡 ESP32 Connect** view (sidebar). Register an
account from the login modal (or use `demo@amppulse.ai` / `Demo@12345`), then
**Add & Connect** an ESP32 IP (e.g. `192.168.1.8`). You can save **multiple**
ESP32s and switch between them anytime — they're remembered **per login**:
- Live cards update every 3s straight from the active device
- Relay toggles hit `/relay1/on|off` and `/relay2/on|off` instantly
- Remove a device to fall back to backend mode for it
- Without an active ESP32, everything gracefully falls back to backend mode

> ⚠️ The sketch sets `Access-Control-Allow-Origin: *` on every response
> (CORS) — this is what lets the browser read the ESP32's `/data` from the
> dashboard origin. Don't remove it.

### Optional: backend telemetry mode (analytics need data)
Live mode doesn't post history to the backend, so features like **Bill
Prediction / Energy Usage / Monthly Reports** fill in once telemetry arrives.
Register a device and use the modular `esp32-firmware` (PlatformIO) firmware to
POST telemetry:
```bash
curl -X POST http://localhost:8000/api/v1/devices/register \
  -H "Content-Type: application/json" \
  -H "X-Provision-Key: <DEVICE_PROVISION_KEY from .env>" \
  -d '{"device_id":"ESP32_001","name":"Home Energy Monitor"}'
```
See `docs/DEPLOYMENT.md` for the full backend-mode flow.

## Documentation index
- `docs/ARCHITECTURE.md` — MVP + production architecture, data flows, direct-mode section
- `docs/API_SPEC.md` — full REST API reference (includes per-user ESP32 IP endpoints)
- `docs/DATABASE.md` — schema and relationships
- `docs/SECURITY.md` — auth model, MVP simplifications vs production requirements
- `docs/DEPLOYMENT.md` — step-by-step laptop + ESP32 setup, troubleshooting
- `docs/TESTING.md` — test strategy for ESP32, backend, frontend, end-to-end
- `docs/PRODUCTION.md` — migration plan and production-readiness checklist

## What's demo vs what's real
- **Real**: live voltage (ZMPT101B) and temperature/humidity (DHT22) from the
  ESP32; relay ON/OFF control (instant in direct mode); bill projection,
  energy usage and monthly reports from backend telemetry.
- **Simulated pending hardware**: current/power/energy (PZEM-004T) — the sketch
  reports a fixed 1.50 A when any relay is on until a real PZEM is wired.
- **Illustrative**: the Organization / Industrial / Government dashboards the
  original demo showed — a single ESP32 + 2-channel relay doesn't yet support
  multi-building/multi-machine scenarios.