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
const char* WIFI_SSID   = "your-ssid";
const char* WIFI_PASS   = "your-pass";
const char* MQTT_HOST   = "192.168.1.10";
const uint16_t MQTT_PORT = 1883;
const char* MQTT_USER   = "";           // "" = anonymous
const char* MQTT_PASS   = "";
const char* MQTT_TOPIC  = "meshtastic/receive";
const char* NODE_ID     = "sensor-01";  // tag for sightings from this node
const int   RSSI_MIN    = -85;          // ignore weaker frames (tune per deployment)
const uint32_t COOLDOWN_MS = 60000;     // per-MAC re-publish suppression
const uint8_t  CHANNEL_MAX = 11;        // WiFi: hop 1..CHANNEL_MAX (13/14 region-dependent)
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

// mac must be an uppercase "AA:BB:CC:DD:EE:FF" string.
void report(const char* mac, int rssi) {
  if (rssi < RSSI_MIN) return;
  if (recently_seen(mac)) return;
  char payload[96];
  snprintf(payload, sizeof(payload),
           "{\"mac\":\"%s\",\"from\":\"%s\",\"rssi\":%d}", mac, NODE_ID, rssi);
#if OUTPUT_SERIAL
  Serial.println(payload);
#else
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
  }
};
#endif

void mqtt_connect() {
  mqtt.connect(NODE_ID, MQTT_USER[0] ? MQTT_USER : nullptr, MQTT_PASS[0] ? MQTT_PASS : nullptr);
}

void setup() {
  Serial.begin(115200);

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
