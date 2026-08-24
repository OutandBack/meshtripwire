"""Bridge a USB Meshtastic node into the tripwire MQTT feed.

Stock Meshtastic firmware doesn't publish the {"mac","from","rssi"} JSON this
project consumes. This bridge listens on a locally attached node and forwards
every RF packet it hears as a sighting: the sender's node ID, its MAC from the
node DB (when known), the receive RSSI, and GPS if the node DB has a fix.
That makes any transmitting Meshtastic device near the property a detection —
the Paxcounter WiFi/BLE path still needs custom sensor firmware.

Usage (host-side, next to the Docker stack):
    pip install meshtastic
    python -m mqtt.serial_bridge --serial-port /dev/ttyUSB0 --broker-port 1884
"""
import argparse
import json
import time

import paho.mqtt.client as mqtt


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
        rssi = packet.get('rxRssi')
        if not rssi:
            return # Locally generated packet, not an RF sighting
        sender = packet.get('fromId') or str(packet.get('from'))
        info = (interface.nodes or {}).get(sender, {})
        sighting = {
            'mac': info.get('user', {}).get('macaddr', ''),
            'from': sender,
            'rssi': rssi,
        }
        pos = info.get('position', {})
        if pos.get('latitude') is not None:
            sighting['lat'] = pos['latitude']
            sighting['lon'] = pos['longitude']
        client.publish(args.topic, json.dumps(sighting))
        print(f"forwarded: {sighting}")

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
