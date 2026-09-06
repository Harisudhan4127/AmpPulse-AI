/*
 * sensors.h - Reads all physical sensors and produces one SensorReading.
 *
 * Structured so each sensor has its own small read function. Adding a new
 * sensor later means adding one more readX() function and one more field
 * in api_client.h's SensorReading struct - nothing else changes.
 */
#pragma once
#include "api_client.h"

class Sensors {
public:
    void begin();
    SensorReading readAll(const String& ch1Status, const String& ch2Status);

private:
    float readVoltage();       // ZMPT101B via analog pin
    float readTemperature();   // DHT22
    float readHumidity();      // DHT22

    // PZEM-004T is not currently wired (see project notes). When real
    // hardware is available, implement UART reads here and flip
    // SIMULATE_ENERGY_SENSOR to false in config.h - callers don't change.
    void readEnergyMetrics(float& current, float& power, float& energyKwh, float& frequency);
    void simulateEnergyMetrics(float& current, float& power, float& energyKwh, float& frequency);

    float _simulatedEnergyAccumulatorKwh = 0.0f;
    unsigned long _lastSimulateMs = 0;
};
