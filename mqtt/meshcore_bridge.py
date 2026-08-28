"""Bridge a USB MeshCore companion radio into the tripwire MQTT feed.

MeshCore's channel messages carry no sender id (unlike Meshtastic packets), so
sensor nodes prepend their name to each compact line: "gate:V,123". This bridge
listens on a locally attached companion radio (e.g. a Heltec V3 running the
MeshCore companion firmware), maps those lines to the same MQTT payloads
serial_bridge produces, and publishes them to the broker.

Field side: build the sensor firmware with OUTPUT_SERIAL 1 and SERIAL_MESHCORE 1,
wired to a MeshCore companion node; sensor and base radios share a channel.

Usage (host-side, next to the Docker stack):
    pip install meshcore
    python -m mqtt.meshcore_bridge --serial-port /dev/ttyUSB0 --broker-port 1884
"""
import argparse
import asyncio
import json

import paho.mqtt.client as mqtt

from mqtt.serial_bridge import sighting_from_line


def payload_from_text(text, sensor_map=None):
    """Map a "node:line" channel-message text to an MQTT payload string, or None.

    The prefix names the sensor (remappable via sensor_map); the rest is the
    same compact line format the Meshtastic path uses. Plain chat on the
    channel has no valid line and is ignored.
    """
    name, sep, line = (text or '').strip().partition(':')
    name, line = name.strip(), line.strip()
    if not sep or not name or not line:
        return None
    name = (sensor_map or {}).get(name, name)
    return sighting_from_line(line, name)


async def run(args):
    # Imported here so the monitor container doesn't need the meshcore package
    from meshcore import MeshCore, EventType

    sensor_map = {}
    if args.sensor_map:
        with open(args.sensor_map) as f:
            sensor_map = json.load(f)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(args.broker, args.broker_port, 60)
    client.loop_start()

    mc = await MeshCore.create_serial(args.serial_port, args.baud)
    await mc.start_auto_message_fetching()  # pull queued messages on MSG_WAITING

    def on_channel_msg(event):
        payload = payload_from_text(event.payload.get('text', ''), sensor_map)
        if payload:
            client.publish(args.topic, payload)
            print(f"forwarded: {payload}")

    filters = {'channel_idx': args.channel} if args.channel is not None else None
    mc.subscribe(EventType.CHANNEL_MSG_RECV, on_channel_msg, attribute_filters=filters)
    print(f"Bridging {args.serial_port} -> mqtt://{args.broker}:{args.broker_port}/{args.topic}")
    while True:
        await asyncio.sleep(60)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--serial-port', required=True, help='companion radio USB device')
    ap.add_argument('--baud', type=int, default=115200)
    ap.add_argument('--broker', default='localhost')
    ap.add_argument('--broker-port', type=int, default=1883)
    ap.add_argument('--topic', default='meshtastic/receive')
    ap.add_argument('--channel', type=int, default=None,
                    help='only accept messages from this channel index (default: all)')
    ap.add_argument('--sensor-map', help='JSON file mapping line prefix -> sensor name')
    args = ap.parse_args()
    asyncio.run(run(args))


if __name__ == '__main__':
    main()
