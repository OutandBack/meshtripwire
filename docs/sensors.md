# Sensors

Anything that publishes to the broker is a sensor. In order of effort:

## Base scanner ($0)

`sensors/base_scanner.py` sniffs real WiFi/BLE MACs on the machine running the
broker. No firmware; range limited to the base station's radios.

```bash
venv/bin/python -m sensors.base_scanner --node base --ble [--wifi wlan1mon]
```

## ESP32 sniffer nodes ($2–3 each)

`firmware/esp32_sniffer/`: dedicated ESP32s doing promiscuous WiFi *or* BLE
capture (one radio per board), backhauling over WiFi/MQTT or serial→LoRa.
Deploy several for distributed coverage. See [Firmware](firmware.md).

## Vehicle sensor (QMC5883L magnetometer)

`firmware/qmc5883l_vehicle/`: a vehicle's ferrous mass shifts the local
magnetic field as it passes within ~2–5 m. The node tracks a slow baseline and
reports when the magnitude deviates past a threshold. Catches vehicles carrying
**no phone or BLE gear at all**: the case MAC sniffing misses. A car that
parks becomes the new baseline automatically.

## Vibration sensor (piezo disc)

`firmware/piezo_vibration/`: a $0.30 piezo disc glued to a door, gate, or
fence run, classified on-device:

- **knock**: one or a few impacts then quiet (door knock, thrown rock)
- **shake**: 4+ impacts inside a rolling 5 s window (climbing, fence shaking)
- **wind**: sustained low-amplitude noise stays below the spike threshold and
  produces nothing; the threshold *is* the wind filter

## Drone detection (Remote ID, firmware-only)

Enable `DETECT_DRONEID` in the ESP32 sniffer and it also hears **drone Remote
ID broadcasts** (ASTM F3411 / Open Drone ID), the public identification
signal most drones must transmit, over the same WiFi/BLE radio it already
sniffs. Zero extra hardware: counter-UAS early warning as a compile flag.
Alerts are rate-limited by `DroneAlertCooldownSeconds`.

## Lightning sensor (AS3935)

`firmware/as3935_lightning/`: an $8 franklin lightning sensor that hears
strikes up to ~40 km away, locally, with no weather API. Its role is
false-positive control: thunder passes the piezo's wind filter, so after a
strike the monitor labels vibration alerts within `LightningLabelSeconds` as
possible thunder. They still alert (label, not drop; a storm is cover for a
real intruder) but carry the tag and stay out of correlation.

## Derived detections (no new hardware)

The same modules detect things done *to* the system and patterns across time:

- **Deauth attack** (`DETECT_ATTACKS`): floods of 802.11 deauth/disassoc
  frames, the standard trick for blinding WiFi cameras
- **Rogue AP**: your SSID broadcast from a BSSID you don't own
- **RF silence**: a sniffer that suddenly hears nothing at all is being jammed
  (or the site truly went quiet; tune `SILENCE_SECONDS` to your RF floor)
- **BLE trackers** (`DETECT_TRACKERS`): Apple Find My offline-finding, Tile,
  and SmartTag advertisements; a tracker loitering on your property that isn't
  yours means someone tagged a vehicle. Expect some benign hits from any
  Find My device separated from its owner.
- **Asset departure** (`[Assets] WatchedMacs`): inverse alerting; a *known*
  MAC (your truck, a trail cam) going unseen past its timeout
- **Casing**: an unknown MAC that alerts and has appeared on `CasingDays`
  distinct days within the window; passers-by don't repeat
- **Mass blackout**: `MassOfflineCount`+ sensors offline simultaneously is
  jamming or a power cut, not a dead battery

## Contact sensors (no custom firmware)

Anything producing a GPIO high/low (reed switches, PIR motion sensors, IR
beam-break receivers, float switches) wires straight to a Meshtastic node's spare
GPIO using the stock **Detection Sensor module** (firmware ≥ 2.2.2):

```bash
meshtastic --set detection_sensor.enabled true \
           --set detection_sensor.monitor_pin 4 \
           --set detection_sensor.name "Back Gate" \
           --set detection_sensor.use_pullup true \
           --set detection_sensor.detection_triggered_high false
```

The node sends the configured name over the mesh on pin change;
`mqtt/serial_bridge.py` maps it to a `contact/trigger` event. Use this for
every on/off sensor; reach for custom firmware only when the sensor needs
on-device analog classification.

## USB LoRa-mesh bridge (mesh-device presence)

`mqtt/serial_bridge.py` also flags the *presence* of any Meshtastic node it
hears over RF, tagged by the node's own radio MAC: a tripwire for people
carrying mesh devices.

## Reality checks

- **MAC randomization**: modern phones rotate WiFi/BLE MACs. Every wireless
  tier detects *presence*, not *identity*. Whitelist only fixed-MAC gear
  (cameras, laptops, sensors), treat phone MACs as ephemeral, and lean on the
  [dwell filter](configuration.md#cutting-false-alarms).
- **Coverage beats sophistication**: more cheap nodes outperform one fancy
  node.
- **Legality**: passively logging MAC addresses may be regulated where you
  are. Your property, your responsibility.
