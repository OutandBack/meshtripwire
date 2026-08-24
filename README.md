# meshtripwire

Wireless tripwire for remote properties. ESP32 sensor nodes (Paxcounter-derived firmware + Meshtastic) detect nearby WiFi/BLE MAC addresses and relay them over LoRa to a Raspberry Pi base station, which filters, logs, and alerts on unknown devices.

Not affiliated with the Meshtastic project. Proof of concept, provided as-is.

**Caveats:**
- Stock Paxcounter sends anonymized people *counts*, not per-MAC data — the sensor side requires custom firmware that publishes `{"mac", "from", "rssi"}` JSON to MQTT.
- Modern phones randomize their MACs. Whitelist fixed devices (cameras, sensors, laptops); treat phone MACs as ephemeral.

## How it works

Sensor nodes scan and forward sightings over LoRa. The base station runs Mosquitto and `mqtt/mac_alert_monitor.py`, which:

1. Parses sightings from MQTT (`meshtastic/receive`)
2. Drops signals below the RSSI threshold, smooths the rest with an EMA
3. Checks the MAC against `config/whitelist.txt`
4. Logs every detection to SQLite (`logs/detections.db`), tagged with GPS from the payload or the static per-node coordinates in `config/nodes.json`
5. Alerts on unknown MACs via ntfy.sh, webhook, or Twilio SMS, rate-limited per MAC

An optional Node-RED dashboard (`node-red/flows.json`) shows live sightings with a strong-signals-only toggle.

## Setup

```bash
bash setup/install_dependencies.sh   # apt packages, venv, Mosquitto, Node-RED
```

Edit `config/config.ini` (broker, thresholds, notification credentials), `config/whitelist.txt` (one MAC per line), and `config/nodes.json` (node ID to GPS mapping). Then, from the project root:

```bash
venv/bin/python -m mqtt.mac_alert_monitor
```

To run as a service, edit the paths/user in `setup/meshtripwire.service`, then:

```bash
sudo cp setup/meshtripwire.service /etc/systemd/system/
sudo systemctl enable --now meshtripwire
```

## Configuration

All settings live in `config/config.ini`:

- `[MQTT]` — broker host/port/topic; optional `Username`/`Password`
- `[Filtering]` — `RSSIMin` threshold, `EMAlpha` smoothing, `StateTimeoutSeconds`, `AlertCooldownSeconds`
- `[Notifications]` — enable/configure ntfy.sh, webhook, Twilio SMS

## Log sync

Back up detections to any cloud storage rclone supports:

```bash
rclone config                                  # one-time remote setup
bash setup/sync_logs.sh gdrive:meshtripwire    # or from cron, e.g. every 30 min
```

## Testing

```bash
venv/bin/python test_smoke.py
```
