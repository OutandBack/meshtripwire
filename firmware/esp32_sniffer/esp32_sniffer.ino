/*
 * ESP32 WiFi/BLE sniffer node.
 *
 * A dedicated ESP32 (NOT the Meshtastic node) observes nearby device MACs and
 * publishes each as a sighting to the tripwire MQTT broker, in the same
 * {"mac","from","rssi"} JSON the monitor consumes. Deploy several around the
 * property for coverage the base-station scanner can't reach.
 *
 * One board sniffs ONE radio (set SCAN_MODE below):
 *   SCAN_WIFI - promiscuous 802.11: captures WiFi source MACs. Backhaul must be
 *               serial, or time-sliced MQTT (the single radio can't sniff and
 *               hold a WiFi station link at once).
 *   SCAN_BLE  - BLE advertising scan: captures BLE MACs. Coexists fine with a
 *               WiFi station link, so MQTT backhaul is clean here.
 * The C3's single radio can't do promiscuous WiFi and BLE simultaneously, so
 * pick one per board (e.g. some WiFi sniffers, some BLE sniffers).
 *
 * Backhaul (OUTPUT_SERIAL): 0 = WiFi->MQTT, 1 = print JSON to Serial for a wired
 * Meshtastic node to relay over LoRa. See firmware/README.md.
 *
 * Board: any ESP32 (ESP32-C3 SuperMini is the cheap default). BLE mode + the
 * built-in BLEDevice library needs an ESP32 with BLE (all current ESP32 variants).
 * Extra library: PubSubClient (Nick O'Leary). Arduino core >= 2.x.
 */

// ---- Radio: pick exactly one ----
#define SCAN_WIFI 1
#define SCAN_BLE  2
#define SCAN_MODE SCAN_WIFI
// ----------------------------------

// ---- Config: edit these ----
#define OUTPUT_SERIAL 0                 // 0 = publish over WiFi/MQTT, 1 = print JSON to Serial for LoRa backhaul
#define SERIAL_MESHCORE 0               // with OUTPUT_SERIAL 1: 0 = plain text lines (Meshtastic
                                        // Serial module), 1 = MeshCore companion-radio framing
#define DETECT_DRONEID 0                // 1 = also report drone Remote ID broadcasts (ASTM F3411 /
                                        // Open Drone ID): WiFi beacon vendor IE in SCAN_WIFI mode,
                                        // BLE service data 0xFFFA in SCAN_BLE mode
#define DETECT_ATTACKS 0                // 1 = report RF attacks (SCAN_WIFI mode): deauth floods,
                                        // rogue APs broadcasting PROTECT_SSID, and RF silence
#define DETECT_TRACKERS 0               // 1 = report BLE trackers (SCAN_BLE mode): Apple Find My
                                        // offline-finding, Tile, Samsung SmartTag advertisements
const uint8_t MESHCORE_CHANNEL = 0;     // channel index on the wired MeshCore companion node
const uint32_t DRONE_COOLDOWN_MS = 15000; // min gap between drone reports
                                          // ponytail: one global cooldown; per-drone if swarms matter

// Attack detection knobs (DETECT_ATTACKS). Tune DEAUTH_THRESHOLD to your RF
// environment: dense apartment WiFi sees benign deauths; a rural site sees none.
const char* PROTECT_SSID  = "";         // rogue-AP alarm: beacons carrying this SSID from a
const char* KNOWN_BSSIDS[] = { "" };    // BSSID not in this list are rogue. "" entries ignored.
const int      DEAUTH_THRESHOLD  = 20;  // deauth/disassoc frames per window that mean attack
const uint32_t DEAUTH_WINDOW_MS  = 10000;
const uint32_t SILENCE_SECONDS   = 120; // zero frames this long = jamming suspect (0 disables).
                                        // Caveat: with WiFi/MQTT backhaul a real jam also kills
                                        // the report path; the base watchdog is the backstop.
const uint32_t ATTACK_COOLDOWN_MS = 60000;
const char* WIFI_SSID   = "your-ssid";
const char* WIFI_PASS   = "your-pass";
const char* MQTT_HOST   = "192.168.1.10";
const uint16_t MQTT_PORT = 1883;
const char* MQTT_USER   = "";           // "" = anonymous
const char* MQTT_PASS   = "";
const char* MQTT_TOPIC  = "meshtastic/receive";
const char* NODE_ID     = "sensor-01";  // tag for MQTT sightings (serial/LoRa maps by relay node instead)
const int   RSSI_MIN    = -85;          // ignore weaker frames (tune per deployment)
const uint32_t COOLDOWN_MS = 60000;     // per-MAC re-publish suppression
const uint8_t  CHANNEL_MAX = 11;        // WiFi: hop 1..CHANNEL_MAX (13/14 region-dependent)

