#include "wifi_manager.h"
#include "config.h"
#include <WiFi.h>
#include <time.h>

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
