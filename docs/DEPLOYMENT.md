# Deployment Guide (Laptop + ESP32, local network)

Two firmware paths are supported:

- **Direct mode (recommended)** — flash the standalone sketch
  (`arduino/amppulse_esp32/amppulse_esp32.ino`). It runs its own web server on
  port 80; the dashboard connects to it by IP for live data + relay control.
  No device registration. (Sections 3–4 + 7–8.)
- **Backend telemetry mode (alternative)** — flash the modular PlatformIO
  firmware (`esp32-firmware/`). It POSTs telemetry to the FastAPI backend,
  which powers analytics (bill prediction, energy usage, monthly reports).
  Requires device registration. (Sections 3–6 + 9.)

## 1. Required software
- Python 3.10+ (backend)
- Node.js not required for backend; only needed if you want a dev static
  server other than `python3 -m http.server` for the frontend
- For the standalone sketch: **Arduino IDE** or **arduino-cli** (the
  `setup_esp32.py` script can auto-install arduino-cli)
- For the alternative path: **PlatformIO** (VS Code extension, or CLI:
  `pip install platformio --break-system-packages`)
- curl or Postman/Insomnia (API testing)

## 2. Find your laptop's local IP address
- **Windows**: `ipconfig` → look for "IPv4 Address" under your active adapter (usually Wi-Fi).
- **macOS**: `ipconfig getifaddr en0` (or `en1` if using a different adapter)
- **Linux**: `ip addr show` or `hostname -I`

Example: `192.168.1.100`. Use this exact value — never `localhost` or
`127.0.0.1` — anywhere a *device* needs to reach the backend, since the ESP32
is a separate device on the network. (The browser dashboard can always use
`localhost`.)

## 3. Backend setup
```bash
cd backend
python3 -m venv .venv           # optional but recommended
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt --break-system-packages
cp .env.example .env
```
Edit `.env`:
- `JWT_SECRET` — generate with `python -c "import secrets; print(secrets.token_hex(32))"`
- `DEVICE_PROVISION_KEY` — random string (only needed for the backend
  telemetry/registration path)
- `CORS_ORIGINS` — add whatever origin your frontend is served from, e.g. `http://localhost:5500`
- Leave `DATABASE_URL` as the default SQLite path for local dev.

## 4. Start the backend
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
`--host 0.0.0.0` lets other devices reach it — `127.0.0.1` would only accept
connections from the laptop itself.

Verify it started:
```bash
curl http://localhost:8000/api/v1/health
# {"success":true,"status":"ok","server_time":"..."}
```

## 5. Firewall configuration
Inbound port 8000 may be blocked by default.
- **Windows**: Settings → Network & Internet → Firewall → Allow an app
  through firewall → add Python (or allow port 8000 for Private networks).
  Or via PowerShell (admin):
  ```powershell
  New-NetFirewallRule -DisplayName "AmpPulse Backend" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow
  ```
- **macOS**: System Settings → Network → Firewall → Options → allow incoming
  connections for Python.
- **Linux (ufw)**:
  ```bash
  sudo ufw allow 8000/tcp
  ```

> For **direct mode** the browser talks to the ESP32 (port 80), not to the
> laptop — but the backend still needs to be reachable by the *browser*,
> and `setup_esp32.py` / the dashboard probe the ESP32 from the laptop itself.

