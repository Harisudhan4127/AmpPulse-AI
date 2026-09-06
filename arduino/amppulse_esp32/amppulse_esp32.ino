/*
 * AmpPulse AI - ESP32 Firmware (MVP)
 * Single-file Arduino sketch - upload to an ESP32 with the Arduino IDE
 * (Board: "ESP32 Dev Module").
 *
 * Required libraries (install via Library Manager):
 *   - ArduinoJson  (bblanchon/ArduinoJson)
 *   - DHT sensor library (adafruit/DHT sensor library)
 *   - Adafruit Unified Sensor (adafruit/Adafruit Unified Sensor)
 *
 * Responsibilities:
 *   1. Connect to Wi-Fi, auto-reconnect on drop.
 *   2. Periodically read sensors and POST telemetry to the backend.
 *   3. Periodically poll the backend for pending relay commands and apply them.
 *   4. Handle failures gracefully: timeouts, backend-unreachable, invalid
 *      responses - log and retry on the next scheduled cycle rather than
 *      blocking or crashing.
 *
 * Non-blocking design: the main loop() never calls delay() for anything
 * other than the top-level idle yield. All periodic work is scheduled with
 * millis() comparisons so Wi-Fi reconnection, telemetry, and command
 * polling can all make progress independently.
 */

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include <time.h>

// ============================================================================
// CONFIG - edit one per physical device.
// Wi-Fi password and device key are secrets - never share or commit them.
// ============================================================================

// ---------- Wi-Fi ----------
#define WIFI_SSID  "HOME"
#define WIFI_PASSWORD  "Home@4127"

// ---------- Backend ----------
// Use your laptop's LOCAL network IP address (NOT "localhost" - the ESP32
// is a separate device on the network and localhost would refer to itself).
#define BACKEND_HOST  "192.168.1.4"
#define BACKEND_PORT  8000

// ---------- Device identity ----------
// Must be unique across all devices talking to the same backend.
#define DEVICE_ID  "ESP32_4A1511"

// Issued ONCE by POST /api/v1/devices/register (see backend/README.md).
#define DEVICE_KEY  "dvk_DufqqVciKzHIohZJgFLzEF_kHvbYD_M6fsrrTzy67jA"

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
// energy readings instead. Flip to false once real PZEM-004T UART wiring
// works, and implement readEnergyMetrics() below.
#define SIMULATE_ENERGY_SENSOR true

// ============================================================================
// Data types
// ============================================================================

struct SensorReading {
    float voltage;
    float current;
    float power;
    float energy_kwh;
    float frequency;
    float temperature;
    float humidity;
    String channel1_status;  // "ON" / "OFF"
    String channel2_status;
};

struct PendingCommand {
    int id;
    int channel;
    String value;  // "ON" / "OFF"
};

// ============================================================================
// Time helpers - NTP epoch seconds once synced, else boot uptime as fallback.
// ============================================================================

uint32_t epochSeconds() {
    time_t now;
    time(&now);
    if (now > 1600000000) {           // valid epoch (>= Sep 2020)
        return (uint32_t)now;
    }
    return (uint32_t)(millis() / 1000);  // NTP not synced yet: uptime fallback
}

// ============================================================================
// WiFiManager - Wi-Fi connection with automatic reconnection.
// Non-blocking: begin() kicks off a connection attempt and returns
// immediately. Call loop() every main-loop iteration; it detects
// disconnects and retries on a timer instead of blocking with delay().
// ============================================================================

class WiFiManager {
public:
    void begin(const char* ssid, const char* password);
    void loop();                 // call every main loop iteration
    bool isConnected() const;

private:
    const char* _ssid = nullptr;
    const char* _password = nullptr;
    unsigned long _lastAttemptMs = 0;
    bool _wasConnected = false;

    void attemptConnect();
};

void WiFiManager::begin(const char* ssid, const char* password) {
    _ssid = ssid;
    _password = password;
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(false);  // we manage reconnection ourselves for clearer logging/control
    attemptConnect();
}

