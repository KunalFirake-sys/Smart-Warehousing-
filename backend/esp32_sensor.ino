/**
 * esp32_sensor.ino
 * 
 * Reads HX711 load cells (one per shelf A/B/C) and IR proximity sensors,
 * then POSTs a batch JSON payload to the FastAPI backend every SEND_INTERVAL_MS.
 *
 * Wiring assumptions:
 *   HX711 A → DOUT: GPIO 16,  SCK: GPIO 17
 *   HX711 B → DOUT: GPIO 18,  SCK: GPIO 19
 *   HX711 C → DOUT: GPIO 21,  SCK: GPIO 22
 *   IR A    → GPIO 25  (LOW = beam blocked / object present)
 *   IR B    → GPIO 26
 *   IR C    → GPIO 27
 *
 * Library deps (install via Arduino Library Manager):
 *   - HX711 by bogde  (search: "HX711 Arduino Library")
 *   - ArduinoJson      (search: "ArduinoJson")
 *   - WiFi             (built-in ESP32 core)
 *   - HTTPClient       (built-in ESP32 core)
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include "HX711.h"

// ── WiFi credentials ────────────────────────────────────────────────────────
const char* WIFI_SSID     = "YOUR_SSID";
const char* WIFI_PASSWORD = "YOUR_PASSWORD";

// ── Backend endpoint ────────────────────────────────────────────────────────
// Replace with the LAN IP of the PC running your FastAPI server.
const char* SERVER_URL = "http://192.168.1.100:8000/sensor";

// How often to send data (milliseconds)
const unsigned long SEND_INTERVAL_MS = 800;

// ── HX711 calibration factors ───────────────────────────────────────────────
// Run a calibration sketch once with known weights and fill these in.
// Positive = weight on scale increases reading; adjust sign if inverted.
const float CALIB_A = 420.0;   // units: raw / gram
const float CALIB_B = 420.0;
const float CALIB_C = 420.0;

// Tare offset (raw units at zero load) — measure with nothing on the shelf.
// Leave as 0 if you call hx.tare() at boot instead.
const long TARE_A = 0;
const long TARE_B = 0;
const long TARE_C = 0;

// ── Pin definitions ─────────────────────────────────────────────────────────
// HX711
const int DOUT_A = 16, SCK_A = 17;
const int DOUT_B = 18, SCK_B = 19;
const int DOUT_C = 21, SCK_C = 22;

// IR sensors — LOW when beam is blocked (object present)
const int IR_A = 25;
const int IR_B = 26;
const int IR_C = 27;

// ── Globals ─────────────────────────────────────────────────────────────────
HX711 hx_A, hx_B, hx_C;
unsigned long lastSendMs = 0;

// ── Setup ───────────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  delay(200);

  // IR pins
  pinMode(IR_A, INPUT_PULLUP);
  pinMode(IR_B, INPUT_PULLUP);
  pinMode(IR_C, INPUT_PULLUP);

  // HX711 init
  hx_A.begin(DOUT_A, SCK_A);
  hx_B.begin(DOUT_B, SCK_B);
  hx_C.begin(DOUT_C, SCK_C);

  // If you didn't hard-code tare offsets, tare at boot:
  Serial.println("Taring load cells — keep shelves empty...");
  if (TARE_A == 0) hx_A.tare();  else hx_A.set_offset(TARE_A);
  if (TARE_B == 0) hx_B.tare();  else hx_B.set_offset(TARE_B);
  if (TARE_C == 0) hx_C.tare();  else hx_C.set_offset(TARE_C);

  hx_A.set_scale(CALIB_A);
  hx_B.set_scale(CALIB_B);
  hx_C.set_scale(CALIB_C);
  Serial.println("HX711 ready.");

  // WiFi
  Serial.printf("Connecting to %s", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(400);
    Serial.print(".");
  }
  Serial.printf("\nConnected. IP: %s\n", WiFi.localIP().toString().c_str());
}

// ── Read helpers ─────────────────────────────────────────────────────────────

float readWeightKg(HX711& hx) {
  if (!hx.is_ready()) return -1.0;   // -1 = sensor not ready this cycle
  float grams = hx.get_units(3);     // average 3 readings
  if (grams < 0) grams = 0;          // clamp negative noise
  return grams / 1000.0;             // → kg
}

bool readIR(int pin) {
  // LOW means beam broken = object present = blocked
  return digitalRead(pin) == LOW;
}

// ── Main loop ────────────────────────────────────────────────────────────────
void loop() {
  unsigned long now = millis();
  if (now - lastSendMs < SEND_INTERVAL_MS) return;
  lastSendMs = now;

  // Read all sensors
  float wA = readWeightKg(hx_A);
  float wB = readWeightKg(hx_B);
  float wC = readWeightKg(hx_C);

  bool irA = readIR(IR_A);
  bool irB = readIR(IR_B);
  bool irC = readIR(IR_C);

  // Build batch JSON — matches exactly what POST /sensor expects
  // {
  //   "readings": [
  //     {"shelf": "A", "weight_kg": 0.183, "ir_blocked": false},
  //     ...
  //   ]
  // }
  StaticJsonDocument<512> doc;
  JsonArray readings = doc.createNestedArray("readings");

  auto addReading = [&](const char* shelf, float w, bool ir) {
    JsonObject r = readings.createNestedObject();
    r["shelf"]      = shelf;
    if (w >= 0) r["weight_kg"] = serialized(String(w, 4));  // 4 decimal places
    r["ir_blocked"] = ir;
  };

  addReading("A", wA, irA);
  addReading("B", wB, irB);
  addReading("C", wC, irC);

  String body;
  serializeJson(doc, body);

  // Send to FastAPI
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[WARN] WiFi disconnected — skipping POST.");
    return;
  }

  HTTPClient http;
  http.begin(SERVER_URL);
  http.addHeader("Content-Type", "application/json");

  int code = http.POST(body);
  if (code > 0) {
    Serial.printf("[OK]  POST %d | A=%.4fkg ir=%d | B=%.4fkg ir=%d | C=%.4fkg ir=%d\n",
                  code, wA, irA, wB, irB, wC, irC);
  } else {
    Serial.printf("[ERR] POST failed: %s\n", http.errorToString(code).c_str());
  }
  http.end();
}
