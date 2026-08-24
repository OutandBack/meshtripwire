"""Bridge a USB Meshtastic node into the tripwire MQTT feed.

Stock Meshtastic firmware doesn't publish the {"mac","from","rssi"} JSON this
project consumes. This bridge listens on a locally attached node and feeds the
MQTT topic in two ways:

  1. Relayed sniffer sightings: a remote ESP32 sniffer prints JSON to a field
     Meshtastic node's Serial module, which sends it over LoRa as a text message.
     The bridge republishes that JSON verbatim — real WiFi/BLE MACs, off-grid.
  2. Node presence: for any other RF packet, it synthesizes a sighting from the
     transmitting node's own MAC (from the node DB), so any Meshtastic device
     near the property is itself a detection.

Usage (host-side, next to the Docker stack):
    pip install meshtastic
    python -m mqtt.serial_bridge --serial-port /dev/ttyUSB0 --broker-port 1884
"""
import argparse
import json
import time

import paho.mqtt.client as mqtt


def parse_compact(text):
    """Parse a bandwidth-optimized "AABBCC112233,-64" sniffer line.

    Returns (mac_with_colons, rssi) or None. Colons are dropped and the sensor id
    is omitted on the wire (mapped back from the relay node), to save LoRa bytes.
    """
    parts = text.split(',')
    if len(parts) != 2:
        return None
    machex, rssi = parts[0].strip(), parts[1].strip()
    if len(machex) != 12 or not all(c in '0123456789abcdefABCDEF' for c in machex):
        return None
    try:
        rssi = int(rssi)
    except ValueError:
        return None
    mac = ':'.join(machex[i:i + 2] for i in range(0, 12, 2)).upper()
    return mac, rssi


def payload_for(packet, nodes, sensor_map=None):
    """Return the MQTT payload string for one received packet, or None to skip.

    nodes is the interface's node DB ({nodeId: {...}}). sensor_map optionally maps
    a relay node id to a friendly sensor name for compact-format sightings. Pure
    function so it's testable without a live Meshtastic interface.
    """
    # Mode 1: a relayed sniffer sighting arriving as a Meshtastic text message —
    # either full JSON, or the compact "AABBCC112233,-64" wire format.
    text = packet.get('decoded', {}).get('text')
    if text:
        text = text.strip()
        try:
            sighting = json.loads(text)
        except (ValueError, TypeError):
            sighting = None
        if isinstance(sighting, dict) and sighting.get('mac'):
            return json.dumps(sighting)
        compact = parse_compact(text)
        if compact:
            mac, rssi = compact
            sender = packet.get('fromId') or str(packet.get('from'))
            name = (sensor_map or {}).get(sender, sender)
            return json.dumps({'mac': mac, 'from': name, 'rssi': rssi})

    # Mode 2: presence of the transmitting node itself — tag it by its own MAC.
    rssi = packet.get('rxRssi')
    if not rssi:
        return None  # locally generated packet, not an RF sighting
    sender = packet.get('fromId') or str(packet.get('from'))
    info = (nodes or {}).get(sender, {})
    sighting = {
        'mac': info.get('user', {}).get('macaddr', ''),
        'from': sender,
        'rssi': rssi,
    }
    pos = info.get('position', {})
    if pos.get('latitude') is not None:
        sighting['lat'] = pos['latitude']
        sighting['lon'] = pos['longitude']
    return json.dumps(sighting)


def main():
    # Imported here so the monitor container doesn't need the meshtastic package
    import meshtastic.serial_interface
    from pubsub import pub

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--serial-port', default=None, help='USB device (default: autodetect)')
    ap.add_argument('--broker', default='localhost')
    ap.add_argument('--broker-port', type=int, default=1883)
    ap.add_argument('--topic', default='meshtastic/receive')
    ap.add_argument('--sensor-map', help='JSON file mapping relay node id -> sensor name '
                                         '(e.g. {"!a1b2c3d4": "gate"})')
    args = ap.parse_args()

    sensor_map = {}
    if args.sensor_map:
        with open(args.sensor_map) as f:
            sensor_map = json.load(f)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(args.broker, args.broker_port, 60)
    client.loop_start()

    def on_receive(packet, interface):
        payload = payload_for(packet, interface.nodes, sensor_map)
        if payload:
            client.publish(args.topic, payload)
            print(f"forwarded: {payload}")

    pub.subscribe(on_receive, 'meshtastic.receive')
    iface = meshtastic.serial_interface.SerialInterface(devPath=args.serial_port)
    print(f"Bridging {iface.devPath} -> mqtt://{args.broker}:{args.broker_port}/{args.topic}")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        iface.close()


if __name__ == '__main__':
    main()
