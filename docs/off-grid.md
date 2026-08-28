# Off-Grid Backhaul

For sensors out of WiFi range, compact serial lines ride a LoRa mesh to the
base. Two stacks are supported today; the sensor firmware is identical for
both (`OUTPUT_SERIAL 1`, plain text lines).

## Meshtastic

```text
ESP32 sensor ──serial──▶ field Meshtastic node ──LoRa──▶ base node ──USB──▶ serial_bridge ─▶ MQTT
```

1. **Sensor**: build the sketch with `OUTPUT_SERIAL 1`; it prints one compact
   line per event to its UART.
2. **Field mesh node**: wire the ESP32's TX to the node's RX (shared ground)
   and enable the Serial module in text mode:

    ```bash
    meshtastic --set serial.enabled true --set serial.mode TEXTMSG \
               --set serial.baud BAUD_115200
    ```

3. **Base mesh node**: a Meshtastic node on USB to the Pi receives the
   messages.
4. **Bridge**: `python -m mqtt.serial_bridge --serial-port /dev/ttyUSB0
   [--sensor-map config/sensor_nodes.json]` expands each line and republishes
   full JSON to the broker. The same bridge also ingests Detection Sensor
   module packets (contact sensors) and flags mesh-node presence.

## MeshCore

Build the sensor sketches with `SERIAL_MESHCORE 1`; each line is framed in
MeshCore's companion serial protocol as a channel message, prefixed
`NODE_ID:` (MeshCore channel messages carry no sender id), for a wired
MeshCore companion node such as a Heltec V3. At the base:

```bash
pip install meshcore
python -m mqtt.meshcore_bridge --serial-port /dev/ttyUSB0 --broker-port 1883
```

The bridge listens on a companion radio sharing the same channel and maps the
prefixed lines to the standard MQTT payloads. Sensor and base radios must
share the channel key; `MESHCORE_CHANNEL` in the sketch picks the channel
index.

## LXMF / Reticulum

Sensors reach a Reticulum network through a small relay host (a Pi Zero W with
an RNode works) instead of a wired mesh node:

```text
ESP32 sensor ──serial──▶ relay host (rns_field_relay) ──LXMF/RNS──▶ base (rns_bridge) ─▶ MQTT
```

The base side prints its LXMF destination hash on startup:

```bash
pip install rns lxmf
python -m mqtt.rns_bridge --broker-port 1883
```

The field side reads the unmodified sensor's serial lines and sends each as an
LXMF message, prefixed with the node name:

```bash
pip install rns lxmf pyserial
python -m sensors.rns_field_relay --serial-port /dev/ttyACM0 \
    --dest <hash printed by rns_bridge> --node-name gate
```

Reticulum interfaces (RNode, TCP tunnels, ...) come from each host's own RNS
config (`~/.reticulum`).

## Off-grid alerts (RelayFabric)

ntfy, webhook, Twilio, and SMTP all need the Internet, the opposite of the
remote sites this is built for. Set `EnableMqtt = true` in `[Notifications]`
and the monitor publishes each alert as JSON to `MqttAlertTopic` on the same
broker. [RelayFabric](https://github.com/RelayFabric/RelayFabric) subscribes
there and relays alerts over a LoRa mesh (Meshtastic, MeshCore, or
LXMF/Reticulum), so they reach you with no cellular. See RelayFabric's
`meshtripwire` plugin (formatted alerts) or its `examples/meshtripwire.yaml`
(generic `mqtt` plugin, no extra code).
