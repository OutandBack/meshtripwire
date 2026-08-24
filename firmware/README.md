# ESP32 sniffer node (option 2)

A dedicated ESP32 that captures WiFi MACs in promiscuous mode and feeds them to
the tripwire, for coverage the base-station scanner can't reach. Publishes the
same `{"mac","from","rssi"}` JSON to `meshtastic/receive`, so the monitor needs
no changes.

## Flash it

1. Arduino IDE with the ESP32 board package (or PlatformIO with `platform = espressif32`).
2. Install the **PubSubClient** library (Nick O'Leary).
3. Edit the config block at the top of `esp32_sniffer/esp32_sniffer.ino`: WiFi
   credentials, `MQTT_HOST`/`MQTT_PORT`, `NODE_ID`, and `RSSI_MIN`.
4. Select your ESP32 board, flash, open Serial Monitor at 115200 to watch.

## Backhaul modes

- **WiFi → MQTT** (default, `OUTPUT_SERIAL 0`): sensor must be in WiFi range of
  the broker. Simplest. The single radio is shared between sniffing and the MQTT
  publish handshake, so capture is bursty — fine for presence detection.
- **Serial → Meshtastic → LoRa** (`OUTPUT_SERIAL 1`): the sketch prints one JSON
  line per sighting to Serial instead. Wire the ESP32 TX to the RX of a nearby
  Meshtastic node running the **Serial module** in `TEXTMSG`/`PROTO` mode; the
  node relays each line over LoRa to the base, where a serial/MQTT shim (adapt
  `mqtt/serial_bridge.py`) republishes to the broker. This is the off-grid path.

## Reality checks

- **MAC randomization**: modern phones rotate their WiFi MAC, so most sightings
  are ephemeral randoms — good for "someone is here", not for identifying who.
  Whitelist fixed-MAC gear only.
- **Airtime**: per-MAC sightings over LoRa add up fast. Keep `COOLDOWN_MS` high
  and `RSSI_MIN` tight so only close, persistent devices generate traffic.
- **Legality**: passively logging MAC addresses may be regulated where you are.
  Your property, your responsibility.
