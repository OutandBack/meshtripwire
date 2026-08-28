# Getting Started

The base station alone detects nothing — the fastest working tripwire is the
Docker stack plus the base scanner on the Pi's own radios. Total time: about
ten minutes.

## Docker (recommended)

Runs the full stack: Mosquitto, the monitor, and the dashboard (port 8080).

```bash
git clone https://github.com/OutandBack/meshtripwire
cd meshtripwire
docker compose up -d --build
```

The monitor reaches the broker via the `MQTT_HOST=mosquitto` env override;
`config/` and `logs/` are mounted from the host, so edit config and whitelist
in place. If port 1883 is taken on the host, override it:
`MQTT_PORT=1884 docker compose up -d`.

!!! warning "Broker exposure"
    The bundled `setup/mosquitto.conf` allows anonymous LAN publishes so
    sensors work out of the box. Anyone on the LAN can then publish
    detections — add auth/TLS (`[MQTT] Username`/`Password`/`UseTLS`) before
    exposing the broker further. See [Arming & Security](security.md).

## Bare metal (Raspberry Pi)

```bash
bash setup/install_dependencies.sh   # apt packages, venv, Mosquitto
```

Edit `config/config.ini` (broker, thresholds, notification credentials),
`config/whitelist.txt` (one MAC per line), and `config/nodes.json` (node ID to
GPS mapping). Then, from the project root:

```bash
venv/bin/python -m mqtt.mac_alert_monitor
```

To run as a service:

```bash
sudo cp setup/meshtripwire.service /etc/systemd/system/   # edit paths/user first
sudo systemctl enable --now meshtripwire
```

## First sensor: the base scanner

No hardware beyond the Pi's own radios:

```bash
venv/bin/pip install bleak                                  # for --ble; scapy for --wifi
venv/bin/python -m sensors.base_scanner --node base --ble   # add --wifi wlan1mon for WiFi
```

Open the dashboard at `http://<pi>:8080` — BLE sightings appear within
seconds. From here:

- add [ESP32 sniffer nodes](sensors.md) for coverage beyond the Pi's radios
- put known devices in `config/whitelist.txt` so your own gear never alerts
- configure [alert channels](alerts.md) and [arming](security.md)
- follow the [firmware guide](firmware.md) for the vehicle and vibration sensors

## Verify the pipeline

Publish a synthetic sighting and watch it flow:

```bash
mosquitto_pub -h <pi> -t meshtastic/receive \
  -m '{"mac":"DE:AD:BE:EF:00:01","from":"test","rssi":-58}'
docker compose logs -f monitor
```

An unknown MAC above the RSSI threshold logs a detection, lands in the
dashboard feed, and (if armed) dispatches an alert.

## Log sync

Back up detections to any cloud storage rclone supports:

```bash
rclone config                                  # one-time remote setup
bash setup/sync_logs.sh gdrive:meshtripwire    # or from cron, e.g. every 30 min
```
