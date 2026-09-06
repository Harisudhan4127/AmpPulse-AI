/*
 * AmpPulse AI - ESP32 Firmware (MVP)
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
#include "config.h"
#include "wifi_manager.h"
#include "api_client.h"
#include "sensors.h"
#include "relay_control.h"

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
