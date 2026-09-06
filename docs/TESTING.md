# Testing Strategy

## ESP32 firmware
| Test | How |
|---|---|
| Wi-Fi connection | Power on with correct credentials in `config.h`; confirm serial log shows `[WiFi] Connected. IP address: ...` |
| Wi-Fi reconnection | Turn off the router's Wi-Fi (or move the ESP32 out of range) mid-run; confirm `[WiFi] Connection lost.` then repeated `[WiFi] Retrying connection...` every `WIFI_RECONNECT_DELAY_MS`, and successful reconnect log when Wi-Fi returns |
| API connection | With backend running, confirm periodic `[Telemetry] Sent OK: ...` lines |
| JSON serialization | Inspect `postTelemetry()` output by temporarily logging `body` before sending, or capture the raw POST with a tool like `mitmproxy`/Wireshark on the same network |
| JSON parsing (commands) | Queue a command via the frontend/curl, confirm `[Command] Received: id=... channel=... value=...` appears in serial log |
| Sensor data transmission | Confirm each `[Telemetry] Sent OK` line shows plausible V/I/P/E/T/H values, and that they change over time (not frozen) |
| Command reception | Send a command, confirm `[Relay] Channel X set to ON/OFF` appears and the ack POST succeeds (no error log) |
| Timeout | Point `BACKEND_HOST` at an unreachable IP; confirm failure is logged within `HTTP_TIMEOUT_MS` (~4s), not hanging indefinitely |
| Retry/backoff | With backend stopped, confirm `[Telemetry] Send failed (...) fail streak=N` grows and retry interval increases (visible in the logged "next retry in Xms") |
| Backend unavailable | Stop `uvicorn`; confirm ESP32 keeps logging failures without crashing/rebooting |
| Invalid response | Temporarily point `BACKEND_HOST`/port at a server returning non-JSON (e.g. a plain webserver on port 80); confirm `deserializeJson` error is logged and firmware continues running |

## Backend
Recommended: `pytest` + FastAPI's `TestClient` (add as a follow-up; not
included in the MVP to keep scope focused, but the app is structured for it
since routers/dependencies are cleanly separated).

Manual/curl-based coverage already exercised during development:
```bash
# Health
curl http://localhost:8000/api/v1/health

# Auth: success and failure
curl -X POST .../auth/login -d '{"email":"demo@amppulse.ai","password":"Demo@12345"}'
curl -X POST .../auth/login -d '{"email":"demo@amppulse.ai","password":"wrong"}'   # expect 401 AUTH_FAILED

# Device registration: success, duplicate, bad key
curl -X POST .../devices/register -H "X-Provision-Key: <key>" -d '{"device_id":"ESP32_001"}'
curl -X POST .../devices/register -H "X-Provision-Key: <key>" -d '{"device_id":"ESP32_001"}'  # expect 409
curl -X POST .../devices/register -H "X-Provision-Key: wrong" -d '{"device_id":"ESP32_002"}'  # expect 401

# Telemetry ingestion: valid, out-of-range, wrong key, device_id mismatch
curl -X POST .../devices/ESP32_001/data -H "X-Device-Key: <key>" -d '{...valid...}'   # 201
curl -X POST .../devices/ESP32_001/data -H "X-Device-Key: <key>" -d '{"voltage":999,...}'  # 422
curl -X POST .../devices/ESP32_001/data -H "X-Device-Key: wrong" -d '{...}'  # 401
curl -X POST .../devices/ESP32_001/data -H "X-Device-Key: <key>" -d '{"device_id":"ESP32_999",...}'  # 400

# Commands: create, poll (marks delivered), ack
curl -X POST .../devices/ESP32_001/commands -H "Authorization: Bearer <jwt>" -d '{"action":"relay_set","channel":1,"value":"OFF"}'
curl .../devices/ESP32_001/commands -H "X-Device-Key: <key>"   # should return the pending command, now delivered
curl -X POST .../devices/ESP32_001/commands/ack -H "X-Device-Key: <key>" -d '{"command_id":1,"status":"acked"}'
```
All of the above were run against a live instance during development and
returned the expected status codes and error envelopes (see chat history /
PR notes for captured output).

Error response format: verified `422` and `401` responses both match
`{"success": false, "error": {"code", "message"}}`.

## Frontend
| Test | How |
|---|---|
| API integration | Open browser dev tools → Network tab; confirm `/api/v1/...` calls fire every ~5s while the Home dashboard is open |
| Device display | Confirm KPI cards (Live Power, Voltage, etc.) show real numbers matching the ESP32's serial log output, not the old hardcoded demo values |
| Status updates | Stop the ESP32; confirm the dashboard's live-dot badge switches to "● OFFLINE" and appliance cards indicate stale/offline state within ~90s (`DEVICE_OFFLINE_AFTER_SECONDS`) |
| Commands | Click "Turn OFF"/"Turn ON" on an appliance card; confirm a toast appears, then the status updates within one poll cycle after the ESP32 applies it |
| Error states | Stop the backend entirely; confirm the top-right indicator shows "● OFFLINE" and a toast/error message appears rather than a silent failure or crash |
| Login | Test wrong password (expect inline error message under the password field) and correct login (expect dashboard to load) |

## End-to-end
1. Start backend → confirm `/api/v1/health` returns 200.
2. Flash and power on ESP32 → confirm serial log shows Wi-Fi connect + first
   successful telemetry POST.
3. Open frontend, log in → confirm live values appear and update.
4. Send a relay command from the frontend → confirm it reaches the ESP32
   (serial log) and the physical relay/LED reacts, and the ack round-trips
   back to the frontend's displayed state.
5. Disconnect ESP32 power → confirm frontend transitions to offline state
   within the expected window; reconnect power → confirm it recovers
   automatically without restarting the backend or frontend.
