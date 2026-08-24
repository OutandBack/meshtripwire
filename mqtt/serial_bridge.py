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


def payload_for(packet, nodes):
    """Return the MQTT payload string for one received packet, or None to skip.

    nodes is the interface's node DB ({nodeId: {...}}). Pure function so it's
    testable without a live Meshtastic interface.
    """
    # Mode 1: a relayed sniffer sighting arriving as a Meshtastic text message.
    # The real MAC and RSSI are inside the JSON; the LoRa hop's RSSI is irrelevant.
    text = packet.get('decoded', {}).get('text')
    if text:
        try:
            sighting = json.loads(text)
        except (ValueError, TypeError):
            sighting = None
        if isinstance(sighting, dict) and sighting.get('mac'):
            return text  # republish verbatim

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
    args = ap.parse_args()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(args.broker, args.broker_port, 60)
    client.loop_start()

    def on_receive(packet, interface):
        payload = payload_for(packet, interface.nodes)
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
