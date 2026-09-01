"""Bridge LXMF (Reticulum) sensor messages into the tripwire MQTT feed.

Field sensors reach a Reticulum network through a small relay host (see
sensors/rns_field_relay.py) that reads the sensor's serial lines and sends
each as an LXMF message. This bridge runs at the base beside the stack,
receives those messages, and republishes the same MQTT payloads the
Meshtastic and MeshCore bridges produce.

Sensor naming: a "node:line" prefix in the message text wins (the field relay
adds it); otherwise the LXMF source hash identifies the sensor, mapped to a
friendly name via --sensor-map, else shortened to its first 8 hex chars.

Usage (host-side, next to the Docker stack):
    pip install rns lxmf
    python -m mqtt.rns_bridge --broker-port 1884
The bridge prints its LXMF destination hash on startup — give that to each
field relay as --dest. Identity and RNS config persist under --storage.
"""
import argparse
import json
import time

import paho.mqtt.client as mqtt

from mqtt.meshcore_bridge import payload_from_text
from mqtt.serial_bridge import sighting_from_line


def payload_from_lxmf(content, source_hash, sensor_map=None):
    """Map an LXMF message to an MQTT payload string, or None.

    content is the message bytes; source_hash the sender's hash (hex string).
    A "node:line" prefix names the sensor (the field relay adds it); a bare
    line falls back to the source hash, remappable via sensor_map.
    """
    try:
        text = content.decode().strip()
    except UnicodeDecodeError:
        return None
    if not text:
        return None
    prefixed = payload_from_text(text, sensor_map)
    if prefixed:
        return prefixed
    name = (sensor_map or {}).get(source_hash, source_hash[:8])
    return sighting_from_line(text, name)


def main():
    # Imported here so the monitor container doesn't need rns/lxmf
    import RNS
    import LXMF
    import os

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--broker', default='localhost')
    ap.add_argument('--broker-port', type=int, default=1883)
    ap.add_argument('--topic', default='meshtastic/receive')
    ap.add_argument('--storage', default='config/rns_bridge',
                    help='directory for the bridge identity and LXMF state')
    ap.add_argument('--sensor-map', help='JSON file mapping "node:" prefix or '
                                         'LXMF source hash -> sensor name')
    args = ap.parse_args()

    sensor_map = {}
    if args.sensor_map:
        with open(args.sensor_map) as f:
            sensor_map = json.load(f)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(args.broker, args.broker_port, 60)
    client.loop_start()

    os.makedirs(args.storage, exist_ok=True)
    identity_path = os.path.join(args.storage, 'identity')
    if os.path.isfile(identity_path):
        identity = RNS.Identity.from_file(identity_path)
    else:
        identity = RNS.Identity()
        identity.to_file(identity_path)

    RNS.Reticulum()  # uses the host's RNS config (interfaces, RNode, etc.)
    router = LXMF.LXMRouter(identity=identity, storagepath=args.storage)
    dest = router.register_delivery_identity(identity, display_name='meshtripwire')

    def on_delivery(message):
        payload = payload_from_lxmf(message.content,
                                    RNS.hexrep(message.source_hash, delimit=False),
                                    sensor_map)
        if payload:
            client.publish(args.topic, payload)
            print(f"forwarded: {payload}")

    router.register_delivery_callback(on_delivery)
    print(f"LXMF destination: {RNS.hexrep(dest.hash, delimit=False)}")
    print(f"Bridging LXMF -> mqtt://{args.broker}:{args.broker_port}/{args.topic}")
    while True:
        router.announce(dest.hash)  # let field relays find us
        time.sleep(1800)


if __name__ == '__main__':
    main()
