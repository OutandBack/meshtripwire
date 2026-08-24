/*
 * Option 2: ESP32 WiFi/BLE sniffer node.
 *
 * A dedicated ESP32 (NOT the Meshtastic node) runs promiscuous WiFi capture and
 * publishes each observed source MAC as a sighting to the tripwire MQTT broker,
 * in the same {"mac","from","rssi"} JSON the monitor consumes. Deploy several
 * around the property for coverage the base-station scanner (option 1) can't reach.
 *
 * Backhaul: this sketch uses WiFi->MQTT (simplest when the sensor is in WiFi
 * range of the base station). For true off-grid LoRa backhaul, set OUTPUT_SERIAL
 * to 1 and wire TX to a nearby Meshtastic node running a serial-input module;
 * the node relays the line over LoRa. See firmware/README.md.
 *
 * Board: any ESP32. Libraries: PubSubClient (Nick O'Leary). Arduino core >= 2.x.
 * Note: promiscuous WiFi and the WiFi STA connection share one radio, so the
 * sketch hops between "connected to publish" and "sniffing" — fine for a tripwire,
 * not a full-airtime capture. For continuous capture, use OUTPUT_SERIAL instead.
 */
#include <WiFi.h>
#include <PubSubClient.h>
#include "esp_wifi.h"

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
const uint8_t  CHANNEL_MAX = 11;        // hop 1..CHANNEL_MAX (13/14 region-dependent)
// ----------------------------

WiFiClient net;
PubSubClient mqtt(net);

// Tiny fixed-size seen-cache for cooldown (ring buffer, no heap churn)
struct Seen { uint8_t mac[6]; uint32_t ts; };
static const int SEEN_N = 128;
Seen seen[SEEN_N];
int seenIdx = 0;

bool recently_seen(const uint8_t* mac) {
  uint32_t now = millis();
  for (int i = 0; i < SEEN_N; i++) {
    if (memcmp(seen[i].mac, mac, 6) == 0) {
      if (now - seen[i].ts < COOLDOWN_MS) return true;
      seen[i].ts = now;
      return false;
    }
  }
  memcpy(seen[seenIdx].mac, mac, 6);
  seen[seenIdx].ts = now;
  seenIdx = (seenIdx + 1) % SEEN_N;
  return false;
}

void report(const uint8_t* mac, int rssi) {
  if (rssi < RSSI_MIN) return;
  if (recently_seen(mac)) return;
  char macstr[18];
  snprintf(macstr, sizeof(macstr), "%02X:%02X:%02X:%02X:%02X:%02X",
           mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  char payload[96];
  snprintf(payload, sizeof(payload),
           "{\"mac\":\"%s\",\"from\":\"%s\",\"rssi\":%d}", macstr, NODE_ID, rssi);
#if OUTPUT_SERIAL
  Serial.println(payload);
#else
  if (mqtt.connected()) mqtt.publish(MQTT_TOPIC, payload);
#endif
}

// Promiscuous RX callback: pull the transmitter address (addr2) and RSSI.
void sniffer_cb(void* buf, wifi_promiscuous_pkt_type_t type) {
  if (type != WIFI_PKT_MGMT && type != WIFI_PKT_DATA) return;
  const wifi_promiscuous_pkt_t* p = (wifi_promiscuous_pkt_t*)buf;
  const uint8_t* payload = p->payload;
  // 802.11 addr2 (source) starts at byte 10 of the MAC header
  report(payload + 10, p->rx_ctrl.rssi);
}

void start_promiscuous() {
  wifi_promiscuous_filter_t filter = {
    .filter_mask = WIFI_PROMIS_FILTER_MASK_MGMT | WIFI_PROMIS_FILTER_MASK_DATA
  };
  esp_wifi_set_promiscuous_filter(&filter);
  esp_wifi_set_promiscuous_rx_cb(&sniffer_cb);
  esp_wifi_set_promiscuous(true);
}

void setup() {
  Serial.begin(115200);
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
}

uint32_t lastHop = 0, lastReconnect = 0;
uint8_t channel = 1;

void loop() {
#if !OUTPUT_SERIAL
  if (!mqtt.connected() && millis() - lastReconnect > 5000) {
    lastReconnect = millis();
    // Promiscuous must be off to use the STA link for the publish handshake
    esp_wifi_set_promiscuous(false);
    if (WiFi.status() != WL_CONNECTED) WiFi.reconnect();
    mqtt.connect(NODE_ID, MQTT_USER[0] ? MQTT_USER : nullptr, MQTT_PASS[0] ? MQTT_PASS : nullptr);
    start_promiscuous();
  }
  mqtt.loop();
#endif
  if (millis() - lastHop > 300) {   // channel hop so we don't miss other APs' clients
    lastHop = millis();
    channel = (channel % CHANNEL_MAX) + 1;
    esp_wifi_set_channel(channel, WIFI_SECOND_CHAN_NONE);
  }
}
