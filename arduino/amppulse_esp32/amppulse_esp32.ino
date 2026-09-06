/*
  ESP32 Wireless Energy Monitoring & Control System

  Hardware:
  - ESP32-WROOM-DA
  - ZMPT101B AC Voltage Sensor
  - DHT22 Temperature & Humidity Sensor
  - 2-Channel Relay Module
  - PZEM-004T Current: SIMULATED for now

  Laptop:
  Connect laptop and ESP32 to the same Wi-Fi.
  Open the IP address shown in Serial Monitor.

  Example:
  http://192.168.1.8
*/

#include <WiFi.h>
#include <WebServer.h>
#include <DHT.h>

// =====================================================
// Wi-Fi
// =====================================================

const char* WIFI_SSID = "HOME";
const char* WIFI_PASSWORD = "Home@4127";

// =====================================================
// Pin Configuration
// =====================================================

// Relay
#define RELAY1_PIN 25
#define RELAY2_PIN 26

// ZMPT101B analog output
#define ZMPT_PIN 34

// DHT22
#define DHT_PIN 4
#define DHT_TYPE DHT22

// =====================================================
// Objects
// =====================================================

WebServer server(80);
DHT dht(DHT_PIN, DHT_TYPE);

// =====================================================
// Sensor Variables
// =====================================================

float voltage = 0.0;
float current = 0.0;
float power = 0.0;

float temperature = 0.0;
float humidity = 0.0;

// =====================================================
// Relay States
// =====================================================

bool relay1 = false;
bool relay2 = false;

// =====================================================
// Timing
// =====================================================

unsigned long lastSensorRead = 0;

const unsigned long SENSOR_INTERVAL = 2000;

// =====================================================
// ZMPT101B Calibration
// =====================================================
//
// This value depends on your ZMPT101B module.
//
// IMPORTANT:
// Calibrate this value using a known AC voltage.
//
// Start with this value and adjust if required.
//

float ZMPT_CALIBRATION = 0.325;

// =====================================================
// Read ZMPT101B
// =====================================================

float readVoltage() {

  const int samples = 500;

  float sum = 0;

  // -----------------------------
  // Find ADC average / offset
  // -----------------------------

  for (int i = 0; i < samples; i++) {

    int adcValue = analogRead(ZMPT_PIN);

    sum += adcValue;

    delayMicroseconds(200);
  }

  float offset = sum / samples;

  // -----------------------------
  // Calculate RMS
  // -----------------------------

  float squareSum = 0;

  for (int i = 0; i < samples; i++) {

    int adcValue = analogRead(ZMPT_PIN);

    float value = adcValue - offset;

    squareSum += value * value;

    delayMicroseconds(200);
  }

  float rms = sqrt(squareSum / samples);

  // -----------------------------
  // Convert to AC voltage
  // -----------------------------

  float result = rms * ZMPT_CALIBRATION;

  return result;
}

// =====================================================
// Simulated PZEM Current
// =====================================================
//
// PZEM-004T is not connected/working yet.
// So current is simulated.
//
// Change these values according to your demo.
//

float readCurrent() {

  if (relay1 || relay2) {

    return 1.50;

  } else {

    return 0.00;
  }
}

// =====================================================
// Read All Sensors
// =====================================================

