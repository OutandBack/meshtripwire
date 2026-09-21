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
#define BACKHAUL_CELL 0                 // with OUTPUT_SERIAL 0: 1 = solo cellular tripwire on a
                                        // LilyGO T-SIM7080G-S3 -- classify on-device, SMS the event
                                        // over LTE. No WiFi/MQTT/broker/Pi. Config in the cell block
                                        // below. Same block copies into any other sensor sketch.
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
const char* HEARTBEAT_TOPIC = "meshtripwire/heartbeat"; // liveness so a quiet node stays healthy
const uint32_t HEARTBEAT_MS = 60000;    // heartbeat interval (WiFi/MQTT mode only)

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

#if !OUTPUT_SERIAL && BACKHAUL_CELL
// ---- Cellular (LilyGO T-SIM7080G-S3) -- copy this whole block to add cellular
//      egress to any sensor sketch; only the report() call site changes. ----
#define TINY_GSM_MODEM_SIM7080
#include <TinyGsmClient.h>
// Board pins from Xinyuan-LilyGO/LilyGo-T-SIM7080G (examples/utilities.h).
// VERIFY against your board revision: LilyGO's wiki lists PWRKEY on GPIO12 for
// an older/other revision; the ESP32-S3 repo uses GPIO41 (below).
const int      MODEM_PWR_PIN = 41;   // PWRKEY: pulse to power the modem on
const int      MODEM_DTR_PIN = 42;
const int      MODEM_RX_PIN  = 4;    // ESP32 RX  <- modem TX
const int      MODEM_TX_PIN  = 5;    // ESP32 TX  -> modem RX
const uint32_t MODEM_BAUD    = 115200;
const char*    CELL_APN      = "hologram";     // your SIM's APN (SMS needs none; POST does)
const char*    CELL_USER     = "";
const char*    CELL_PASS     = "";
const char*    SMS_TO        = "+15551234567"; // number to text; "" disables SMS
#define CELL_WEBHOOK 0               // 1 = ALSO POST each event over LTE data
#define WEBHOOK_TLS  1               // 1 = HTTPS (443). TLS on the modem needs on-hardware tuning.
const char*    WEBHOOK_HOST  = "example.com";
const uint16_t WEBHOOK_PORT  = 443;
const char*    WEBHOOK_PATH  = "/hook";

HardwareSerial SerialAT(1);
TinyGsm modem(SerialAT);
#if CELL_WEBHOOK
  #if WEBHOOK_TLS
    TinyGsmClientSecure webClient(modem);
  #else
    TinyGsmClient webClient(modem);
  #endif
#endif

void modemPowerOn() {
  pinMode(MODEM_PWR_PIN, OUTPUT);
  digitalWrite(MODEM_PWR_PIN, LOW);  delay(100);
  digitalWrite(MODEM_PWR_PIN, HIGH); delay(1000);   // >1s pulse latches power on
  digitalWrite(MODEM_PWR_PIN, LOW);
}

void cell_setup() {
  pinMode(MODEM_DTR_PIN, OUTPUT);
  digitalWrite(MODEM_DTR_PIN, LOW);  // keep modem awake
  SerialAT.begin(MODEM_BAUD, SERIAL_8N1, MODEM_RX_PIN, MODEM_TX_PIN);
  modemPowerOn();
  delay(3000);                       // modem needs a few seconds after PWRKEY
  modem.restart();
  modem.waitForNetwork(60000L);      // registration; SMS works once registered
}

#if CELL_WEBHOOK
String json_escape(const char* s) {
  String o;
  for (const char* p = s; *p; p++) { if (*p == '"' || *p == '\\') o += '\\'; o += *p; }
  return o;
}
void cell_post(const char* text) {
  if (!modem.isGprsConnected() && !modem.gprsConnect(CELL_APN, CELL_USER, CELL_PASS)) return;
  if (!webClient.connect(WEBHOOK_HOST, WEBHOOK_PORT)) return;
  String body = String("{\"text\":\"") + json_escape(text) + "\"}";
  webClient.print(String("POST ") + WEBHOOK_PATH + " HTTP/1.1\r\n");
  webClient.print(String("Host: ") + WEBHOOK_HOST + "\r\n");
  webClient.print("Content-Type: application/json\r\n");
  webClient.print(String("Content-Length: ") + body.length() + "\r\n");
  webClient.print("Connection: close\r\n\r\n");
  webClient.print(body);
  uint32_t t0 = millis();
  while (webClient.connected() && millis() - t0 < 10000) { while (webClient.available()) webClient.read(); }
  webClient.stop();
}
#endif

