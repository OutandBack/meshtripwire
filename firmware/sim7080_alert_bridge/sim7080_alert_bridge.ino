/*
 * Cellular alert bridge: LilyGO T-SIM7080G-S3 (ESP32-S3 + SIM7080G LTE-M/NB-IoT).
 *
 * "RelayFabric, but cellular." For an off-grid base station with cell coverage
 * but no Internet: this board joins the base LAN over WiFi, subscribes to the
 * monitor's MQTT alert topic (set [Notifications] EnableMqtt = true, default
 * topic meshtripwire/alerts), and pushes each alert OUT over LTE:
 *   - SMS via the SIM7080G (default; no data plan needed, text lands on a phone)
 *   - optional HTTPS POST to a webhook over cellular data (CELL_WEBHOOK 1) --
 *     point it at a signal-cli / ntfy / Telegram relay to reach those.
 *
 * No base-station code changes: it consumes the existing EnableMqtt channel.
 * The Pi keeps all the smarts (correlation, arming, history); this only carries
 * the finished alert off-grid. Alerts arrive as JSON {"mac","node","ts",
 * "message"}; the human-readable "message" is what gets sent.
 */

// ---- Config: edit these ----
// Base LAN (to read the alert broker) -- same WiFi the sniffer nodes use.
const char* WIFI_SSID   = "your-ssid";
const char* WIFI_PASS   = "your-pass";
const char* MQTT_HOST   = "192.168.1.10";   // the Pi base station on the LAN
const uint16_t MQTT_PORT = 1883;
const char* MQTT_USER   = "";               // "" = anonymous
const char* MQTT_PASS   = "";
const char* ALERT_TOPIC = "meshtripwire/alerts";  // [Notifications] MqttAlertTopic
const char* CLIENT_ID   = "cell-bridge";
#define DEBUG_PRINT 1                        // 1 = narrate to USB Serial for bring-up
// ----------------------------

// ---- Cellular (LilyGO T-SIM7080G-S3) -- copy this whole block to add cellular
//      egress to any sensor sketch; only report()/the call site changes. ----
#define TINY_GSM_MODEM_SIM7080
#include <TinyGsmClient.h>
// Board pins from Xinyuan-LilyGO/LilyGo-T-SIM7080G (examples/utilities.h).
// VERIFY against your board revision: LilyGO's wiki quick-start lists PWRKEY on
// GPIO12 for an older/other revision; the ESP32-S3 repo uses GPIO41 (below).
const int      MODEM_PWR_PIN = 41;   // PWRKEY: pulse to power the modem on
const int      MODEM_DTR_PIN = 42;
const int      MODEM_RX_PIN  = 4;    // ESP32 RX  <- modem TX
const int      MODEM_TX_PIN  = 5;    // ESP32 TX  -> modem RX
const uint32_t MODEM_BAUD    = 115200;
const char*    CELL_APN      = "hologram";   // your SIM's APN (SMS needs none; POST does)
const char*    CELL_USER     = "";
const char*    CELL_PASS     = "";
const char*    SMS_TO        = "+15551234567"; // number to text; "" disables SMS
#define CELL_WEBHOOK 0               // 1 = ALSO POST each alert over LTE data
#define WEBHOOK_TLS  1               // 1 = HTTPS (port 443). TLS on the modem is the
                                     // one bit that needs on-hardware tuning.
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

// Bring the modem up far enough to send SMS (network registration). Data (GPRS)
// is attached lazily in cell_post(), only when the webhook path is compiled in.
void cell_setup() {
  pinMode(MODEM_DTR_PIN, OUTPUT);
  digitalWrite(MODEM_DTR_PIN, LOW);  // keep modem awake
  SerialAT.begin(MODEM_BAUD, SERIAL_8N1, MODEM_RX_PIN, MODEM_TX_PIN);
  modemPowerOn();
  delay(3000);                       // datasheet: modem needs a few seconds post-PWRKEY
  modem.restart();
  modem.waitForNetwork(60000L);      // registration; SMS works once registered
}

#if CELL_WEBHOOK
// Minimal JSON string escape for the POST body (message text is plain, but a
// stray quote/backslash must not break the JSON).
String json_escape(const char* s) {
  String o;
  for (const char* p = s; *p; p++) {
    if (*p == '"' || *p == '\\') o += '\\';
    o += *p;
  }
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

// The one egress entry point. Copy this + the block above into a sensor sketch
// and call cell_egress(<alert text>) in place of the MQTT publish.
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

#include <WiFi.h>
#include <PubSubClient.h>
WiFiClient net;
PubSubClient mqtt(net);

// Forward the alert's "message" field over cellular; fall back to raw payload.
void on_alert(char* topic, byte* payload, unsigned int len) {
  String raw; raw.reserve(len);
  for (unsigned int i = 0; i < len; i++) raw += (char)payload[i];
  int k = raw.indexOf("\"message\":\"");
  if (k >= 0) {
    int start = k + 11, end = raw.indexOf('"', start);
    if (end > start) { cell_egress(raw.substring(start, end).c_str()); return; }
  }
  cell_egress(raw.c_str());  // not our schema -- send it whole
}

void mqtt_reconnect() {
  if (mqtt.connected()) return;
  if (mqtt.connect(CLIENT_ID, MQTT_USER[0] ? MQTT_USER : nullptr, MQTT_PASS[0] ? MQTT_PASS : nullptr))
    mqtt.subscribe(ALERT_TOPIC, 1);   // QoS 1: the monitor publishes alerts at QoS 1
}

void setup() {
  Serial.begin(115200);
  cell_setup();
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) delay(250);
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(on_alert);
  mqtt_reconnect();
#if DEBUG_PRINT
  Serial.printf("bridge: wifi=%d net=%d -> SMS %s\n",
                WiFi.status() == WL_CONNECTED, modem.isNetworkConnected(), SMS_TO);
#endif
}

uint32_t lastReconnect = 0;

void loop() {
  if (!mqtt.connected() && millis() - lastReconnect > 5000) {
    lastReconnect = millis();
    if (WiFi.status() != WL_CONNECTED) WiFi.reconnect();
    mqtt_reconnect();
  }
  mqtt.loop();
  delay(10);
}
