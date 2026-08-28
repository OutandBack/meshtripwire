/*
 * ESP32 vehicle-detection node: QMC5883L magnetometer (GY-271 module, I2C).
 *
 * A vehicle's ferrous mass shifts the local magnetic field as it passes within
 * a few meters. This node samples the field ~50 Hz, tracks a slow baseline, and
 * reports an event when the magnitude deviates past a threshold. Detection is
 * on-device; only events leave the node.
 *
 * Backhaul (OUTPUT_SERIAL), same pattern as esp32_sniffer:
 *   0 = publish {"event":"vehicle","from":NODE_ID,"mag":<delta>} over WiFi/MQTT
 *   1 = print compact "V,<delta>" to Serial for a wired Meshtastic node to relay
 *       over LoRa (serial_bridge maps the relay node back to a sensor name)
 *
 * Wiring (GY-271): VCC->3V3, GND->GND, SDA->GPIO SDA, SCL->GPIO SCL (default
 * Wire pins for your board). No extra libraries beyond PubSubClient for MQTT;
 * the QMC5883L is driven by raw register access over Wire.
 *
 * Placement: within ~2-5 m of the drive lane (farther works for trucks). Mount
 * rigidly — the sensor measures field change, so a swaying post is a false alarm.
 */

// ---- Config: edit these ----
#define OUTPUT_SERIAL 0                 // 0 = WiFi/MQTT, 1 = Serial line for LoRa backhaul
#define SERIAL_MESHCORE 0               // with OUTPUT_SERIAL 1: 0 = plain text lines (Meshtastic
                                        // Serial module), 1 = MeshCore companion-radio framing
#define DEBUG_PRINT   1                 // 1 = print mag/baseline/delta 1/s for calibration.
                                        // Ignored when OUTPUT_SERIAL=1: a wired Meshtastic
                                        // node would relay every debug line over LoRa.
const uint8_t MESHCORE_CHANNEL = 0;     // channel index on the wired MeshCore companion node
const char* WIFI_SSID   = "your-ssid";
const char* WIFI_PASS   = "your-pass";
const char* MQTT_HOST   = "192.168.1.10";
const uint16_t MQTT_PORT = 1883;
const char* MQTT_USER   = "";           // "" = anonymous
const char* MQTT_PASS   = "";
const char* MQTT_TOPIC  = "meshtastic/receive";
const char* NODE_ID     = "gate";       // tag for MQTT events (serial/LoRa maps by relay node instead)

// Calibration knobs — real sensors drift and every site's field differs. Tune
// TRIGGER_LSB against your own drive-bys: log deltas first, then set the
// threshold between ambient noise and the smallest vehicle you care about.
const float    BASELINE_ALPHA = 0.001f; // slow EMA; absorbs diurnal drift and a car that parks
const int      TRIGGER_LSB    = 300;    // |mag - baseline| that counts as a vehicle (LSB @ 2G range)
const int      CONSECUTIVE_N  = 5;      // samples over threshold before firing (~0.1 s @ 50 Hz)
const uint32_t COOLDOWN_MS    = 15000;  // one event per pass, not one per wheel
const uint32_t RESEED_MS      = 60000;  // sustained shift (a car that parked) becomes the new baseline
// ----------------------------

#include <Wire.h>
#if !OUTPUT_SERIAL
  #include <WiFi.h>
  #include <PubSubClient.h>
  WiFiClient net;
  PubSubClient mqtt(net);
#endif

const uint8_t QMC_ADDR = 0x0D;

#if OUTPUT_SERIAL && SERIAL_MESHCORE
// MeshCore companion serial protocol: '<' len_lo len_hi payload. Channel
// messages carry no sender id, so each line is prefixed with "NODE_ID:".
void meshcore_send_line(const char* line) {
  uint8_t buf[96];
  uint32_t ts = millis() / 1000;  // no RTC; monotonic keeps the dedup hash moving
  int n = 0;
  buf[n++] = 0x03;                // CMD_SEND_CHANNEL_TXT_MSG
  buf[n++] = 0x00;                // txt_type: plain
  buf[n++] = MESHCORE_CHANNEL;
  buf[n++] = ts & 0xFF; buf[n++] = (ts >> 8) & 0xFF;
  buf[n++] = (ts >> 16) & 0xFF; buf[n++] = (ts >> 24) & 0xFF;
  n += snprintf((char*)buf + n, sizeof(buf) - n, "%s:%s", NODE_ID, line);
  Serial.write((uint8_t)0x3C);
  Serial.write((uint8_t)(n & 0xFF));
  Serial.write((uint8_t)(n >> 8));
  Serial.write(buf, n);
}

void meshcore_appstart() {
  delay(1500);  // let the companion radio boot before the handshake
  const uint8_t hello[] = {0x01, 0x03, ' ', ' ', ' ', ' ', ' ', ' ', 'm', 't', 'w'};  // CMD_APP_START
  Serial.write((uint8_t)0x3C);
  Serial.write((uint8_t)sizeof(hello));
  Serial.write((uint8_t)0);
  Serial.write(hello, sizeof(hello));
}
#endif