void readSensors() {

  // -----------------------------
  // Voltage
  // -----------------------------

  voltage = readVoltage();

  // -----------------------------
  // Temperature
  // -----------------------------

  temperature = dht.readTemperature();

  // -----------------------------
  // Humidity
  // -----------------------------

  humidity = dht.readHumidity();

  // -----------------------------
  // Current
  // -----------------------------

  current = readCurrent();

  // -----------------------------
  // Power
  // -----------------------------

  power = voltage * current;

  // -----------------------------
  // Check DHT errors
  // -----------------------------

  if (isnan(temperature)) {

    temperature = 0;
  }

  if (isnan(humidity)) {

    humidity = 0;
  }

  // -----------------------------
  // Serial Monitor
  // -----------------------------

  Serial.println();
  Serial.println("================================");

  Serial.print("Voltage     : ");
  Serial.print(voltage, 1);
  Serial.println(" V");

  Serial.print("Current     : ");
  Serial.print(current, 2);
  Serial.println(" A");

  Serial.print("Power       : ");
  Serial.print(power, 1);
  Serial.println(" W");

  Serial.print("Temperature : ");
  Serial.print(temperature, 1);
  Serial.println(" C");

  Serial.print("Humidity    : ");
  Serial.print(humidity, 1);
  Serial.println(" %");

  Serial.print("Relay 1     : ");
  Serial.println(relay1 ? "ON" : "OFF");

  Serial.print("Relay 2     : ");
  Serial.println(relay2 ? "ON" : "OFF");

  Serial.println("================================");
}

// =====================================================
// CORS + Response Helper
// =====================================================
//
// The dashboard web app runs on a different origin
// (http://<laptop>:8000). Without these headers the
// browser blocks reading the ESP32's responses, so
// fetch("/data") fails silently in direct mode.
//

void sendResponse(int code, const char* type, const String& body) {

  server.sendHeader(
    "Access-Control-Allow-Origin",
    "*"
  );

  server.sendHeader(
    "Access-Control-Allow-Methods",
    "GET, POST, OPTIONS"
  );

  server.sendHeader(
    "Access-Control-Allow-Headers",
    "Content-Type"
  );

  // Live sensor data must never come from a browser/proxy cache
  server.sendHeader(
    "Cache-Control",
    "no-store"
  );

  server.send(code, type, body);
}

void handleNotFound() {

  // Allow browser preflight (OPTIONS) requests
  if (server.method() == HTTP_OPTIONS) {

    server.sendHeader(
      "Access-Control-Allow-Origin",
      "*"
    );

    server.sendHeader(
      "Access-Control-Allow-Methods",
      "GET, POST, OPTIONS"
    );

    server.sendHeader(
      "Access-Control-Allow-Headers",
      "Content-Type"
    );

    server.sendHeader(
      "Cache-Control",
      "no-store"
    );

    server.send(
      204,
      "text/plain",
      ""
    );

    return;
  }

  sendResponse(
    404,
    "text/plain",
    "Not found"
  );
}

// =====================================================
// JSON Data
// =====================================================

void handleData() {

  String json = "{";

  json += "\"voltage\":";
  json += String(voltage, 1);

  json += ",\"current\":";
  json += String(current, 2);

  json += ",\"power\":";
  json += String(power, 1);

  json += ",\"temperature\":";
  json += String(temperature, 1);

  json += ",\"humidity\":";
  json += String(humidity, 1);

  json += ",\"relay1\":";
  json += relay1 ? "true" : "false";

  json += ",\"relay2\":";
  json += relay2 ? "true" : "false";

  json += "}";

  sendResponse(200, "application/json", json);
}

// =====================================================
// Relay 1 ON
// =====================================================

void relay1On() {

  relay1 = true;

  // Active LOW relay
  digitalWrite(RELAY1_PIN, LOW);

  sendResponse(200, "text/plain", "Relay 1 ON");

  Serial.println("Relay 1 -> ON");
}

// =====================================================
// Relay 1 OFF
// =====================================================

void relay1Off() {

  relay1 = false;

  // Active LOW relay
  digitalWrite(RELAY1_PIN, HIGH);

  sendResponse(200, "text/plain", "Relay 1 OFF");

  Serial.println("Relay 1 -> OFF");
}

// =====================================================
// Relay 2 ON
// =====================================================

void relay2On() {

  relay2 = true;

  // Active LOW relay
  digitalWrite(RELAY2_PIN, LOW);

  sendResponse(200, "text/plain", "Relay 2 ON");

  Serial.println("Relay 2 -> ON");
}

