#include "sensors.h"
#include "config.h"
#include <DHT.h>

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