void qmc_write(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(QMC_ADDR);
  Wire.write(reg);
  Wire.write(val);
  Wire.endTransmission();
}

// Reads X/Y/Z (2G range, ~12000 LSB/Gauss). Returns false if no data ready.
bool qmc_read(int16_t& x, int16_t& y, int16_t& z) {
  Wire.beginTransmission(QMC_ADDR);
  Wire.write(0x06);                     // status register
  if (Wire.endTransmission(false) != 0) return false;
  Wire.requestFrom(QMC_ADDR, (uint8_t)1);
  if (!Wire.available() || !(Wire.read() & 0x01)) return false;  // DRDY
  Wire.beginTransmission(QMC_ADDR);
  Wire.write(0x00);                     // data registers, LSB first
  Wire.endTransmission(false);
  Wire.requestFrom(QMC_ADDR, (uint8_t)6);
  if (Wire.available() < 6) return false;
  x = Wire.read() | (Wire.read() << 8);
  y = Wire.read() | (Wire.read() << 8);
  z = Wire.read() | (Wire.read() << 8);
  return true;
}

void report(int delta) {
#if OUTPUT_SERIAL
  char line[16];
  snprintf(line, sizeof(line), "V,%d", delta);
  #if SERIAL_MESHCORE
    meshcore_send_line(line);
  #else
    Serial.println(line);
  #endif
#else
  char payload[80];
  snprintf(payload, sizeof(payload),
           "{\"event\":\"vehicle\",\"from\":\"%s\",\"mag\":%d}", NODE_ID, delta);
  if (mqtt.connected()) mqtt.publish(MQTT_TOPIC, payload);
#endif
}

void setup() {
  Serial.begin(115200);
#if OUTPUT_SERIAL && SERIAL_MESHCORE
  meshcore_appstart();
#endif
  Wire.begin();
  qmc_write(0x0B, 0x01);  // SET/RESET period (datasheet-recommended init)
  qmc_write(0x09, 0x0D);  // continuous mode, 50 Hz, 2G range, OSR 512
#if DEBUG_PRINT && !OUTPUT_SERIAL
  delay(100);
  Wire.beginTransmission(QMC_ADDR);
  Serial.println(Wire.endTransmission() == 0
                 ? "qmc5883l_vehicle: sensor found"
                 : "qmc5883l_vehicle: QMC5883L NOT FOUND at 0x0D - check wiring");
#endif
#if !OUTPUT_SERIAL
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) delay(250);
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.connect(NODE_ID, MQTT_USER[0] ? MQTT_USER : nullptr, MQTT_PASS[0] ? MQTT_PASS : nullptr);
#endif
}

float baseline = 0;
int over = 0;
uint32_t lastEvent = 0, lastReconnect = 0, triggerStart = 0;

void loop() {
#if OUTPUT_SERIAL && SERIAL_MESHCORE
  while (Serial.available()) Serial.read();  // drain companion-radio responses
#endif
#if !OUTPUT_SERIAL
  if (!mqtt.connected() && millis() - lastReconnect > 5000) {
    lastReconnect = millis();
    if (WiFi.status() != WL_CONNECTED) WiFi.reconnect();
    mqtt.connect(NODE_ID, MQTT_USER[0] ? MQTT_USER : nullptr, MQTT_PASS[0] ? MQTT_PASS : nullptr);
  }
  mqtt.loop();
#endif

  int16_t x, y, z;
  if (!qmc_read(x, y, z)) { delay(5); return; }
  float mag = sqrtf((float)x * x + (float)y * y + (float)z * z);
  if (baseline == 0) baseline = mag;    // first sample seeds the baseline

  int delta = (int)fabsf(mag - baseline);
  bool triggered = delta > TRIGGER_LSB;
#if DEBUG_PRINT && !OUTPUT_SERIAL
  static uint32_t lastDbg = 0;
  if (millis() - lastDbg > 1000) {
    lastDbg = millis();
    Serial.printf("mag=%.0f baseline=%.0f delta=%d%s\n",
                  mag, baseline, delta, triggered ? " TRIGGERED" : "");
  }
#endif
  if (!triggered) {
    // Only adapt when quiet: a passing vehicle must not be absorbed mid-event.
    baseline += BASELINE_ALPHA * (mag - baseline);
    over = 0;
    triggerStart = 0;
  } else if (!triggerStart) {
    triggerStart = millis();
    over = 1;
  } else if (millis() - triggerStart > RESEED_MS) {
    baseline = mag;                     // the shift is the new normal (car parked)
    over = 0;
    triggerStart = 0;
  } else if (over < CONSECUTIVE_N && ++over == CONSECUTIVE_N
             && millis() - lastEvent > COOLDOWN_MS) {
    lastEvent = millis();
    report(delta);
  }
  delay(15);                            // ~50 Hz pacing to match the ODR
}
