/*
 * config.h - Device-specific configuration.
 *
 * COPY config.example.h to config.h and fill in your real values.
 * config.h is gitignored - it holds your Wi-Fi password and device key,
 * which must NEVER be committed to version control or shared.
 *
 * This is the ONLY file you should need to edit per physical device.
 */
#pragma once

// ---------- Wi-Fi ----------
#define WIFI_SSID       "YOUR_WIFI_SSID"
#define WIFI_PASSWORD   "YOUR_WIFI_PASSWORD"

// ---------- Backend ----------
// Use your laptop's LOCAL network IP address (NOT "localhost" - the ESP32
// is a separate device on the network and localhost would refer to itself).
// Find your laptop's IP with `ipconfig` (Windows) or `ifconfig`/`ip addr` (Mac/Linux).
#define BACKEND_HOST    "192.168.1.100"
#define BACKEND_PORT    8000

// ---------- Device identity ----------
// Must be unique across all devices talking to the same backend.
#define DEVICE_ID       "ESP32_001"

// Issued ONCE by POST /api/v1/devices/register (see backend/README.md).
// This is the device's own secret credential - treat it like a password.
#define DEVICE_KEY      "PASTE_YOUR_DEVICE_KEY_HERE"

// ---------- Timing ----------
#define TELEMETRY_INTERVAL_MS   5000    // how often to POST sensor data
#define COMMAND_POLL_INTERVAL_MS 3000   // how often to check for pending commands
#define HTTP_TIMEOUT_MS         4000    // per-request timeout
#define WIFI_RECONNECT_DELAY_MS 5000    // delay between Wi-Fi reconnect attempts

// ---------- Hardware pins ----------
#define RELAY_CHANNEL_1_PIN   26
#define RELAY_CHANNEL_2_PIN   27
#define ZMPT_VOLTAGE_PIN      34   // analog input from ZMPT101B
#define DHT_PIN               4
#define DHT_TYPE              DHT22

// ---------- Feature flags ----------
// PZEM-004T is not currently wired/working (per project notes) - simulate
// energy readings instead so the rest of the system can be developed and
// demoed. Flip to false once real PZEM-004T UART wiring is working, and
// implement readPZEM() in sensors.cpp.
#define SIMULATE_ENERGY_SENSOR true
