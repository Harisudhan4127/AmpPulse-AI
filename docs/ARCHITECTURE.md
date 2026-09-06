# Architecture

## 1. Project understanding (recap)
AmpPulse AI monitors electricity at appliance level using an ESP32 with a
2-channel relay (device control), ZMPT101B (voltage), PZEM-004T (energy —
not yet wired, simulated), and DHT22 (temperature/humidity). The product
goal is appliance-level visibility, cost estimation, bill prediction, and
AI-driven savings insights, exposed via a dashboard, initially for Home
users with Organization/Industrial/Government as future extensions.

## 2. Existing architecture before this work
- Frontend only: static `index.html` + `app.js` + `styles.css`.
- All dashboard numbers were hardcoded in a `DEMO` JS object.
- No backend, no database, no ESP32 firmware, no real device communication.

## 3. Recommended architecture (this implementation)

```
                      Wi-Fi Network
                            │
                  ┌─────────┴─────────┐
                  │                   │
            ┌─────▼─────┐       ┌─────▼──────────┐
            │   ESP32   │       │    Laptop      │
            │ Sensors / │◄─────►│  Local Host    │
            │  Relay    │ JSON  │ FastAPI + SQLite│
            └───────────┘ HTTP  └───────┬─────────┘
                                        │ JSON REST
                                ┌───────▼────────┐
                                │    Frontend    │
                                │    Browser     │
                                └────────────────┘
```

- **ESP32 ↔ Backend**: HTTP + JSON, device-key authenticated, POST telemetry
  + GET/POST commands (polling model).
- **Backend ↔ Frontend**: HTTP + JSON, user-JWT authenticated, REST resources
  for devices/telemetry/commands.
- **Backend ↔ Database**: SQLAlchemy ORM over SQLite (MVP) — never exposed
  directly to ESP32 or frontend.

## 4. MVP architecture (implemented)

```
┌─────────────────────────────────────┐
│              Laptop                 │
│  ┌────────────┐   ┌─────────────┐  │
│  │ Frontend   │──►│ Backend API │  │
│  │ (static)   │   │  (FastAPI)  │  │
│  └────────────┘   └──────┬──────┘  │
│                     ┌─────▼─────┐  │
│                     │ SQLite DB │  │
│                     └───────────┘  │
└──────────────────┬───────────────── ┘
                    │ Wi-Fi
             ┌──────▼──────┐
             │    ESP32    │
             └─────────────┘
```

Single process (`uvicorn`), single SQLite file, static frontend served by
any simple HTTP server (or opened as a file — CORS is configured either
way). No message brokers, no microservices, no container orchestration —
intentionally, per the "don't over-engineer the MVP" constraint.

## 4.5 Direct ESP32 connection mode

The dashboard can talk to each ESP32's **own web server** (port 80) directly,
bypassing the backend for live telemetry and relay control. Users can save
**multiple** ESP32s per account (`user_esp32s` table) and switch between them
freely; the active device drives the live cards, chart, and relay controls,
while the backend keeps handling login, analytics, and reports.

The IPs are saved per login via `POST /api/v1/user/esp32` (list = 
`GET /api/v1/user/esp32s`, update/delete by id) and each is probed for
reachability + latency.

```
            fetch http://<esp32-ip>:80/data  (live voltage/current/power/temp/humidity/relays)
Frontend ───────────────────────────────────▶ ESP32 WebServer
            fetch http://<esp32-ip>:80/relay1|2/on|off  (instant relay control)
```

- **CORS**: the browser blocks reading cross-origin responses unless the
  ESP32 sends `Access-Control-Allow-Origin: *`. The standalone sketch
  (`amppulse_esp32.ino`) sets this header on every response and handles the
  `OPTIONS` preflight via `sendResponse()` / `handleNotFound()`.
- **Fallback**: with no IP (or after Disconnect/3s timeout), live data and
  relay control return to the normal backend path. Analytics (Bill
  Prediction, Monthly Reports, Energy Usage) always use the backend, which
  still needs telemetry POSTed to it (device-key auth).

## 5. Production architecture (future)

**Stage 1 — Development (current)**
```
ESP32 → Wi-Fi → Laptop (Backend + SQLite) → Frontend
```

**Stage 2 — Small deployment**
```
ESP32 → Internet → Cloud/VPS Backend (same FastAPI app) → Postgres → Frontend (hosted)
```
Changes: `DATABASE_URL` → Postgres connection string; `BACKEND_HOST` in ESP32
config → public domain/IP; add HTTPS (reverse proxy e.g. Nginx/Caddy with
Let's Encrypt); real NTP-based timestamps on ESP32 instead of uptime-seconds.
**Unchanged**: API routes, JSON contracts, ESP32 application logic, relay
control flow, auth model shape (device key + user JWT).

**Stage 3 — Production**
```
ESP32 Devices → Internet → Load Balancer/API Gateway → Backend Services (horizontally scaled)
                                                       → Postgres (managed) + Cache + Queue
                                                       → Frontend (CDN-hosted)
```
Changes: multiple backend instances behind a load balancer (FastAPI app is
already stateless — sessions are JWT, not server-side — so this requires no
code change, just more instances); message queue only if telemetry volume
requires async ingestion; device provisioning becomes a proper admin flow;
OTA firmware updates; monitoring/alerting stack (e.g. Prometheus/Grafana).
**Unchanged**: ESP32 firmware's API contract — only the server URL config
changes, exactly as required.

## 6. Data flow — telemetry (ESP32 → dashboard)
```
ESP32 reads sensors
  → POST /api/v1/devices/{id}/data  (X-Device-Key auth)
  → Backend validates payload (Pydantic), stores TelemetryReading row,
    updates Device.last_seen_at
  → Frontend polls GET /api/v1/devices/{id}/data and /summary every 5s
    (JWT auth) and re-renders the dashboard
```

## 7. Data flow — command (dashboard → ESP32)
```
User clicks "Turn OFF Fan"
  → Frontend POST /api/v1/devices/{id}/commands (JWT auth)
  → Backend creates a Command row (status=pending)
  → ESP32 polls GET /api/v1/devices/{id}/commands every 3s (X-Device-Key auth)
  → Backend marks matching commands status=delivered, returns them
  → ESP32 toggles the relay, POSTs /commands/ack (acked/failed)
```

## 8. Why polling, not MQTT/WebSocket (MVP)
Chosen for the MVP because: (a) a few seconds of command latency is fine for
relay toggling, (b) it reuses the same HTTP/JSON stack and libraries as
telemetry — no second protocol, broker, or port to manage on a laptop, and
(c) it is trivially firewall/NAT-friendly on a home Wi-Fi network. Upgrade
trigger for production: many devices needing sub-second control, or telemetry
volume that makes constant polling wasteful — at that point introduce
MQTT (e.g. Mosquitto) or WebSockets without changing the ESP32's sensor-
reading or command-application logic, only the transport layer in
`api_client.cpp`.
