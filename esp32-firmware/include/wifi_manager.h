/*
 * wifi_manager.h - Wi-Fi connection with automatic reconnection.
 *
 * Non-blocking: connect() kicks off a connection attempt and returns
 * immediately. Call loop() every main-loop iteration; it detects
 * disconnects and retries on a timer instead of blocking with delay().
 */
#pragma once
#include <Arduino.h>

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