void WiFiManager::attemptConnect() {
    Serial.printf("[WiFi] Connecting to '%s'...\n", _ssid);
    WiFi.disconnect(true);
    delay(100);
    WiFi.begin(_ssid, _password);
    _lastAttemptMs = millis();
}

void WiFiManager::loop() {
    bool connected = (WiFi.status() == WL_CONNECTED);

    if (connected && !_wasConnected) {
        Serial.print("[WiFi] Connected. IP address: ");
        Serial.println(WiFi.localIP());
        configTime(0, 0, "pool.ntp.org", "time.nist.gov");  // sync clock for real timestamps
    }
    if (!connected && _wasConnected) {
        Serial.println("[WiFi] Connection lost.");
    }
    _wasConnected = connected;

    if (!connected && (millis() - _lastAttemptMs >= WIFI_RECONNECT_DELAY_MS)) {
        Serial.println("[WiFi] Retrying connection...");
        attemptConnect();
    }
}

bool WiFiManager::isConnected() const {
    return WiFi.status() == WL_CONNECTED;
}

// ============================================================================
// ApiClient - HTTP/JSON client for talking to the AmpPulse AI backend.
// Wraps every backend call the ESP32 needs: telemetry POST, command GET/ack.
// Every method builds the full URL from BACKEND_HOST/BACKEND_PORT (never
// "localhost"), sets a request timeout, sends the X-Device-Key header for
// authentication, and returns a simple success/failure result.
// ============================================================================

class ApiClient {
public:
    void begin(const char* host, int port, const char* deviceId, const char* deviceKey);

    // Returns true if the backend is reachable at all (used for pre-flight checks/logging).
    bool checkHealth();

    // POST /api/v1/devices/{device_id}/data
    // Returns true on 2xx. On failure, fills outErrorCode with a short reason.
    bool postTelemetry(const SensorReading& reading, String& outErrorCode);

    // GET /api/v1/devices/{device_id}/commands
    // Returns the number of pending commands found (0 on none or on error).
    int pollCommands(PendingCommand outCommands[], int maxCommands);

    // POST /api/v1/devices/{device_id}/commands/ack
    bool ackCommand(int commandId, bool success);

private:
    String _host;
    int _port;
    String _deviceId;
    String _deviceKey;

    String baseUrl() const;
};

void ApiClient::begin(const char* host, int port, const char* deviceId, const char* deviceKey) {
    _host = host;
    _port = port;
    _deviceId = deviceId;
    _deviceKey = deviceKey;
}

String ApiClient::baseUrl() const {
    return "http://" + _host + ":" + String(_port);
}

bool ApiClient::checkHealth() {
    if (WiFi.status() != WL_CONNECTED) return false;

    HTTPClient http;
    http.setTimeout(HTTP_TIMEOUT_MS);
    http.begin(baseUrl() + "/api/v1/health");
    int code = http.GET();
    http.end();
    return code == 200;
}

bool ApiClient::postTelemetry(const SensorReading& r, String& outErrorCode) {
    if (WiFi.status() != WL_CONNECTED) {
        outErrorCode = "WIFI_DISCONNECTED";
        return false;
    }

    JsonDocument doc;
    doc["device_id"] = _deviceId;
    doc["timestamp"] = epochSeconds();  // NTP epoch, falls back to uptime before sync
    doc["device_ip"] = WiFi.localIP().toString();
    doc["voltage"] = r.voltage;
    doc["current"] = r.current;
    doc["power"] = r.power;
    doc["energy_kwh"] = r.energy_kwh;
    doc["frequency"] = r.frequency;
    doc["temperature"] = r.temperature;
    doc["humidity"] = r.humidity;
    JsonObject channels = doc["channel_status"].to<JsonObject>();
    channels["1"] = r.channel1_status;
    channels["2"] = r.channel2_status;
    doc["status"] = "online";

    String body;
    serializeJson(doc, body);

    HTTPClient http;
    http.setTimeout(HTTP_TIMEOUT_MS);
    http.begin(baseUrl() + "/api/v1/devices/" + _deviceId + "/data");
    http.addHeader("Content-Type", "application/json");
    http.addHeader("X-Device-Key", _deviceKey);

    int code = http.POST(body);

    if (code <= 0) {
        // Negative return values from HTTPClient mean a transport-level
        // failure: DNS, connect refused, or TIMEOUT (the case we most
        // expect on a flaky local Wi-Fi network).
        outErrorCode = (code == HTTPC_ERROR_READ_TIMEOUT || code == HTTPC_ERROR_CONNECTION_REFUSED)
                            ? "BACKEND_UNREACHABLE"
                            : "TRANSPORT_ERROR";
        Serial.printf("[API] Telemetry POST failed, HTTPClient code=%d\n", code);
        http.end();
        return false;
    }

    if (code == 201 || code == 200) {
        http.end();
        return true;
    }

    // Backend responded but with an error status (validation, auth, etc).
    String respBody = http.getString();
    Serial.printf("[API] Telemetry rejected, HTTP %d: %s\n", code, respBody.c_str());
    outErrorCode = (code == 401) ? "AUTH_FAILED" : (code == 422 ? "VALIDATION_ERROR" : "SERVER_ERROR");
    http.end();
    return false;
}

