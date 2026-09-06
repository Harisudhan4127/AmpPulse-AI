# Deployment Guide (Laptop + ESP32, local network)

## 1. Required software
- Python 3.10+ (backend)
- Node.js not required for backend; only needed if you want to run a dev
  static server other than `python3 -m http.server` for the frontend
- PlatformIO (ESP32 firmware) — VS Code extension, or CLI: `pip install platformio --break-system-packages`
- A code editor (VS Code recommended for PlatformIO integration)
- curl or Postman/Insomnia (API testing)

## 2. Find your laptop's local IP address
- **Windows**: `ipconfig` → look for "IPv4 Address" under your active adapter (usually Wi-Fi).
- **macOS**: `ipconfig getifaddr en0` (or `en1` if using a different adapter)
- **Linux**: `ip addr show` or `hostname -I`

Example: `192.168.1.100`. Use this exact value — never `localhost` or
`127.0.0.1` — anywhere the ESP32 needs to reach the backend, since the ESP32
is a separate device on the network.

## 3. Backend setup
```bash
cd backend
python3 -m venv venv            # optional but recommended
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt --break-system-packages
cp .env.example .env
```
Edit `.env`:
- `JWT_SECRET` — generate with `python -c "import secrets; print(secrets.token_hex(32))"`
- `DEVICE_PROVISION_KEY` — pick a random string (used to register new ESP32 devices)
- `CORS_ORIGINS` — add whatever origin your frontend is served from, e.g. `http://localhost:5500`
- Leave `DATABASE_URL` as the default SQLite path for local dev.

## 4. Start the backend
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
`--host 0.0.0.0` is required so the ESP32 (a different device) can reach it —
`127.0.0.1` would only accept connections from the laptop itself.

Verify it started:
```bash
curl http://localhost:8000/api/v1/health
# {"success":true,"status":"ok","server_time":"..."}
```

## 5. Firewall configuration
The backend listens on port 8000. Your laptop's firewall may block inbound
connections from other devices (like the ESP32) by default.

- **Windows**: Settings → Network & Internet → Firewall → Allow an app
  through firewall → add Python (or allow port 8000 for Private networks).
  Or via PowerShell (admin):
  ```powershell
  New-NetFirewallRule -DisplayName "AmpPulse Backend" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow
  ```
- **macOS**: System Settings → Network → Firewall → Options → allow incoming
  connections for Python, or temporarily disable firewall for local testing.
- **Linux (ufw)**:
  ```bash
  sudo ufw allow 8000/tcp
  ```

## 6. Verify ESP32 → laptop connectivity (before flashing firmware)
From another device on the same Wi-Fi (e.g. your phone's browser, or a
second terminal if you have one), visit:
```
http://<laptop-local-ip>:8000/api/v1/health
```
If this doesn't load, the firewall step above needs revisiting, or the
laptop and ESP32 are not on the same Wi-Fi network/subnet.

## 7. Register your ESP32 device
```bash
curl -X POST http://localhost:8000/api/v1/devices/register \
  -H "Content-Type: application/json" \
  -H "X-Provision-Key: <your DEVICE_PROVISION_KEY>" \
  -d '{"device_id":"ESP32_001","name":"Home Energy Monitor","firmware_version":"1.0.0"}'
```
**Copy the returned `device_key` immediately — it is shown only once.**

## 8. ESP32 firmware configuration & upload
```bash
cd ../esp32-firmware
cp include/config.example.h include/config.h
```
Edit `include/config.h`:
- `WIFI_SSID`, `WIFI_PASSWORD` — your Wi-Fi credentials
- `BACKEND_HOST` — your laptop's local IP from step 2
- `BACKEND_PORT` — `8000` (unless changed)
- `DEVICE_ID` — `ESP32_001` (must match what you registered)
- `DEVICE_KEY` — the key from step 7

Build and upload:
```bash
pio run -t upload
pio device monitor
```
Watch the serial monitor for:
```
[WiFi] Connected. IP address: ...
[Telemetry] Sent OK: V=... I=... P=... E=... T=... H=...
```

## 9. Frontend configuration & startup
```bash
cd ../frontend
python3 -m http.server 5500
```
Open `http://localhost:5500` in a browser. If your backend runs on a
non-default host/port, edit the `window.AMPPULSE_API_BASE` line near the
top of `index.html` before serving.

Login with the seeded demo user (from backend `.env`):
- Email: `demo@amppulse.ai`
- Password: `Demo@12345`

## 10. API testing (independent of the frontend)
```bash
# Login
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"demo@amppulse.ai","password":"Demo@12345"}'

# List devices (use the access_token from login)
curl http://localhost:8000/api/v1/devices \
  -H "Authorization: Bearer <token>"

# Simulate a telemetry post (as if from the ESP32)
curl -X POST http://localhost:8000/api/v1/devices/ESP32_001/data \
  -H "Content-Type: application/json" \
  -H "X-Device-Key: <device_key>" \
  -d '{"device_id":"ESP32_001","timestamp":1757146200,"voltage":230.5,"current":2.1,"power":483.5,"energy_kwh":1.2,"frequency":50.0,"temperature":28,"humidity":60,"channel_status":{"1":"ON","2":"OFF"}}'
```
Or open `http://localhost:8000/docs` for interactive Swagger UI testing.

## 11. End-to-end test
1. Confirm ESP32 serial log shows successful `[Telemetry] Sent OK` lines.
2. Open the frontend, log in, and confirm the Home dashboard's Live Power/
   Voltage/Energy numbers update every ~5 seconds and match what the ESP32
   is sending.
3. Click "Turn OFF" on an appliance card. Confirm:
   - Frontend shows a toast "Command sent..."
   - ESP32 serial log shows `[Command] Received: ... [Relay] Channel X set to OFF`
   - Frontend appliance status flips to OFF within ~5–8 seconds (next poll cycle)

## 12. Troubleshooting
| Symptom | Likely cause | Fix |
|---|---|---|
| ESP32 serial shows repeated `[WiFi] Retrying connection...` | Wrong SSID/password, or 5GHz-only network (ESP32 needs 2.4GHz) | Double-check credentials; ensure router broadcasts 2.4GHz |
| `[Telemetry] Send failed (BACKEND_UNREACHABLE)` | Backend not running, wrong IP, or firewall blocking | Re-check steps 4–6; confirm `curl` from another device on the network works |
| Frontend shows "● OFFLINE" but ESP32 is fine | Frontend `AMPPULSE_API_BASE` doesn't match backend host/port, or CORS origin mismatch | Check `CORS_ORIGINS` in backend `.env` includes your frontend's exact origin |
| `401 UNAUTHORIZED` on telemetry POST | Wrong/stale `DEVICE_KEY` in firmware config | Re-register the device or double check you copied the key exactly (only shown once) |
| Frontend login fails | Wrong seeded credentials, or backend `.env` changed after first run (seed only happens if user doesn't exist) | Use the exact `SEED_USER_EMAIL`/`SEED_USER_PASSWORD` from your `.env`, or delete `amppulse.db` to reseed (dev only — destroys data) |
| Commands never apply | ESP32 not polling (check serial log for `[Command]` lines), or wrong channel number sent from frontend | Confirm `COMMAND_POLL_INTERVAL_MS` firmware is running; check `channel` is 1 or 2 |

This setup is reproducible on another laptop by repeating steps 1–9 with
that laptop's own local IP address.