## 6. Verify laptop ↔ network connectivity
From another device on the same Wi-Fi (e.g. your phone's browser):
```
http://<laptop-local-ip>:8000/api/v1/health
```
If this doesn't load, revisit the firewall step, or confirm both devices are
on the same Wi-Fi/subnet (ESP32 needs 2.4GHz).

## 7. Direct mode (recommended) — standalone sketch + dashboard

### 7.1 Fastest: one-shot script
```bash
python3 setup_esp32.py            # menu: 1 = configure + upload + verify
```
The script: creates the backend venv, seeds `.env`, starts the backend,
writes your Wi-Fi SSID/password into the sketch, uploads via arduino-cli
(auto-installs it), then asks for the ESP32's IP and verifies `/data`.

### 7.2 Manual flash
Open `arduino/amppulse_esp32/amppulse_esp32.ino` in the Arduino IDE
(Board: **ESP32 Dev Module**, 115200 baud), set:
```cpp
const char* WIFI_SSID = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
```
Upload. The Serial Monitor prints the assigned IP, e.g. `IP Address: 192.168.1.8`.

### 7.3 Connect the dashboard
1. Start the frontend: `cd frontend && python3 -m http.server 5500`
2. Open `http://localhost:5500`, login (`demo@amppulse.ai` / `Demo@12345`)
3. Open **📡 ESP32 Connect** in the sidebar, enter the ESP32's IP, press **Connect**
4. Live cards update every 3s; relay toggles hit the device instantly

The IP is saved per login (`PUT /api/v1/user/esp32`) and auto-reused next time.
Without an IP, the dashboard falls back to backend mode.

> The sketch sends `Access-Control-Allow-Origin: *` on every response — this
> required header is what lets the browser read the ESP32's `/data` from the
> dashboard's origin. Keep it.

Verify the ESP32 web server directly (from the laptop):
```bash
curl http://<esp32-ip>:80/data
# {"voltage":230.5,"current":1.50,"power":345.8,"temperature":28.4,
#  "humidity":62.1,"relay1":false,"relay2":false}
```

## 8. Frontend configuration & startup
```bash
cd frontend
python3 -m http.server 5500
```
Open `http://localhost:5500` in a browser. If your backend runs on a
non-default host/port, edit the `window.AMPPULSE_API_BASE` line near the
top of `index.html` before serving.

Login with the seeded demo user (from backend `.env`):
- Email: `demo@amppulse.ai`
- Password: `Demo@12345`

## 9. Backend telemetry mode (alternative) — PlatformIO firmware
Use this path if you want telemetry stored in the backend for analytics
(Bill Prediction, Energy Usage, Monthly Reports).

### 9.1 Register your ESP32 device
```bash
curl -X POST http://localhost:8000/api/v1/devices/register \
  -H "Content-Type: application/json" \
  -H "X-Provision-Key: <your DEVICE_PROVISION_KEY>" \
  -d '{"device_id":"ESP32_001","name":"Home Energy Monitor","firmware_version":"1.0.0"}'
```
**Copy the returned `device_key` immediately — it is shown only once.**

### 9.2 Configure & upload
```bash
cd esp32-firmware
cp include/config.example.h include/config.h
```
Edit `include/config.h`:
- `WIFI_SSID`, `WIFI_PASSWORD` — your Wi-Fi credentials
- `BACKEND_HOST` — your laptop's local IP from step 2
- `BACKEND_PORT` — `8000`
- `DEVICE_ID` — `ESP32_001` (must match what you registered)
- `DEVICE_KEY` — the key from step 9.1

```bash
pio run -t upload
pio device monitor
```
Watch the serial monitor for:
```
[WiFi] Connected. IP address: ...
[Telemetry] Sent OK: V=... I=... P=... E=... T=... H=...
```

## 10. API testing (independent of the frontend)
```bash
# Login
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"demo@amppulse.ai","password":"Demo@12345"}'

# List devices (use the access_token from login)
curl http://localhost:8000/api/v1/devices \
  -H "Authorization: Bearer <token>"

# Save/read the direct-connect ESP32 IP for the current user
curl -X PUT http://localhost:8000/api/v1/user/esp32 \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"ip":"192.168.1.8","port":80}'
curl http://localhost:8000/api/v1/user/esp32 -H "Authorization: Bearer <token>"

# Simulate a telemetry post (backend telemetry mode / as if from the ESP32)
curl -X POST http://localhost:8000/api/v1/devices/ESP32_001/data \
  -H "Content-Type: application/json" \
  -H "X-Device-Key: <device_key>" \
  -d '{"device_id":"ESP32_001","timestamp":1757146200,"voltage":230.5,"current":2.1,"power":483.5,"energy_kwh":1.2,"frequency":50.0,"temperature":28,"humidity":60,"channel_status":{"1":"ON","2":"OFF"}}'
```
Or open `http://localhost:8000/docs` for interactive Swagger UI testing.

## 11. End-to-end test

**Direct mode:**
1. ESP32 serial shows Wi-Fi connected + its IP.
2. Dashboard → ESP32 Connect → enter IP → status shows `● Connected to <ip>`.
3. Live numbers update every 3s and match the sketch's serial prints.
4. Toggle a relay card → the toast confirms it, and relay status flips on the
   next poll; in direct mode the ESP32 applies it immediately.

**Backend telemetry mode:**
1. ESP32 serial log shows successful `[Telemetry] Sent OK` lines.
2. Frontend Home dashboard's Live Power/Voltage/Energy update every ~5s and
   match the ESP32's values.
3. "Turn OFF" on an appliance → toast → serial shows
   `[Command] Received: ... [Relay] Channel X set to OFF` → status flips OFF.

## 12. Troubleshooting
| Symptom | Likely cause | Fix |
|---|---|---|
| ESP32 serial shows repeated Wi-Fi retries | Wrong SSID/password, or 5GHz-only network (ESP32 needs 2.4GHz) | Double-check credentials; ensure router broadcasts 2.4GHz |
| Dashboard can't connect / `Cannot reach ESP32` | Wrong IP, ESP32 not on the same Wi-Fi, or IP changed after reboot | Re-read the IP from the Serial Monitor; consider a DHCP reservation in the router |
| Dashboard connects but `fetch` fails | CORS headers missing on the ESP32 (only affects the dashboard, not `curl`) | Keep the `sendResponse()` helper in `amppulse_esp32.ino` that sends `Access-Control-Allow-Origin` |
| Frontend shows "● OFFLINE" but device is fine | `AMPPULSE_API_BASE` doesn't match backend host/port, or CORS origin mismatch | Check `CORS_ORIGINS` in backend `.env` includes your frontend's exact origin |
| `[Telemetry] Send failed (BACKEND_UNREACHABLE)` | Backend not running, wrong IP, or firewall blocking (backend telemetry mode) | Re-check steps 4–6; confirm `curl` from another network device works |
| `401 UNAUTHORIZED` on telemetry POST | Wrong/stale `DEVICE_KEY` in firmware config | Re-register the device (key shown only once) |
| Frontend login fails | Wrong seeded credentials, or `.env` changed after first run (seed only happens if user doesn't exist) | Use the exact `SEED_USER_EMAIL`/`SEED_USER_PASSWORD` from `.env`, or delete `amppulse.db` to reseed (dev only — destroys data) |
| Commands never apply | ESP32 not polling, or wrong channel number (backend telemetry mode) | Check serial log for `[Command]` lines; confirm channel is 1 or 2 |
| Voltage reads 0 / flat | ZMPT101B ADC clipping (default 0 dB attenuation caps ~1.1 V) | Ensure `setup()` calls `analogSetPinAttenuation(ZMPT_PIN, ADC_11db)`; recalibrate `ZMPT_CALIBRATION` |

This setup is reproducible on another laptop by repeating the steps with that
laptop's own local IP address.