/*
 * ESP32 lightning node: AS3935 franklin lightning sensor (I2C module, e.g.
 * CJMCU-3935 / GY-AS3935).
 *
 * Detects the RF signature of lightning strikes up to ~40 km away and reports
 * each with an estimated distance to the storm front. The base station uses
 * these to label piezo vibration alerts that coincide with thunder — locally,
 * with no weather API and no Internet, which is the point of this project.
 *
 * Backhaul (OUTPUT_SERIAL), same pattern as the other sensor nodes:
 *   0 = publish {"event":"lightning","from":NODE_ID,"km":N} over WiFi/MQTT
 *   1 = print compact "L,<km>" to Serial for a wired mesh node to relay
 *       (Meshtastic Serial module, or MeshCore with SERIAL_MESHCORE 1)
 *
 * Wiring (I2C): VCC->3V3, GND->GND, SDA->GPIO8, SCL->GPIO9 (C3 defaults),
 * IRQ->IRQ_PIN. Mount away from switching power supplies and LED drivers —
 * the AS3935 hears electrical noise as disturbers.
 */

// ---- Config: edit these ----
#define OUTPUT_SERIAL 0                 // 0 = WiFi/MQTT, 1 = Serial line for LoRa backhaul
#define SERIAL_MESHCORE 0               // with OUTPUT_SERIAL 1: 0 = plain text lines (Meshtastic
                                        // Serial module), 1 = MeshCore companion-radio framing
#define DEBUG_PRINT   1                 // 1 = print every IRQ (noise/disturber/strike) for tuning.
                                        // Ignored when OUTPUT_SERIAL=1: a wired Meshtastic
                                        // node would relay every debug line over LoRa.
const int   IRQ_PIN     = 4;            // AS3935 IRQ output
const char* WIFI_SSID   = "your-ssid";
const char* WIFI_PASS   = "your-pass";
const char* MQTT_HOST   = "192.168.1.10";
const uint16_t MQTT_PORT = 1883;
const char* MQTT_USER   = "";           // "" = anonymous
const char* MQTT_PASS   = "";
const char* MQTT_TOPIC  = "meshtastic/receive";
const char* NODE_ID     = "gate";       // tag for MQTT events (serial/LoRa maps by relay node instead)
const uint8_t MESHCORE_CHANNEL = 0;     // channel index on the wired MeshCore companion node

// Calibration knobs — every module and site differs. Flash with DEBUG_PRINT on
// during a quiet day: frequent "noise"/"disturber" prints mean raise NOISE_FLOOR
// or WATCHDOG, or move the module away from electronics. TUNING_CAP trims the
// antenna (0-15, 8 pF steps); many breakouts note their trimmed value.
const bool     OUTDOOR      = true;     // false = indoor AFE gain (much more sensitive)
const uint8_t  NOISE_FLOOR  = 2;        // 0-7, ambient RF noise rejection
const uint8_t  WATCHDOG     = 2;        // 0-15, signal validation threshold
const uint8_t  SPIKE_REJECT = 2;        // 0-15, higher rejects more man-made spikes
const uint8_t  TUNING_CAP   = 0;        // 0-15, antenna tuning (module-specific)
const uint32_t COOLDOWN_MS  = 5000;     // min gap between reports (storms strike often)
// ----------------------------

#include <Wire.h>
#if !OUTPUT_SERIAL
  #include <WiFi.h>
  #include <PubSubClient.h>
  WiFiClient net;
  PubSubClient mqtt(net);
#endif

const uint8_t AS_ADDR = 0x03;           // 0x01/0x02/0x03 depending on A0/A1 straps

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

void as_write(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(AS_ADDR);
  Wire.write(reg);
  Wire.write(val);
  Wire.endTransmission();
}

uint8_t as_read(uint8_t reg) {
  Wire.beginTransmission(AS_ADDR);
  Wire.write(reg);
  Wire.endTransmission(false);
  Wire.requestFrom(AS_ADDR, (uint8_t)1);
  return Wire.available() ? Wire.read() : 0xFF;
}

void report(int km) {
#if OUTPUT_SERIAL
  char line[12];
  snprintf(line, sizeof(line), "L,%d", km);
  #if SERIAL_MESHCORE
    meshcore_send_line(line);
  #else
    Serial.println(line);
  #endif
#else
  char payload[80];
  snprintf(payload, sizeof(payload),
           "{\"event\":\"lightning\",\"from\":\"%s\",\"km\":%d}", NODE_ID, km);
  if (mqtt.connected()) mqtt.publish(MQTT_TOPIC, payload);
#endif
}

void setup() {
  Serial.begin(115200);
#if OUTPUT_SERIAL && SERIAL_MESHCORE
  meshcore_appstart();
#endif
  Wire.begin();
  pinMode(IRQ_PIN, INPUT);

  as_write(0x3C, 0x96);                 // PRESET_DEFAULT: reset all registers
  delay(3);
  as_write(0x3D, 0x96);                 // CALIB_RCO: calibrate internal oscillators
  delay(3);
  // AFE gain [5:1]: outdoor 01110, indoor 10010 (datasheet-recommended values)
  as_write(0x00, (OUTDOOR ? 0x0E : 0x12) << 1);
  as_write(0x01, ((NOISE_FLOOR & 0x07) << 4) | (WATCHDOG & 0x0F));
  as_write(0x02, 0xC0 | (SPIKE_REJECT & 0x0F));   // keep reserved bits, min strikes = 1
  as_write(0x08, TUNING_CAP & 0x0F);

#if DEBUG_PRINT && !OUTPUT_SERIAL
  delay(100);
  Wire.beginTransmission(AS_ADDR);
  Serial.println(Wire.endTransmission() == 0
                 ? "as3935_lightning: sensor found"
                 : "as3935_lightning: AS3935 NOT FOUND at 0x03 - check wiring/address");
#endif

#if !OUTPUT_SERIAL
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) delay(250);
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.connect(NODE_ID, MQTT_USER[0] ? MQTT_USER : nullptr, MQTT_PASS[0] ? MQTT_PASS : nullptr);
#endif
}

uint32_t lastReport = 0, lastReconnect = 0;

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

  if (digitalRead(IRQ_PIN) == HIGH) {
    delay(3);                           // datasheet: wait 2 ms before reading INT reason
    uint8_t reason = as_read(0x03) & 0x0F;
    if (reason == 0x08) {               // lightning
      int km = as_read(0x07) & 0x3F;    // 1 = overhead, 63 = out of range
      if (!lastReport || millis() - lastReport > COOLDOWN_MS) {  // 0 = never fired
        lastReport = millis();
        report(km);
      }
#if DEBUG_PRINT && !OUTPUT_SERIAL
      Serial.printf("lightning: %d km\n", km);
    } else if (reason == 0x04) {
      Serial.println("disturber (man-made spike rejected)");
    } else if (reason == 0x01) {
      Serial.println("noise level too high - raise NOISE_FLOOR or move the module");
#endif
    }
  }
  delay(10);
}