// =====================================================
// Relay 2 OFF
// =====================================================

void relay2Off() {

  relay2 = false;

  // Active LOW relay
  digitalWrite(RELAY2_PIN, HIGH);

  sendResponse(200, "text/plain", "Relay 2 OFF");

  Serial.println("Relay 2 -> OFF");
}

// =====================================================
// Main Web Page
// =====================================================

void handleRoot() {

  String html = R"rawliteral(

<!DOCTYPE html>

<html>

<head>

<meta name="viewport"
      content="width=device-width, initial-scale=1">

<title>ESP32 Energy Monitor</title>

<style>

body {
  font-family: Arial, sans-serif;
  background: #f2f2f2;
  margin: 0;
  padding: 20px;
  text-align: center;
}

.container {
  max-width: 800px;
  margin: auto;
}

h1 {
  margin-bottom: 25px;
}

.card {
  background: white;
  padding: 20px;
  margin-bottom: 18px;
  border-radius: 15px;
  box-shadow: 0 3px 10px rgba(0,0,0,0.15);
}

.grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 15px;
}

.parameter {
  background: #fafafa;
  padding: 15px;
  border-radius: 10px;
}

.label {
  font-size: 16px;
  color: #555;
}

.value {
  font-size: 30px;
  font-weight: bold;
  margin-top: 8px;
}

button {
  border: none;
  padding: 12px 25px;
  margin: 5px;
  border-radius: 8px;
  font-size: 16px;
  cursor: pointer;
}

.on {
  background: #4CAF50;
  color: white;
}

.off {
  background: #f44336;
  color: white;
}

.status {
  font-weight: bold;
  margin: 10px;
}

.connected {
  color: green;
}

</style>

</head>


<body>

<div class="container">

<h1>ESP32 Energy Monitoring System</h1>

<div class="card">

<h2>Electrical Parameters</h2>

<div class="grid">

<div class="parameter">

<div class="label">
Voltage
</div>

<div class="value">
<span id="voltage">0.0</span> V
</div>

</div>


<div class="parameter">

<div class="label">
Current
</div>

<div class="value">
<span id="current">0.00</span> A
</div>

</div>


<div class="parameter">

<div class="label">
Power
</div>

<div class="value">
<span id="power">0.0</span> W
</div>

</div>

</div>

</div>


<div class="card">

<h2>Environment</h2>

<div class="grid">

<div class="parameter">

<div class="label">
Temperature
</div>

<div class="value">
<span id="temperature">0.0</span> °C
</div>

</div>


<div class="parameter">

<div class="label">
Humidity
</div>

<div class="value">
<span id="humidity">0.0</span> %
</div>

</div>

</div>

</div>


<div class="card">

<h2>Wireless Device Control</h2>


<h3>Relay 1</h3>

<button
class="on"
onclick="relay1Control('on')">

ON

</button>


<button
class="off"
onclick="relay1Control('off')">

OFF

</button>


<div class="status">

Status:

<span id="relay1">
OFF
</span>

</div>


<hr>


<h3>Relay 2</h3>

<button
class="on"
onclick="relay2Control('on')">

ON

</button>


<button
class="off"
onclick="relay2Control('off')">

OFF

</button>


<div class="status">

Status:

<span id="relay2">
OFF
</span>

</div>

</div>


<div class="card">

<p class="connected">
● ESP32 Connected
</p>

<p>
Automatic data update every 2 seconds
</p>

</div>

</div>


<script>

// =====================================
// Update Sensor Data
// =====================================

