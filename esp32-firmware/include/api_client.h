/*
 * api_client.h - HTTP/JSON client for talking to the AmpPulse AI backend.
 *
 * Wraps every backend call the ESP32 needs: telemetry POST, command GET/ack.
 * Every method:
 *   - builds the full URL from BACKEND_HOST/BACKEND_PORT (never "localhost")
 *   - sets a request timeout (HTTP_TIMEOUT_MS)
 *   - sends the X-Device-Key header for authentication
 *   - returns a simple success/failure result so callers can decide on
 *     retry/backoff without needing to know HTTP internals
 */
#pragma once
#include <Arduino.h>
#include <ArduinoJson.h>

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