// Known MACs to IGNORE — only unknowns are reported, so LoRa airtime isn't spent
// on your own gear. Uppercase, colon-separated. Add cameras, laptops, sensors...
// ponytail: compile-time list; if it changes often, push it OTA instead of reflashing.
const char* WHITELIST[] = {
  "00:00:00:00:00:00",  // placeholder — replace with your fixed-MAC devices
};
const int WHITELIST_N = sizeof(WHITELIST) / sizeof(WHITELIST[0]);
// ----------------------------

#include <WiFi.h>
#include <PubSubClient.h>
#if SCAN_MODE == SCAN_WIFI
  #include "esp_wifi.h"
#else
  #include <BLEDevice.h>
  #include <BLEScan.h>
  #include <BLEAdvertisedDevice.h>
#endif

WiFiClient net;
PubSubClient mqtt(net);

// Fixed-size seen-cache for per-MAC cooldown (ring buffer, no heap churn).
struct Seen { char mac[18]; uint32_t ts; };
static const int SEEN_N = 128;
Seen seen[SEEN_N];
int seenIdx = 0;

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

bool recently_seen(const char* mac) {
  uint32_t now = millis();
  for (int i = 0; i < SEEN_N; i++) {
    if (strcmp(seen[i].mac, mac) == 0) {
      if (now - seen[i].ts < COOLDOWN_MS) return true;
      seen[i].ts = now;
      return false;
    }
  }
  strncpy(seen[seenIdx].mac, mac, sizeof(seen[seenIdx].mac) - 1);
  seen[seenIdx].ts = now;
  seenIdx = (seenIdx + 1) % SEEN_N;
  return false;
}

bool is_whitelisted(const char* mac) {
  for (int i = 0; i < WHITELIST_N; i++)
    if (strcmp(WHITELIST[i], mac) == 0) return true;
  return false;
}

#if DETECT_DRONEID || DETECT_ATTACKS || DETECT_TRACKERS
// Shared event reporter: compact "<kind>,<value>" over serial, JSON over MQTT.
void report_event(char kind, const char* name, const char* field, int value) {
#if OUTPUT_SERIAL
  char line[16];
  snprintf(line, sizeof(line), "%c,%d", kind, value);
  #if SERIAL_MESHCORE
    meshcore_send_line(line);
  #else
    Serial.println(line);
  #endif
#else
  char payload[96];
  snprintf(payload, sizeof(payload),
           "{\"event\":\"%s\",\"from\":\"%s\",\"%s\":%d}", name, NODE_ID, field, value);
  if (mqtt.connected()) mqtt.publish(MQTT_TOPIC, payload);
#endif
}

uint32_t lastFrameMs = 0;   // any frame/advertisement heard, for silence detection
#endif

#if DETECT_DRONEID
uint32_t lastDrone = 0;

// A drone's Remote ID is a mandated public broadcast; RSSI stands in for
// proximity. Serial extraction from the ODID message pack is the upgrade path.
void report_drone(int rssi) {
  if (millis() - lastDrone < DRONE_COOLDOWN_MS) return;
  lastDrone = millis();
  report_event('D', "drone", "rssi", rssi);
}
#endif

// mac must be an uppercase "AA:BB:CC:DD:EE:FF" string.
void report(const char* mac, int rssi) {
  if (rssi < RSSI_MIN) return;
  if (is_whitelisted(mac)) return;   // don't spend airtime on known gear
  if (recently_seen(mac)) return;
#if OUTPUT_SERIAL
  // Compact "AABBCC112233,-64" over LoRa: strip colons, drop the node id (the
  // relay node's own address identifies the sensor; serial_bridge maps it back).
  char line[24];
  char* p = line;
  for (const char* c = mac; *c; c++) if (*c != ':') *p++ = *c;
  p += snprintf(p, line + sizeof(line) - p, ",%d", rssi);
  #if SERIAL_MESHCORE
    meshcore_send_line(line);
  #else
    Serial.println(line);
  #endif
#else
  char payload[96];
  snprintf(payload, sizeof(payload),
           "{\"mac\":\"%s\",\"from\":\"%s\",\"rssi\":%d}", mac, NODE_ID, rssi);
  if (mqtt.connected()) mqtt.publish(MQTT_TOPIC, payload);
#endif
}

