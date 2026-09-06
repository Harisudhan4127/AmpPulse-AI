#include "api_client.h"
#include "config.h"
#include <HTTPClient.h>
#include <WiFi.h>
#include <time.h>

static uint32_t epochSeconds() {
    time_t now;
    time(&now);
    if (now > 1600000000) {
        return (uint32_t)now;
    }
    return (uint32_t)(millis() / 1000);
}

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
