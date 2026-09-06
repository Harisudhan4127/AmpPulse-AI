# AmpPulse AI — ESP32 Firmware

## Structure
```
esp32-firmware/
├── platformio.ini
├── include/
│   ├── config.example.h   # copy to config.h and fill in your values
│   ├── wifi_manager.h
│   ├── api_client.h
│   ├── sensors.h
│   └── relay_control.h
└── src/
    ├── main.cpp
    ├── wifi_manager.cpp
    ├── api_client.cpp
    ├── sensors.cpp
    └── relay_control.cpp
```

## Hardware wiring (defaults in config.example.h — adjust to your build)
| Component        | ESP32 Pin |
|-------------------|-----------|
| Relay channel 1    | GPIO 26   |
| Relay channel 2    | GPIO 27   |
| ZMPT101B (analog)  | GPIO 34   |
| DHT22 data         | GPIO 4    |

⚠️ **Never connect mains-voltage wiring directly to the ESP32.** Use properly
isolated/rated voltage and current sensing modules (ZMPT101B, PZEM-004T) and
qualified electrical installation practices for anything touching AC mains.

## One-time setup

1. Install [PlatformIO](https://platformio.org/) (VS Code extension, or `pip install platformio --break-system-packages`).
2. Copy the config template:
   ```bash
   cp include/config.example.h include/config.h
   ```
3. Edit `include/config.h`:
   - `WIFI_SSID` / `WIFI_PASSWORD` — your Wi-Fi network.
   - `BACKEND_HOST` — your laptop's **local network IP** (e.g. `192.168.1.100`), never `localhost` or `127.0.0.1`.
   - `DEVICE_ID` — unique name, e.g. `ESP32_001`.
   - `DEVICE_KEY` — obtained by registering the device against the backend first (see backend README §Device provisioning). Paste the returned `device_key` here.
4. Build and upload:
   ```bash
   pio run -t upload
   pio device monitor
   ```

## What the firmware does
- Connects to Wi-Fi; if the connection drops, retries every `WIFI_RECONNECT_DELAY_MS`.
- Every `TELEMETRY_INTERVAL_MS` (default 5s), reads sensors and `POST`s a JSON
  reading to `/api/v1/devices/{device_id}/data`.
- Every `COMMAND_POLL_INTERVAL_MS` (default 3s), `GET`s
  `/api/v1/devices/{device_id}/commands` and applies any pending relay commands,
  then acknowledges each one.
- On telemetry failure (backend unreachable, timeout, auth error), backs off
  exponentially (capped at 6x the base interval) instead of retrying immediately.
- PZEM-004T is simulated (`SIMULATE_ENERGY_SENSOR = true` in config.h) since the
  real sensor isn't wired yet per the project notes — swap in real UART reads in
  `sensors.cpp::readEnergyMetrics()` when hardware is ready; no other file changes.

## Debug logging
All logging goes to Serial at 115200 baud, tagged by subsystem: `[WiFi]`,
`[Telemetry]`, `[Command]`, `[Relay]`, `[Sensors]`, `[Boot]`.
