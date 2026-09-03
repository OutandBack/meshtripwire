# ESP32 sniffer node

A dedicated ESP32 that captures WiFi MACs in promiscuous mode and feeds them to
the tripwire, for coverage the base-station scanner can't reach. Publishes the
same `{"mac","from","rssi"}` JSON to `meshtastic/receive`, so the monitor needs
no changes.

For the non-MAC sensor nodes, see
[Vehicle detection node](#vehicle-detection-node-qmc5883l) and
[Vibration node](#vibration-node-piezo-disc) below.

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
- **Serial → LoRa mesh** (`OUTPUT_SERIAL 1`): the sketch prints one compact line
  per sighting to Serial instead. Three mesh stacks carry it today:
  - **Meshtastic** (`SERIAL_MESHCORE 0`, default): wire the ESP32 TX to the
    node's RX and enable its Serial module (`TEXTMSG` mode);
    `mqtt/serial_bridge.py` at the base republishes each line (alongside
    node-presence detection).
  - **MeshCore** (`SERIAL_MESHCORE 1`): lines are framed in the companion serial
    protocol as channel messages, prefixed `NODE_ID:` (MeshCore channel messages
    carry no sender id). The wired node runs MeshCore companion firmware; at the
    base, `mqtt/meshcore_bridge.py` (`pip install meshcore`) listens on a
    companion radio sharing the same channel. `MESHCORE_CHANNEL` picks the
    channel index.
  - **LXMF/Reticulum**: connect the sensor's serial to a small relay host
    running `sensors/rns_field_relay.py` (Pi Zero W + RNode works); at the
    base, `mqtt/rns_bridge.py` receives the LXMF messages. No firmware
    changes; the same plain lines ride either stack.

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

## Vehicle detection node (QMC5883L)

`firmware/qmc5883l_vehicle/` turns any ESP32 plus a ~$2 GY-271 (QMC5883L)
magnetometer module into a driveway vehicle sensor. A vehicle's ferrous mass
shifts the local magnetic field as it passes; the node tracks a slow baseline
and reports when the field magnitude deviates past a threshold. It catches
vehicles carrying no phone or BLE gear at all — the case the MAC sniffers miss.

**Wiring** (GY-271, I2C): VCC→3V3, GND→GND, SDA/SCL→your board's default Wire
pins. Mount rigidly within ~2–5 m of the drive lane (farther works for trucks);
a swaying post reads as field change and false-alarms.

**Backhaul**, same `OUTPUT_SERIAL` switch as the sniffer:

- **WiFi → MQTT** (`0`, default): publishes
  `{"event":"vehicle","from":"gate","mag":<delta>}` to `meshtastic/receive`.
- **Serial → LoRa mesh** (`1`): prints a compact `V,<delta>` line (~6 bytes) for
  a wired Meshtastic node to relay; `serial_bridge.py` expands it and maps the
  relay node to a sensor name via `--sensor-map`.

The monitor routes vehicle events straight to alerting — no whitelist, EMA, or
dwell (a magnetometer hit is already vehicle-specific and identity-blind). It
respects arming and its own `[Filtering] VehicleAlertCooldownSeconds` cooldown,
and events count toward the sensor watchdog.

**Calibration**: every site's field differs and sensors drift. Flash with
detection on, watch Serial/MQTT during a few drive-bys, then set `TRIGGER_LSB`
between ambient noise and your smallest vehicle. `BASELINE_ALPHA` controls how
fast the baseline absorbs drift; a sustained shift longer than `RESEED_MS`
(a car that parked) becomes the new baseline automatically.

## Vibration node (piezo disc)

`firmware/piezo_vibration/` turns any ESP32 plus a piezo disc (~$0.30) into a
door-knock or fence-vibration sensor that classifies on-device:

- **knock** — one or a few impacts then quiet (door knock, thrown rock) →
  `{"event":"knock","from":"fence-e","peak":N}` or `K,<peak>` over LoRa.
- **shake** — 4+ impacts inside a rolling 5 s window (climbing, fence shaking) →
  `{"event":"shake","from":"fence-e","hits":N}` or `S,<hits>`.
- **wind** — sustained low-amplitude noise stays below `SPIKE_THRESHOLD` and
  produces nothing. The threshold IS the wind filter.

**Wiring**: piezo disc between `PIEZO_PIN` (default GPIO3) and GND with a 1 MΩ
resistor in parallel to bleed charge. The ESP32's ESD diodes clip knock-energy
spikes safely; add a 3.3 V zener across large discs on hard-struck surfaces.
Mount the disc rigidly (epoxy/screw clamp) — a loose disc reads as noise. On a
fence, one disc per panel-run carries several meters of mesh.

**Calibration**: flash with `DEBUG_PRINT` on and watch the 1/s
`env_max/baseline/hits_in_window` line. Knock, shake, and let the wind blow,
then set `SPIKE_THRESHOLD` above the loudest wind reading and below your softest
real knock. `SHAKE_HITS`/`WINDOW_MS` set how much repetition counts as climbing.

The monitor alerts on both types with independent per-node cooldowns
(`[Filtering] KnockAlertCooldownSeconds` / `ShakeAlertCooldownSeconds` — shake
defaults lower because it's high-confidence). Backhaul, arming, SQLite logging,
and the sensor watchdog behave exactly as for the vehicle node.

## Lightning node (AS3935)

`firmware/as3935_lightning/` pairs any ESP32 with an AS3935 franklin lightning
sensor module (~$8, e.g. CJMCU-3935). It detects the RF signature of strikes up
to ~40 km out with an estimated distance, and reports each as
`{"event":"lightning","from":node,"km":N}` (or a compact `L,<km>` line over the
LoRa relay).

Its job is false-positive control: thunder shakes fences and doors hard enough
to pass the piezo's wind filter. After a lightning event, the monitor labels
vibration alerts inside `[Filtering] LightningLabelSeconds` (default 120) as
possible thunder — they still alert (a storm is decent cover for a real
intruder, so nothing is dropped) but carry the label and stay out of
correlation. Locally, with no weather API and no Internet.

**Wiring** (I2C): VCC→3V3, GND→GND, SDA→GPIO8, SCL→GPIO9, IRQ→`IRQ_PIN`
(default GPIO4). Keep it away from switching supplies and LED drivers.

**Calibration**: flash with `DEBUG_PRINT` on during a quiet day. Frequent
"noise"/"disturber" prints mean raise `NOISE_FLOOR`/`WATCHDOG` or move the
module. `TUNING_CAP` trims the antenna (0–15, module-specific — many breakouts
ship with their value noted). `OUTDOOR` switches AFE gain.

## Digital sensors via stock Meshtastic (Detection Sensor module)

Reed switches, PIR motion sensors, IR beam-break receivers, float switches —
anything that yields a GPIO high/low — need **no custom firmware at all**. Wire
the sensor to a spare GPIO on a Meshtastic node and enable the stock Detection
Sensor module (firmware ≥ 2.2.2):

```
meshtastic --set detection_sensor.enabled true \
           --set detection_sensor.monitor_pin 4 \
           --set detection_sensor.name "Back Gate" \
           --set detection_sensor.use_pullup true \
           --set detection_sensor.detection_triggered_high false
```

The node sends the configured name over the mesh when the pin changes state.
`mqtt/serial_bridge.py` maps these packets to
`{"v":1,"type":"contact","node":...,"event":"trigger"}` events — the sensor
name comes from `--sensor-map` if the relay node is mapped, else from the
module's configured name. The monitor alerts with
`[Filtering] ContactAlertCooldownSeconds` (default 60), which also absorbs the
module's periodic state re-broadcasts.

Use this for every on/off sensor; reach for custom firmware (above) only when
the sensor needs on-device analog classification.

## Reality checks

- **MAC randomization**: modern phones rotate their WiFi MAC, so most sightings
  are ephemeral randoms — good for "someone is here", not for identifying who.
  Whitelist fixed-MAC gear only.
- **Airtime**: per-MAC sightings over LoRa add up fast. Keep `COOLDOWN_MS` high
  and `RSSI_MIN` tight so only close, persistent devices generate traffic.
- **Legality**: passively logging MAC addresses may be regulated where you are.
  Your property, your responsibility.
