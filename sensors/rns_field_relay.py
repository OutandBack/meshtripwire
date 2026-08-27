"""Field-side relay: sensor serial lines -> LXMF messages over Reticulum.

Runs on a small host (a Pi Zero W with an RNode, or any machine on a
Reticulum network) wired by USB/UART to a sensor ESP32 built with
OUTPUT_SERIAL 1 — no firmware changes needed. Each compact line the sensor
prints ("AABBCC112233,-64", "V,123", "K,812", "S,9") is sent as one LXMF
message, prefixed "<node-name>:", to the base station's mqtt/rns_bridge.py.

Usage:
    pip install rns lxmf pyserial
    python -m sensors.rns_field_relay --serial-port /dev/ttyACM0 \
        --dest <hash printed by rns_bridge> --node-name gate

Reticulum interfaces (RNode, TCP, ...) come from the host's own RNS config
(~/.reticulum). Identity persists under --storage.
"""
import argparse
import os
import time


def main():
    import RNS
    import LXMF
    import serial

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--serial-port', required=True, help='sensor ESP32 USB/UART device')
    ap.add_argument('--baud', type=int, default=115200)
    ap.add_argument('--dest', required=True, help='LXMF destination hash of the base rns_bridge')
    ap.add_argument('--node-name', default='sensor', help='sensor name prefixed to each line')
    ap.add_argument('--storage', default=os.path.expanduser('~/.meshtripwire-relay'))
    args = ap.parse_args()

    os.makedirs(args.storage, exist_ok=True)
    identity_path = os.path.join(args.storage, 'identity')
    if os.path.isfile(identity_path):
        identity = RNS.Identity.from_file(identity_path)
    else:
        identity = RNS.Identity()
        identity.to_file(identity_path)

    RNS.Reticulum()
    router = LXMF.LXMRouter(identity=identity, storagepath=args.storage)
    source = router.register_delivery_identity(identity, display_name=args.node_name)

    dest_hash = bytes.fromhex(args.dest)
    print("Resolving path to the base bridge...")
    while not RNS.Transport.has_path(dest_hash):
        RNS.Transport.request_path(dest_hash)
        time.sleep(2)
    bridge = RNS.Destination(RNS.Identity.recall(dest_hash), RNS.Destination.OUT,
                             RNS.Destination.SINGLE, "lxmf", "delivery")

    ser = serial.Serial(args.serial_port, args.baud, timeout=5)
    print(f"Relaying {args.serial_port} -> LXMF {args.dest[:8]}... as '{args.node_name}'")
    while True:
        line = ser.readline().decode(errors='ignore').strip()
        if not line:
            continue
        # ponytail: fire-and-forget opportunistic delivery; the sensor's own
        # cooldowns pace the traffic, and LXMF retries within its own logic
        msg = LXMF.LXMessage(bridge, source, f"{args.node_name}:{line}",
                             desired_method=LXMF.LXMessage.OPPORTUNISTIC)
        router.handle_outbound(msg)
        print(f"sent: {args.node_name}:{line}")


if __name__ == '__main__':
    main()
