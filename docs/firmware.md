# Firmware

Four Arduino sketches under `firmware/`, all sharing the same conventions:
a config block at the top of the file, an `OUTPUT_SERIAL` switch between
WiFi/MQTT and serial→LoRa backhaul, and a `DEBUG_PRINT` calibration readout
(automatically compiled out in serial mode so debug lines can never leak onto
the mesh).

## Flashing

1. Arduino IDE with the ESP32 board package (or PlatformIO with
   `platform = espressif32`), plus the **PubSubClient** library. BLE mode uses
   the core's built-in `BLEDevice`; no extra install.
2. Edit the sketch's config block: WiFi credentials, `MQTT_HOST`/`MQTT_PORT`,
   a unique `NODE_ID`, and the sensor's calibration knobs.
3. On an ESP32-C3 SuperMini: hold **BOOT**, tap **RESET**, release BOOT to
   enter download mode; power over USB-C only while flashing. Build with the
   `CDCOnBoot=cdc` board option so `Serial` goes over the native USB port.
4. Open Serial Monitor at 115200 to watch the debug readout; confirm arrivals
   with `docker compose logs -f monitor`.

## WiFi/BLE sniffer (`esp32_sniffer`)

One board sniffs one radio: set `SCAN_MODE` to `SCAN_WIFI` or `SCAN_BLE` and
deploy a mix. BLE coexists cleanly with the WiFi/MQTT uplink; WiFi promiscuous
mode has to time-slice against the uplink, so serial→LoRa backhaul is smoother
there.

Key knobs: `RSSI_MIN` (ignore weak frames), `COOLDOWN_MS` (per-MAC re-publish
suppression), on-device `WHITELIST[]` (known MACs are never transmitted; they
would waste LoRa airtime).

**RF attacks** (`DETECT_ATTACKS 1`, WiFi mode): deauth-flood counting
(`DEAUTH_THRESHOLD` per window), rogue-AP detection for `PROTECT_SSID` against
`KNOWN_BSSIDS[]`, and RF-silence reporting (`SILENCE_SECONDS`).
**BLE trackers** (`DETECT_TRACKERS 1`, BLE mode): Apple Find My
offline-finding, Tile, and SmartTag advertisement signatures.

**Drone Remote ID** (`DETECT_DRONEID 1`): the same sniffer also reports drone
Remote ID broadcasts (ASTM F3411 / Open Drone ID), the public identification
signal most drones must transmit. WiFi mode matches the beacon vendor IE
(ASD-STAN OUI `FA:0B:BC`); BLE mode matches service data UUID `0xFFFA`.
Passive reception only; RSSI stands in for proximity.

## Vehicle sensor (`qmc5883l_vehicle`)

**Wiring** (GY-271 module, I2C): VCC→3V3, GND→GND, SDA→GPIO8, SCL→GPIO9 (the
C3 core's default `Wire` pins). Mount rigidly within ~2–5 m of the drive lane;
a swaying post reads as field change and false-alarms.

**Calibration**: every site's field differs and sensors drift. Flash with
`DEBUG_PRINT` on and watch the once-per-second `mag/baseline/delta` line
during a few drive-bys, then set `TRIGGER_LSB` between ambient noise and your
smallest vehicle. `BASELINE_ALPHA` controls drift absorption; a shift
sustained past `RESEED_MS` (a car that parked) becomes the new baseline
automatically.

## Vibration sensor (`piezo_vibration`)

**Wiring**: piezo disc between `PIEZO_PIN` (default GPIO3) and GND with a
1 MΩ resistor in parallel to bleed charge. The ESP32's ESD diodes clip
knock-energy spikes safely; add a 3.3 V zener across large discs on
hard-struck surfaces. Mount the disc rigidly (epoxy/screw clamp); a loose
disc reads as noise. On a fence, one disc per panel-run carries several meters
of mesh.

**Calibration**: flash with `DEBUG_PRINT` on and watch the once-per-second
`env_max/baseline/hits_in_window` line. Knock, shake, and let the wind blow;
then set `SPIKE_THRESHOLD` above the loudest wind reading and below your
softest real knock. `SHAKE_HITS`/`WINDOW_MS` set how much repetition counts
as climbing. `GLASS_MIN_SAMPLES` separates a shatter (dense ringing, the
`ring=` debug figure) from a knock; calibrate by tapping vs. breaking a jar.

## Lightning sensor (`as3935_lightning`)

**Wiring** (AS3935 module, I2C): VCC→3V3, GND→GND, SDA→GPIO8, SCL→GPIO9,
IRQ→`IRQ_PIN` (default GPIO4). Keep it away from switching supplies and LED
drivers; the AS3935 hears electrical noise as disturbers.

**Calibration**: flash with `DEBUG_PRINT` on during a quiet day. Frequent
"noise"/"disturber" prints mean raise `NOISE_FLOOR`/`WATCHDOG` or move the
module. `TUNING_CAP` trims the antenna (0–15, module-specific); `OUTDOOR`
switches AFE gain. Each strike reports with an estimated distance, and the
monitor uses them to thunder-label vibration alerts
([Configuration](configuration.md): `LightningLabelSeconds`).

## Backhaul modes

- **WiFi → MQTT** (`OUTPUT_SERIAL 0`, default): the sensor must be in WiFi
  range of the broker. Simplest.
- **Serial → LoRa mesh** (`OUTPUT_SERIAL 1`): the sketch prints one compact
  line per event to its UART for a mesh node or relay host to carry. See
  [Off-Grid Backhaul](off-grid.md).

## Conserving LoRa bandwidth

LoRa is a few kbps with legal duty-cycle limits. Three measures, on by
default, together cut traffic roughly 10–20×:

- **Compact wire format**: `AABBCC112233,-64` (~16 bytes) instead of JSON
  (~56); sensor events are smaller still (`V,123`, `K,812`, `S,9`).
- **On-device whitelist**: known MACs never transmit.
- **Sensor id dropped on the wire**: the relaying mesh node's own address
  identifies the sensor; map node id → friendly name at the base with
  `--sensor-map`.

If you still saturate the channel: raise `COOLDOWN_MS`, tighten `RSSI_MIN`,
or use a faster Meshtastic modem preset (ShortFast) if range allows.
