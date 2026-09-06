#include "relay_control.h"
#include "config.h"

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
