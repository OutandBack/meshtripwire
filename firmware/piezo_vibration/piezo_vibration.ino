/*
 * ESP32 vibration node: piezo disc on a door, gate, or fence run.
 *
 * A piezo disc glued to the surface produces voltage spikes on impact. The node
 * samples the envelope and classifies on-device; only events leave the node:
 *
 *   knock - one or a few impacts then quiet (door knock, thrown rock)
 *   shake - repeated impacts inside a rolling window (climbing, fence shaking)
 *   wind  - sustained LOW-amplitude noise stays below SPIKE_THRESHOLD: no event.
 *           The threshold is the wind filter; tune it against your actual fence.
 *
 * Backhaul (OUTPUT_SERIAL), same pattern as the other sensor nodes:
 *   0 = publish {"event":"knock","from":NODE_ID,"peak":N} /
 *               {"event":"shake","from":NODE_ID,"hits":N} over WiFi/MQTT
 *   1 = print compact "K,<peak>" / "S,<hits>" to Serial for a wired Meshtastic
 *       node to relay over LoRa (serial_bridge maps relay node -> sensor name)
 *
 * Wiring: piezo disc between PIEZO_PIN and GND, with a 1 Mohm resistor in
 * parallel to bleed charge. The ESP32's internal ESD diodes clip the signal to
 * a safe range at knock energies; for large discs on hard-struck surfaces add a
 * 3.3V zener across the disc. Mount the disc rigidly (epoxy or a screw clamp);
 * loose mounting reads as noise.
 */

// ---- Config: edit these ----
#define OUTPUT_SERIAL 0                 // 0 = WiFi/MQTT, 1 = Serial lines for LoRa backhaul
#define SERIAL_MESHCORE 0               // with OUTPUT_SERIAL 1: 0 = plain text lines (Meshtastic
                                        // Serial module), 1 = MeshCore companion-radio framing
#define DEBUG_PRINT   1                 // 1 = print envelope/hits 1/s for calibration.
                                        // Ignored when OUTPUT_SERIAL=1: a wired Meshtastic
                                        // node would relay every debug line over LoRa.
const uint8_t MESHCORE_CHANNEL = 0;     // channel index on the wired MeshCore companion node
const int   PIEZO_PIN   = 3;            // ADC-capable GPIO
const char* WIFI_SSID   = "your-ssid";
const char* WIFI_PASS   = "your-pass";
const char* MQTT_HOST   = "192.168.1.10";
const uint16_t MQTT_PORT = 1883;
const char* MQTT_USER   = "";           // "" = anonymous
const char* MQTT_PASS   = "";
const char* MQTT_TOPIC  = "meshtastic/receive";
const char* NODE_ID     = "fence-e";    // tag for MQTT events (serial/LoRa maps by relay node instead)

// Calibration knobs — every disc, mount, and fence rings differently. Flash with
// DEBUG_PRINT on, knock and shake the thing for real, then set SPIKE_THRESHOLD
// above the loudest wind reading you observe and below your softest test knock.
const int      SPIKE_THRESHOLD = 400;   // envelope (12-bit ADC counts) that counts as a hit
const uint32_t HIT_GAP_MS      = 150;   // ringing within this gap is one hit, not several
const uint32_t WINDOW_MS       = 5000;  // rolling window for the shake decision
const int      SHAKE_HITS      = 4;     // hits inside WINDOW_MS that mean climbing/shaking
const uint32_t QUIET_MS        = 1500;  // silence after hits that closes a knock event
const int      GLASS_MIN_SAMPLES = 80;  // over-threshold samples in one burst that mean glass:
                                        // a knock is one impulse that decays (few samples); a
                                        // shatter rings densely for 100-300 ms (many). Calibrate
                                        // with DEBUG_PRINT: tap vs. break a jar, read ring=
const uint32_t COOLDOWN_MS     = 15000; // one event per episode (monitor adds per-type cooldowns)
// ----------------------------