int ApiClient::pollCommands(PendingCommand outCommands[], int maxCommands) {
    if (WiFi.status() != WL_CONNECTED) return 0;

    HTTPClient http;
    http.setTimeout(HTTP_TIMEOUT_MS);
    http.begin(baseUrl() + "/api/v1/devices/" + _deviceId + "/commands");
    http.addHeader("X-Device-Key", _deviceKey);

    int code = http.GET();
    if (code != 200) {
        if (code > 0) {
            Serial.printf("[API] Command poll failed, HTTP %d\n", code);
        }
        http.end();
        return 0;
    }

    String body = http.getString();
    http.end();

    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, body);
    if (err) {
        Serial.printf("[API] Failed to parse commands JSON: %s\n", err.c_str());
        return 0;
    }

    JsonArray commands = doc["commands"].as<JsonArray>();
    int count = 0;
    for (JsonObject cmd : commands) {
        if (count >= maxCommands) break;
        outCommands[count].id = cmd["id"] | -1;
        outCommands[count].channel = cmd["channel"] | 0;
        outCommands[count].value = String((const char*)(cmd["value"] | "OFF"));
        count++;
    }
    return count;
}

bool ApiClient::ackCommand(int commandId, bool success) {
    if (WiFi.status() != WL_CONNECTED) return false;

    JsonDocument doc;
    doc["command_id"] = commandId;
    doc["status"] = success ? "acked" : "failed";
    String body;
    serializeJson(doc, body);

    HTTPClient http;
    http.setTimeout(HTTP_TIMEOUT_MS);
    http.begin(baseUrl() + "/api/v1/devices/" + _deviceId + "/commands/ack");
    http.addHeader("Content-Type", "application/json");
    http.addHeader("X-Device-Key", _deviceKey);

    int code = http.POST(body);
    http.end();
    return code == 200;
}

// ============================================================================
// Sensors - Reads all physical sensors and produces one SensorReading.
// * ZMPT101B via analog pin -> voltage
// * DHT22 -> temperature / humidity
// * PZEM-004T currently simulated -> current / power / energy / frequency
// ============================================================================

class Sensors {
public:
    void begin();
    SensorReading readAll(const String& ch1Status, const String& ch2Status);

private:
    float readVoltage();       // ZMPT101B via analog pin
    float readTemperature();   // DHT22
    float readHumidity();      // DHT22

    void readEnergyMetrics(float& current, float& power, float& energyKwh, float& frequency);
    void simulateEnergyMetrics(float& current, float& power, float& energyKwh, float& frequency);

    float _simulatedEnergyAccumulatorKwh = 0.0f;
    unsigned long _lastSimulateMs = 0;
};

static DHT dht(DHT_PIN, DHT_TYPE);

void Sensors::begin() {
    dht.begin();
    pinMode(ZMPT_VOLTAGE_PIN, INPUT);
    _lastSimulateMs = millis();
}