#if SCAN_MODE == SCAN_WIFI
// Promiscuous RX callback: pull the transmitter address (addr2) and RSSI.
void sniffer_cb(void* buf, wifi_promiscuous_pkt_type_t type) {
  if (type != WIFI_PKT_MGMT && type != WIFI_PKT_DATA) return;
  const wifi_promiscuous_pkt_t* p = (wifi_promiscuous_pkt_t*)buf;
  const uint8_t* m = p->payload + 10;  // 802.11 addr2 (source) at byte 10 of the header
  char mac[18];
  snprintf(mac, sizeof(mac), "%02X:%02X:%02X:%02X:%02X:%02X",
           m[0], m[1], m[2], m[3], m[4], m[5]);
  report(mac, p->rx_ctrl.rssi);

#if DETECT_DRONEID || DETECT_ATTACKS
  lastFrameMs = millis();
#endif

#if DETECT_ATTACKS
  // Deauth flood: burglars spray deauth/disassoc frames to blind WiFi cameras.
  static uint32_t deauthCount = 0, deauthWinStart = 0, lastDeauth = 0, lastRogue = 0;
  uint8_t fc0 = p->payload[0];
  if (type == WIFI_PKT_MGMT && (fc0 == 0xC0 || fc0 == 0xA0)) {
    uint32_t now = millis();
    if (now - deauthWinStart > DEAUTH_WINDOW_MS) { deauthWinStart = now; deauthCount = 0; }
    if (++deauthCount >= (uint32_t)DEAUTH_THRESHOLD && now - lastDeauth > ATTACK_COOLDOWN_MS) {
      lastDeauth = now;
      report_event('A', "deauth", "count", deauthCount);
      deauthCount = 0;
    }
  }
  // Rogue AP: a beacon carrying the protected SSID from an unknown BSSID
  if (PROTECT_SSID[0] && type == WIFI_PKT_MGMT && fc0 == 0x80 && p->rx_ctrl.sig_len > 38) {
    const uint8_t* ie = p->payload + 36;   // first IE of a beacon is the SSID
    size_t want = strlen(PROTECT_SSID);
    if (ie[0] == 0 && ie[1] == want && memcmp(ie + 2, PROTECT_SSID, want) == 0) {
      const uint8_t* b = p->payload + 16;  // addr3 = BSSID
      char bssid[18];
      snprintf(bssid, sizeof(bssid), "%02X:%02X:%02X:%02X:%02X:%02X",
               b[0], b[1], b[2], b[3], b[4], b[5]);
      bool known = false;
      for (unsigned i = 0; i < sizeof(KNOWN_BSSIDS) / sizeof(KNOWN_BSSIDS[0]); i++)
        if (KNOWN_BSSIDS[i][0] && strcasecmp(KNOWN_BSSIDS[i], bssid) == 0) known = true;
      if (!known && millis() - lastRogue > ATTACK_COOLDOWN_MS) {
        lastRogue = millis();
        report_event('R', "rogue_ap", "rssi", p->rx_ctrl.rssi);
      }
    }
  }
#endif

#if DETECT_DRONEID
  // Open Drone ID over WiFi rides beacon frames as a vendor-specific IE with
  // the ASD-STAN OUI FA:0B:BC. Walk the beacon's IEs (header 24 + fixed 12).
  if (type == WIFI_PKT_MGMT && p->payload[0] == 0x80 && p->rx_ctrl.sig_len > 38) {
    const uint8_t* ie = p->payload + 36;
    const uint8_t* end = p->payload + p->rx_ctrl.sig_len - 4;  // minus FCS
    while (ie + 2 <= end && ie + 2 + ie[1] <= end) {
      if (ie[0] == 221 && ie[1] >= 4 &&
          ie[2] == 0xFA && ie[3] == 0x0B && ie[4] == 0xBC) {
        report_drone(p->rx_ctrl.rssi);
        break;
      }
      ie += 2 + ie[1];
    }
  }
#endif
}

