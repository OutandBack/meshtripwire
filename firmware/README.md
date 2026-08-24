# ESP32 sniffer node

A dedicated ESP32 that captures WiFi MACs in promiscuous mode and feeds them to
the tripwire, for coverage the base-station scanner can't reach. Publishes the
same `{"mac","from","rssi"}` JSON to `meshtastic/receive`, so the monitor needs
no changes.

## Flash it

1. Arduino IDE with the ESP32 board package (or PlatformIO with `platform = espressif32`).
2. Install the **PubSubClient** library (Nick O'Leary). BLE mode also uses the
   ESP32 core's built-in `BLEDevice` library (no separate install).
3. Pick the radio with `SCAN_MODE` (`SCAN_WIFI` or `SCAN_BLE`), then edit the
   config block: WiFi credentials, `MQTT_HOST`/`MQTT_PORT`, `NODE_ID`, `RSSI_MIN`.
4. Select your ESP32 board, flash, open Serial Monitor at 115200 to watch.

## WiFi vs BLE mode

One board sniffs one radio — the ESP32-C3's single radio can't do promiscuous
WiFi and BLE at the same time. Deploy a mix: `SCAN_WIFI` nodes catch phones/APs
on 2.4 GHz WiFi, `SCAN_BLE` nodes catch earbuds, watches, tags, and phones
advertising over BLE. BLE mode coexists cleanly with the WiFi/MQTT uplink; WiFi
mode has to time-slice sniffing against the uplink, so serial→LoRa backhaul is
smoother there.

## Backhaul modes

- **WiFi → MQTT** (default, `OUTPUT_SERIAL 0`): sensor must be in WiFi range of
  the broker. Simplest. The single radio is shared between sniffing and the MQTT
  publish handshake, so capture is bursty — fine for presence detection.
- **Serial → LoRa mesh** (`OUTPUT_SERIAL 1`): the sketch prints one JSON line per
  sighting to Serial instead. Wire the ESP32 TX to the RX of a nearby LoRa-mesh
  node — Meshtastic (Serial module, `TEXTMSG`/`PROTO` mode), MeshCore, or a
  Reticulum interface — which relays each line over LoRa to the base. There,
  `mqtt/serial_bridge.py` receives the text message and republishes the JSON to
  the broker (it handles this automatically alongside node-presence detection).
  This is the off-grid path; the mesh stack is your choice.

## Conserving LoRa bandwidth

LoRa is a few kbps with legal duty-cycle limits, so the serial→LoRa path is
tuned to send as little as possible. Three measures, all on by default:

- **Compact wire format**: over serial the sketch emits `AABBCC112233,-64`
  (~16 bytes) instead of JSON (~56). `serial_bridge.py` expands it back.
- **On-device whitelist**: edit `WHITELIST[]` in the sketch with your known-good
  MACs; those are never transmitted, so airtime is spent only on unknowns.
- **Sensor id dropped on the wire**: the relaying mesh node's own address already
  identifies the sensor. Map node id → friendly name at the base with
  `serial_bridge --sensor-map` (see `config/sensor_nodes.json.example`).

Together these cut traffic roughly 10–20×. If you still saturate the channel:
raise `COOLDOWN_MS`, tighten `RSSI_MIN`, or use a faster Meshtastic modem preset
(ShortFast) if range allows. For the absolute minimum, a binary payload (6-byte
MAC + 1-byte RSSI ≈ 8 bytes) via the Serial module's PROTO mode would shave more,
at the cost of decoding on both ends — not implemented here.

## Reality checks

- **MAC randomization**: modern phones rotate their WiFi MAC, so most sightings
  are ephemeral randoms — good for "someone is here", not for identifying who.
  Whitelist fixed-MAC gear only.
- **Airtime**: per-MAC sightings over LoRa add up fast. Keep `COOLDOWN_MS` high
  and `RSSI_MIN` tight so only close, persistent devices generate traffic.
- **Legality**: passively logging MAC addresses may be regulated where you are.
  Your property, your responsibility.