float Sensors::readVoltage() {
    // ZMPT101B outputs an analog AC waveform centered around ~1.65V (ESP32
    // ADC reference). A real implementation samples many points across a
    // few AC cycles and computes RMS. This simplified version reads one
    // raw sample and maps it onto a plausible mains-voltage range - replace
    // with a proper RMS sampling routine once the sensor is calibrated.
    int raw = analogRead(ZMPT_VOLTAGE_PIN);          // 0-4095 on ESP32
    float normalized = (float)raw / 4095.0f;          // 0.0 - 1.0
    float voltage = 210.0f + normalized * 30.0f;      // maps to ~210-240V band
    return voltage;
}

float Sensors::readTemperature() {
    float t = dht.readTemperature();
    if (isnan(t)) {
        Serial.println("[Sensors] DHT22 temperature read failed, using last-known fallback.");
        return 25.0f;  // safe fallback so one bad read doesn't stop telemetry
    }
    return t;
}

float Sensors::readHumidity() {
    float h = dht.readHumidity();
    if (isnan(h)) {
        Serial.println("[Sensors] DHT22 humidity read failed, using last-known fallback.");
        return 50.0f;
    }
    return h;
}

void Sensors::simulateEnergyMetrics(float& current, float& power, float& energyKwh, float& frequency) {
    // Deterministic-ish simulated load so dashboards have something
    // meaningful to show before PZEM-004T wiring is completed.
    unsigned long now = millis();
    float elapsedHours = (now - _lastSimulateMs) / 3600000.0f;
    _lastSimulateMs = now;

    current = 2.0f + (float)(esp_random() % 400) / 100.0f;   // ~2.0-6.0 A
    frequency = 49.9f + (float)(esp_random() % 20) / 100.0f;  // ~49.9-50.1 Hz
    power = current * 230.0f * 0.95f;                          // approx W (PF ~0.95)

    _simulatedEnergyAccumulatorKwh += (power / 1000.0f) * elapsedHours;
    energyKwh = _simulatedEnergyAccumulatorKwh;
}

void Sensors::readEnergyMetrics(float& current, float& power, float& energyKwh, float& frequency) {
    // TODO: implement real PZEM-004T UART/Modbus read here once hardware is
    // wired. Keep the same output parameters so no caller code changes.
    simulateEnergyMetrics(current, power, energyKwh, frequency);
}

SensorReading Sensors::readAll(const String& ch1Status, const String& ch2Status) {
    SensorReading r{};
    r.voltage = readVoltage();
    r.temperature = readTemperature();
    r.humidity = readHumidity();

    if (SIMULATE_ENERGY_SENSOR) {
        simulateEnergyMetrics(r.current, r.power, r.energy_kwh, r.frequency);
    } else {
        readEnergyMetrics(r.current, r.power, r.energy_kwh, r.frequency);
    }

    r.channel1_status = ch1Status;
    r.channel2_status = ch2Status;
    return r;
}

// ============================================================================
// RelayControl - Controls the 2-channel relay module.
// ============================================================================

class RelayControl {
public:
    void begin();
    void setChannel(int channel, bool on);   // channel: 1 or 2
    String statusString(int channel) const;  // "ON" / "OFF"

private:
    bool _state1 = true;   // default ON at boot (matches typical "always-on" fridge/fan use case)
    bool _state2 = true;

    int pinFor(int channel) const;
};

int RelayControl::pinFor(int channel) const {
    return channel == 1 ? RELAY_CHANNEL_1_PIN : RELAY_CHANNEL_2_PIN;
}

void RelayControl::begin() {
    pinMode(RELAY_CHANNEL_1_PIN, OUTPUT);
    pinMode(RELAY_CHANNEL_2_PIN, OUTPUT);
    // Most 2-channel relay boards are active-LOW (LOW = energized/ON).
    // Adjust the polarity below if your specific module is active-HIGH.
    digitalWrite(RELAY_CHANNEL_1_PIN, LOW);
    digitalWrite(RELAY_CHANNEL_2_PIN, LOW);
}

void RelayControl::setChannel(int channel, bool on) {
    int pin = pinFor(channel);
    digitalWrite(pin, on ? LOW : HIGH);  // active-LOW module
    if (channel == 1) _state1 = on; else _state2 = on;
    Serial.printf("[Relay] Channel %d set to %s\n", channel, on ? "ON" : "OFF");
}

