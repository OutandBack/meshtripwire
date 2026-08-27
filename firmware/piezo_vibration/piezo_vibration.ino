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
#define DEBUG_PRINT   1                 // 1 = print envelope/hits 1/s for calibration.
                                        // Ignored when OUTPUT_SERIAL=1: a wired Meshtastic
                                        // node would relay every debug line over LoRa.
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
const uint32_t COOLDOWN_MS     = 15000; // one event per episode (monitor adds per-type cooldowns)
// ----------------------------

#if !OUTPUT_SERIAL
  #include <WiFi.h>
  #include <PubSubClient.h>
  WiFiClient net;
  PubSubClient mqtt(net);
#endif

void report(char kind, int value) {
#if OUTPUT_SERIAL
  Serial.print(kind);
  Serial.print(',');
  Serial.println(value);
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

int hits_in_window(uint32_t now) {
  int n = 0;
  for (int i = 0; i < HITS_N; i++)
    if (hitTimes[i] && now - hitTimes[i] < WINDOW_MS) n++;
  return n;
}

void loop() {
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
    Serial.printf("env_max=%d baseline=%.0f hits_in_window=%d\n",
                  dbgMax, baseline, hits_in_window(now));
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
    if (env > pendingPeak) pendingPeak = env;
    int hits = hits_in_window(now);
    if (hits >= SHAKE_HITS && now - lastEvent > COOLDOWN_MS) {
      lastEvent = now;
      report('S', hits);
      pending = false;
      pendingPeak = 0;
      for (int i = 0; i < HITS_N; i++) hitTimes[i] = 0;  // one climb = one event
    }
  } else if (pending && now - lastHit > QUIET_MS) {
    // Burst ended below the shake bar: it was a knock/brief impact.
    if (now - lastEvent > COOLDOWN_MS) {
      lastEvent = now;
      report('K', pendingPeak);
    }
    pending = false;
    pendingPeak = 0;
  }
  delayMicroseconds(500);               // ~2 kHz sampling
}
