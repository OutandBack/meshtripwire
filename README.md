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
3. Checks the MAC against `config/whitelist.txt` (hot-reloaded on file change, no restart needed)
4. Logs every detection to SQLite (`logs/detections.db`), tagged with GPS from the payload or the static per-node coordinates in `config/nodes.json`
5. Alerts on unknown MACs via ntfy.sh, webhook, Twilio SMS, or an MQTT topic, rate-limited per MAC

An optional Node-RED dashboard (`node-red/flows.json`) shows live sightings with a strong-signals-only toggle.

### Off-grid alerts (RelayFabric)

ntfy, webhook, and Twilio all need the Internet — the opposite of the remote
sites this is built for. Set `EnableMqtt = true` in `[Notifications]` and the
monitor publishes each alert (JSON: `mac`, `node`, `ts`, `message`) to
`MqttAlertTopic` on the same broker. [RelayFabric](https://github.com/RelayFabric/RelayFabric)
subscribes to that topic and relays alerts over **LXMF/Reticulum or a
Meshtastic channel**, so they reach you with no cellular. See RelayFabric's
`meshtripwire` plugin (formatted alerts) or `examples/meshtripwire.yaml` (relay
with the generic `mqtt` plugin, no extra code).

## Setup

### Docker (recommended)

Runs the full stack: Mosquitto, the monitor, and Node-RED (dashboard on port 1880).

```bash
docker compose up -d --build
```

The monitor reaches the broker via the `MQTT_HOST=mosquitto` env override; `config/` and `logs/` are mounted from the host, so edit config and whitelist in place. The bundled `setup/mosquitto.conf` allows anonymous LAN publishes — add auth/TLS before exposing it further.

### Bare metal (Raspberry Pi)

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

- `[MQTT]` — broker host/port/topic; optional `Username`/`Password` and `UseTLS`/`CAFile`
- `[Files]` — data file paths; `RetentionDays` prunes old detections (0 = keep forever)
- `[Filtering]` — `RSSIMin` threshold, `EMAlpha` smoothing, `StateTimeoutSeconds`, `AlertCooldownSeconds`
- `[Notifications]` — enable/configure ntfy.sh, webhook, Twilio SMS, and the MQTT alert output (`EnableMqtt`/`MqttAlertTopic`, for off-grid relay via RelayFabric)

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