String RelayControl::statusString(int channel) const {
    bool state = (channel == 1) ? _state1 : _state2;
    return state ? "ON" : "OFF";
}

// ============================================================================
// Globals
// ============================================================================

WiFiManager wifiManager;
ApiClient apiClient;
Sensors sensors;
RelayControl relays;

unsigned long lastTelemetryMs = 0;
unsigned long lastCommandPollMs = 0;

// Simple retry/backoff state for telemetry: on repeated failure, back off
// up to a capped multiple of the base interval instead of hammering an
// unreachable backend.
int telemetryFailStreak = 0;
const int MAX_BACKOFF_MULTIPLIER = 6;

void applyCommand(const PendingCommand& cmd) {
    if (cmd.channel != 1 && cmd.channel != 2) {
        Serial.printf("[Command] Ignoring command with invalid channel %d\n", cmd.channel);
        apiClient.ackCommand(cmd.id, false);
        return;
    }
    bool turnOn = (cmd.value == "ON");
    relays.setChannel(cmd.channel, turnOn);
    bool acked = apiClient.ackCommand(cmd.id, true);
    if (!acked) {
        Serial.printf("[Command] Applied command %d locally but failed to ack to backend "
                      "(will not be resent - backend already marked it delivered).\n", cmd.id);
    }
}

// ============================================================================
// Arduino entry points
// ============================================================================

void setup() {
    Serial.begin(115200);
    delay(200);
    Serial.println("\n[Boot] AmpPulse AI ESP32 firmware starting...");
    Serial.printf("[Boot] device_id=%s backend=%s:%d\n", DEVICE_ID, BACKEND_HOST, BACKEND_PORT);

    relays.begin();
    sensors.begin();
    wifiManager.begin(WIFI_SSID, WIFI_PASSWORD);
    apiClient.begin(BACKEND_HOST, BACKEND_PORT, DEVICE_ID, DEVICE_KEY);
}

void loop() {
    wifiManager.loop();

    if (!wifiManager.isConnected()) {
        // Nothing productive to do without Wi-Fi; wifiManager.loop() is
        // already retrying on its own timer.
        delay(50);
        return;
    }

    unsigned long now = millis();

    // ---- Telemetry ----
    unsigned long telemetryInterval = TELEMETRY_INTERVAL_MS *
        (1 + min(telemetryFailStreak, MAX_BACKOFF_MULTIPLIER));

    if (now - lastTelemetryMs >= telemetryInterval) {
        lastTelemetryMs = now;

        SensorReading reading = sensors.readAll(
            relays.statusString(1),
            relays.statusString(2)
        );

        String errorCode;
        bool ok = apiClient.postTelemetry(reading, errorCode);

        if (ok) {
            if (telemetryFailStreak > 0) {
                Serial.println("[Telemetry] Backend reachable again, resetting backoff.");
            }
            telemetryFailStreak = 0;
            Serial.printf("[Telemetry] Sent OK: V=%.1f I=%.2f P=%.1f E=%.3f T=%.1f H=%.1f\n",
                reading.voltage, reading.current, reading.power,
                reading.energy_kwh, reading.temperature, reading.humidity);
        } else {
            telemetryFailStreak++;
            Serial.printf("[Telemetry] Send failed (%s), fail streak=%d, next retry in %lums\n",
                errorCode.c_str(), telemetryFailStreak,
                (unsigned long)TELEMETRY_INTERVAL_MS * (1 + min(telemetryFailStreak, MAX_BACKOFF_MULTIPLIER)));
        }
    }

    // ---- Command polling ----
    if (now - lastCommandPollMs >= COMMAND_POLL_INTERVAL_MS) {
        lastCommandPollMs = now;

        PendingCommand commands[4];
        int count = apiClient.pollCommands(commands, 4);
        for (int i = 0; i < count; i++) {
            Serial.printf("[Command] Received: id=%d channel=%d value=%s\n",
                commands[i].id, commands[i].channel, commands[i].value.c_str());
            applyCommand(commands[i]);
        }
    }

    delay(10);  // small idle yield, not a functional blocking wait
}