/*
 * relay_control.h - Controls the 2-channel relay module.
 */
#pragma once
#include <Arduino.h>

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
