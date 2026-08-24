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
  Reticulum interface — which relays each line over LoRa to the base, where a
  serial/MQTT shim (adapt `mqtt/serial_bridge.py`) republishes to the broker.
  This is the off-grid path; the mesh stack is your choice.

## Reality checks

- **MAC randomization**: modern phones rotate their WiFi MAC, so most sightings
  are ephemeral randoms — good for "someone is here", not for identifying who.
  Whitelist fixed-MAC gear only.
- **Airtime**: per-MAC sightings over LoRa add up fast. Keep `COOLDOWN_MS` high
  and `RSSI_MIN` tight so only close, persistent devices generate traffic.
- **Legality**: passively logging MAC addresses may be regulated where you are.
  Your property, your responsibility.