void start_promiscuous() {
  wifi_promiscuous_filter_t filter = {
    .filter_mask = WIFI_PROMIS_FILTER_MASK_MGMT | WIFI_PROMIS_FILTER_MASK_DATA
  };
  esp_wifi_set_promiscuous_filter(&filter);
  esp_wifi_set_promiscuous_rx_cb(&sniffer_cb);
  esp_wifi_set_promiscuous(true);
}
#else  // SCAN_BLE
class ScanCB : public BLEAdvertisedDeviceCallbacks {
  void onResult(BLEAdvertisedDevice dev) override {
    String mac = dev.getAddress().toString().c_str();
    mac.toUpperCase();
    report(mac.c_str(), dev.getRSSI());
#if DETECT_DRONEID || DETECT_ATTACKS || DETECT_TRACKERS
    lastFrameMs = millis();
#endif
#if DETECT_TRACKERS
    // BLE trackers left on the property = someone tagged a vehicle or is
    // staging theft. Caveat: any Find My device separated from its owner
    // advertises the same way; expect some benign hits.
    static uint32_t lastTracker = 0;
    bool tracker = false;
    if (dev.haveManufacturerData()) {
      String md = dev.getManufacturerData();
      if (md.length() >= 4 && (uint8_t)md[0] == 0x4C && (uint8_t)md[1] == 0x00 &&
          (uint8_t)md[2] == 0x12 && (uint8_t)md[3] == 0x19)
        tracker = true;                                  // Apple Find My offline-finding
    }
    if (!tracker && dev.isAdvertisingService(BLEUUID((uint16_t)0xFEED)))
      tracker = true;                                    // Tile
    if (!tracker && dev.haveServiceData() &&
        dev.getServiceDataUUID().equals(BLEUUID((uint16_t)0xFD5A)))
      tracker = true;                                    // Samsung SmartTag
    if (tracker && millis() - lastTracker > ATTACK_COOLDOWN_MS) {
      lastTracker = millis();
      report_event('T', "tracker", "rssi", dev.getRSSI());
    }
#endif
#if DETECT_DRONEID
    // Open Drone ID over BLE: service data on the ASTM 16-bit UUID 0xFFFA
    if (dev.haveServiceData() &&
        dev.getServiceDataUUID().equals(BLEUUID((uint16_t)0xFFFA))) {
      report_drone(dev.getRSSI());
    }
#endif
  }
};
#endif

void mqtt_connect() {
  mqtt.connect(NODE_ID, MQTT_USER[0] ? MQTT_USER : nullptr, MQTT_PASS[0] ? MQTT_PASS : nullptr);
}

void setup() {
  Serial.begin(115200);
#if OUTPUT_SERIAL && SERIAL_MESHCORE
  meshcore_appstart();
#endif

#if SCAN_MODE == SCAN_WIFI
  #if OUTPUT_SERIAL
    WiFi.mode(WIFI_MODE_NULL);
    esp_wifi_start();
  #else
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) delay(250);
    mqtt.setServer(MQTT_HOST, MQTT_PORT);
  #endif
  start_promiscuous();
#else  // SCAN_BLE
  #if !OUTPUT_SERIAL
    // BLE scan and a WiFi station link coexist fine on one radio.
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) delay(250);
    mqtt.setServer(MQTT_HOST, MQTT_PORT);
    mqtt_connect();
  #endif
  BLEDevice::init("");
  BLEScan* scan = BLEDevice::getScan();
  scan->setAdvertisedDeviceCallbacks(new ScanCB());
  scan->setActiveScan(false);  // passive: don't probe, just listen (lower power, less noise)
  scan->setInterval(160);
  scan->setWindow(160);        // ~100% duty within the interval
  scan->start(0, nullptr, false);  // 0 = scan forever, results via callback
#endif
}

uint32_t lastHop = 0, lastReconnect = 0;
uint8_t channel = 1;

void loop() {
#if OUTPUT_SERIAL && SERIAL_MESHCORE
  while (Serial.available()) Serial.read();  // drain companion-radio responses
#endif
#if !OUTPUT_SERIAL
  if (!mqtt.connected() && millis() - lastReconnect > 5000) {
    lastReconnect = millis();
    if (WiFi.status() != WL_CONNECTED) WiFi.reconnect();
  #if SCAN_MODE == SCAN_WIFI
    // Promiscuous must be off to use the STA link for the publish handshake.
    esp_wifi_set_promiscuous(false);
    mqtt_connect();
    start_promiscuous();
  #else
    mqtt_connect();
  #endif
  }
  mqtt.loop();
#endif

#if DETECT_ATTACKS
  // RF silence: a healthy sniffer always hears something; total quiet is
  // a jamming suspect. Report once per episode, re-armed when frames return.
  static uint32_t lastSilence = 0;
  if (SILENCE_SECONDS && lastFrameMs &&
      millis() - lastFrameMs > SILENCE_SECONDS * 1000UL &&
      millis() - lastSilence > ATTACK_COOLDOWN_MS) {
    lastSilence = millis();
    report_event('Q', "silence", "seconds", (millis() - lastFrameMs) / 1000);
  }
#endif

#if SCAN_MODE == SCAN_WIFI
  if (millis() - lastHop > 300) {   // channel hop so we don't miss other APs' clients
    lastHop = millis();
    channel = (channel % CHANNEL_MAX) + 1;
    esp_wifi_set_channel(channel, WIFI_SECOND_CHAN_NONE);
  }
#else
  delay(10);  // BLE scanning runs in the background; nothing to poll
#endif
}
