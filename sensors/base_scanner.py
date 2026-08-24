"""Base-station radio scanner.

Sniffs real WiFi and/or BLE MAC addresses on the machine running the broker and
publishes them as sightings to the tripwire MQTT topic. No custom firmware, but
coverage is only the base station's radio range (no LoRa backhaul).

Run whichever radios you have:
    # BLE only (needs a Bluetooth adapter; pip install bleak)
    python -m sensors.base_scanner --node base --ble

    # WiFi only (needs a monitor-mode interface + root; pip install scapy)
    sudo python -m sensors.base_scanner --node base --wifi wlan1mon

    # both, to a remote broker
    python -m sensors.base_scanner --node base --ble --wifi wlan1mon --broker 10.0.0.5 --broker-port 1884

Put a monitor interface up first, e.g.:
    sudo iw dev wlan1 interface add wlan1mon type monitor && sudo ip link set wlan1mon up

Each sighting is published as {"mac","from","rssi"} — the same shape the monitor
already consumes. Cooldown suppresses re-publishing the same MAC too often; the
monitor does its own per-MAC alert throttling on top.
"""
import argparse
import json
import os
import threading
import time

import paho.mqtt.client as mqtt

_last_pub = {} # mac -> last publish ts, for local dedup


def publish_sighting(client, topic, node, mac, rssi, cooldown):
    mac = mac.upper()
    now = time.time()
    if now - _last_pub.get(mac, 0) < cooldown:
        return
    _last_pub[mac] = now
    client.publish(topic, json.dumps({"mac": mac, "from": node, "rssi": int(rssi)}))
    print(f"published: {mac} rssi={rssi} via {node}")


def scan_ble(client, args):
    """Continuous BLE scan via bleak; reports each advertising device."""
    import asyncio

    from bleak import BleakScanner

    async def run():
        def on_detect(device, adv):
            rssi = adv.rssi if adv.rssi is not None else -100
            publish_sighting(client, args.topic, args.node, device.address, rssi, args.cooldown)

        scanner = BleakScanner(detection_callback=on_detect)
        print("BLE scanning...")
        await scanner.start()
        while True:
            await asyncio.sleep(3600)

    asyncio.run(run())


def scan_wifi(client, args):
    """Promiscuous WiFi sniff via scapy; reports source MAC of 802.11 frames."""
    from scapy.all import Dot11, sniff

    def handle(pkt):
        if not pkt.haslayer(Dot11) or pkt.addr2 is None:
            return
        rssi = getattr(pkt, 'dBm_AntSignal', None)
        publish_sighting(client, args.topic, args.node, pkt.addr2,
                         rssi if rssi is not None else -100, args.cooldown)

    print(f"WiFi sniffing on {args.wifi}...")
    sniff(iface=args.wifi, prn=handle, store=0)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--node', default='base', help='node id to tag sightings with')
    ap.add_argument('--ble', action='store_true', help='scan BLE (needs bleak + adapter)')
    ap.add_argument('--wifi', metavar='IFACE', help='monitor-mode iface to sniff (needs scapy + root)')
    ap.add_argument('--broker', default='localhost')
    ap.add_argument('--broker-port', type=int, default=1883)
    ap.add_argument('--topic', default='meshtastic/receive')
    ap.add_argument('--cooldown', type=float, default=60, help='seconds between re-publishing the same MAC')
    ap.add_argument('--heartbeat-topic', default='meshtripwire/heartbeat',
                    help='publish liveness here so the monitor watchdog knows this sensor is up')
    ap.add_argument('--heartbeat-interval', type=float, default=120, help='seconds between heartbeats')
    args = ap.parse_args()

    if not args.ble and not args.wifi:
        ap.error("enable at least one radio: --ble and/or --wifi IFACE")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(os.environ.get('MQTT_HOST') or args.broker, args.broker_port, 60)
    client.loop_start()
    print(f"publishing to mqtt://{args.broker}:{args.broker_port}/{args.topic}")

    workers = []
    if args.wifi:
        workers.append(threading.Thread(target=scan_wifi, args=(client, args), daemon=True))
    if args.ble:
        workers.append(threading.Thread(target=scan_ble, args=(client, args), daemon=True))
    for w in workers:
        w.start()
    try:
        while True:
            client.publish(args.heartbeat_topic, json.dumps({"node": args.node}))
            time.sleep(args.heartbeat_interval)
    except KeyboardInterrupt:
        print("stopping")


if __name__ == '__main__':
    main()