function updateData() {

  fetch('/data')

  .then(response => response.json())

  .then(data => {

    document.getElementById('voltage').innerText =
      Number(data.voltage).toFixed(1);

    document.getElementById('current').innerText =
      Number(data.current).toFixed(2);

    document.getElementById('power').innerText =
      Number(data.power).toFixed(1);

    document.getElementById('temperature').innerText =
      Number(data.temperature).toFixed(1);

    document.getElementById('humidity').innerText =
      Number(data.humidity).toFixed(1);


    document.getElementById('relay1').innerText =
      data.relay1 ? "ON" : "OFF";


    document.getElementById('relay2').innerText =
      data.relay2 ? "ON" : "OFF";

  })

  .catch(error => {

    console.log("Connection error:", error);

  });

}


// =====================================
// Relay 1
// =====================================

function relay1Control(state) {

  fetch('/relay1/' + state)

  .then(() => {

    updateData();

  });

}


// =====================================
// Relay 2
// =====================================

function relay2Control(state) {

  fetch('/relay2/' + state)

  .then(() => {

    updateData();

  });

}


// =====================================
// Automatic Update
// =====================================

setInterval(updateData, 2000);

updateData();

</script>

</body>

</html>

)rawliteral";

  sendResponse(200, "text/html", html);
}

// =====================================================
// Setup
// =====================================================

void setup() {

  // -----------------------------
  // Serial
  // -----------------------------

  Serial.begin(115200);

  delay(1000);

  Serial.println();
  Serial.println();
  Serial.println("================================");
  Serial.println("ESP32 Energy Monitoring System");
  Serial.println("================================");


  // -----------------------------
  // Relay
  // -----------------------------

  pinMode(RELAY1_PIN, OUTPUT);
  pinMode(RELAY2_PIN, OUTPUT);

  // Relay OFF at startup
  digitalWrite(RELAY1_PIN, HIGH);
  digitalWrite(RELAY2_PIN, HIGH);


  // -----------------------------
  // ZMPT101B
  // -----------------------------

  pinMode(ZMPT_PIN, INPUT);

  analogReadResolution(12);

  // The ZMPT101B's output swings up to ~3.3 V. The default 0 dB
  // attenuation caps the ADC at ~1.1 V (clipping = wrong readings).
  // 11 dB gives the full 0-3.3 V range.
  analogSetPinAttenuation(
    ZMPT_PIN,
    ADC_11db
  );


  // -----------------------------
  // DHT22
  // -----------------------------

  dht.begin();


  // -----------------------------
  // Wi-Fi
  // -----------------------------

  Serial.println("Connecting to Wi-Fi...");

  WiFi.begin(
    WIFI_SSID,
    WIFI_PASSWORD
  );

  while (WiFi.status() != WL_CONNECTED) {

    delay(500);

    Serial.print(".");
  }

  Serial.println();

  Serial.println("Wi-Fi connected!");

  Serial.print("IP Address: ");

  Serial.println(
    WiFi.localIP()
  );


  // -----------------------------
  // Web Server
  // -----------------------------

  server.on(
    "/",
    HTTP_GET,
    handleRoot
  );

  server.on(
    "/data",
    HTTP_GET,
    handleData
  );

  server.on(
    "/relay1/on",
    HTTP_GET,
    relay1On
  );

  server.on(
    "/relay1/off",
    HTTP_GET,
    relay1Off
  );

  server.on(
    "/relay2/on",
    HTTP_GET,
    relay2On
  );

  server.on(
    "/relay2/off",
    HTTP_GET,
    relay2Off
  );

  // Send CORS headers for any unregistered request
  // (including the browser's OPTIONS preflight)
  server.onNotFound(handleNotFound);

  server.begin();

  Serial.println("Web server started.");

  Serial.println();

  Serial.print("Open in laptop: http://");

  Serial.println(
    WiFi.localIP()
  );

  Serial.println();


  // -----------------------------
  // First Sensor Reading
  // -----------------------------

  readSensors();
}

// =====================================================
// Loop
// =====================================================

void loop() {

  // Handle browser requests
  server.handleClient();


  // Read sensors periodically
  if (
    millis() - lastSensorRead >=
    SENSOR_INTERVAL
  ) {

    lastSensorRead = millis();

    readSensors();
  }
}