#if !OUTPUT_SERIAL
  #include <WiFi.h>
  #include <PubSubClient.h>
  WiFiClient net;
  PubSubClient mqtt(net);
#endif

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

void report(char kind, int value) {
#if OUTPUT_SERIAL
  char line[16];
  snprintf(line, sizeof(line), "%c,%d", kind, value);
  #if SERIAL_MESHCORE
    meshcore_send_line(line);
  #else
    Serial.println(line);
  #endif
#else
  char payload[80];
  snprintf(payload, sizeof(payload),
           "{\"event\":\"%s\",\"from\":\"%s\",\"%s\":%d}",
           kind == 'K' ? "knock" : "shake", NODE_ID,
           kind == 'K' ? "peak" : "hits", value);
  if (mqtt.connected()) mqtt.publish(MQTT_TOPIC, payload);
#endif
}

void setup() {
  Serial.begin(115200);
#if OUTPUT_SERIAL && SERIAL_MESHCORE
  meshcore_appstart();
#endif
  analogReadResolution(12);
#if !OUTPUT_SERIAL
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) delay(250);
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.connect(NODE_ID, MQTT_USER[0] ? MQTT_USER : nullptr, MQTT_PASS[0] ? MQTT_PASS : nullptr);
#endif
}

float baseline = 0;                     // slow DC offset of the ADC
static const int HITS_N = 16;           // ring of recent hit timestamps
uint32_t hitTimes[HITS_N];
int hitIdx = 0;
uint32_t lastHit = 0, lastEvent = 0, lastReconnect = 0;
bool pending = false;                   // a burst is open, waiting for quiet or shake
int pendingPeak = 0;
int burstSamples = 0;                   // over-threshold samples this burst (ring density)

int hits_in_window(uint32_t now) {
  int n = 0;
  for (int i = 0; i < HITS_N; i++)
    if (hitTimes[i] && now - hitTimes[i] < WINDOW_MS) n++;
  return n;
}

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

  uint32_t now = millis();
  int raw = analogRead(PIEZO_PIN);
  if (baseline == 0) baseline = raw;
  baseline += 0.001f * (raw - baseline);  // spikes are brief; DC drift is slow
  int env = abs(raw - (int)baseline);

#if DEBUG_PRINT && !OUTPUT_SERIAL
  static uint32_t lastDbg = 0;
  static int dbgMax = 0;
  if (env > dbgMax) dbgMax = env;
  if (now - lastDbg > 1000) {
    lastDbg = now;
    Serial.printf("env_max=%d baseline=%.0f hits_in_window=%d ring=%d\n",
                  dbgMax, baseline, hits_in_window(now), burstSamples);
    dbgMax = 0;
  }
#endif

  if (env > SPIKE_THRESHOLD) {
    if (now - lastHit > HIT_GAP_MS) {   // new hit, not ringing from the last one
      hitTimes[hitIdx] = now;
      hitIdx = (hitIdx + 1) % HITS_N;
    }
    lastHit = now;
    pending = true;
    burstSamples++;
    if (env > pendingPeak) pendingPeak = env;
    int hits = hits_in_window(now);
    if (hits >= SHAKE_HITS && now - lastEvent > COOLDOWN_MS) {
      lastEvent = now;
      report('S', hits);
      pending = false;
      pendingPeak = 0;
      burstSamples = 0;
      for (int i = 0; i < HITS_N; i++) hitTimes[i] = 0;  // one climb = one event
    }
  } else if (pending && now - lastHit > QUIET_MS) {
    // Burst ended below the shake bar: dense ringing is glass, else a knock.
    if (now - lastEvent > COOLDOWN_MS) {
      lastEvent = now;
      report(burstSamples >= GLASS_MIN_SAMPLES ? 'G' : 'K', pendingPeak);
    }
    pending = false;
    pendingPeak = 0;
    burstSamples = 0;
  }
  delayMicroseconds(500);               // ~2 kHz sampling
}