void cell_egress(const char* text) {
  if (SMS_TO[0]) modem.sendSMS(SMS_TO, text);
#if CELL_WEBHOOK
  cell_post(text);
#endif
#if DEBUG_PRINT
  Serial.printf("egress: %s\n", text);
#endif
}
// ---- end cellular block ----
#elif !OUTPUT_SERIAL
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
#elif BACKHAUL_CELL
  // Solo cellular tripwire: text the classified event straight out over LTE.
  const char* event = kind == 'K' ? "knock" : kind == 'G' ? "glass" : "shake";
  const char* field = kind == 'S' ? "hits" : "peak";
  char text[80];
  snprintf(text, sizeof(text), "meshtripwire %s: %s %s=%d", NODE_ID, event, field, value);
  cell_egress(text);
#else
  // Explicit per-kind mapping so it matches the compact-line/registry contract:
  // K=knock/peak, G=glass/peak, S=shake/hits. (A K-vs-else test would miss glass.)
  const char* event = kind == 'K' ? "knock" : kind == 'G' ? "glass" : "shake";
  const char* field = kind == 'S' ? "hits" : "peak";
  char payload[80];
  snprintf(payload, sizeof(payload),
           "{\"event\":\"%s\",\"from\":\"%s\",\"%s\":%d}", event, NODE_ID, field, value);
  if (mqtt.connected()) mqtt.publish(MQTT_TOPIC, payload);
#endif
}

void setup() {
  Serial.begin(115200);
#if OUTPUT_SERIAL && SERIAL_MESHCORE
  meshcore_appstart();
#endif
  analogReadResolution(12);
#if !OUTPUT_SERIAL && BACKHAUL_CELL
  cell_setup();
#elif !OUTPUT_SERIAL
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
#if !OUTPUT_SERIAL && !BACKHAUL_CELL
  if (!mqtt.connected() && millis() - lastReconnect > 5000) {
    lastReconnect = millis();
    if (WiFi.status() != WL_CONNECTED) WiFi.reconnect();
    mqtt.connect(NODE_ID, MQTT_USER[0] ? MQTT_USER : nullptr, MQTT_PASS[0] ? MQTT_PASS : nullptr);
  }
  mqtt.loop();
  // Heartbeat so a working-but-quiet sensor still satisfies the base watchdog.
  static uint32_t lastHb = 0;
  if (mqtt.connected() && (!lastHb || millis() - lastHb > HEARTBEAT_MS)) {
    lastHb = millis();
    char hb[48];
    snprintf(hb, sizeof(hb), "{\"node\":\"%s\"}", NODE_ID);
    mqtt.publish(HEARTBEAT_TOPIC, hb);
  }
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
    if (hits >= SHAKE_HITS && (!lastEvent || now - lastEvent > COOLDOWN_MS)) {
      lastEvent = now;
      report('S', hits);
      pending = false;
      pendingPeak = 0;
      burstSamples = 0;
      for (int i = 0; i < HITS_N; i++) hitTimes[i] = 0;  // one climb = one event
    }
  } else if (pending && now - lastHit > QUIET_MS) {
    // Burst ended below the shake bar: dense ringing is glass, else a knock.
    if (!lastEvent || now - lastEvent > COOLDOWN_MS) {  // 0 = never fired
      lastEvent = now;
      report(burstSamples >= GLASS_MIN_SAMPLES ? 'G' : 'K', pendingPeak);
    }
    pending = false;
    pendingPeak = 0;
    burstSamples = 0;
  }
  delayMicroseconds(500);               // ~2 kHz sampling
}
