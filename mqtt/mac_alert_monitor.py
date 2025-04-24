import paho.mqtt.client as mqtt
import json
import os
from datetime import datetime
from notifications.alert_dispatch import send_alert

WHITELIST_FILE = 'config/whitelist.txt'
LOG_FILE = 'logs/sightings.log'
TOPIC = 'meshtastic/receive'

def load_whitelist():
    with open(WHITELIST_FILE) as f:
        return {line.strip().upper() for line in f if line.strip()}

def log_detection(mac, node, status):
    timestamp = datetime.utcnow().isoformat() + "Z"
    log_line = f"[{timestamp}] MAC={mac} | NODE={node} | STATUS={status}\n"
    with open(LOG_FILE, "a") as f:
        f.write(log_line)

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
        mac = payload.get("mac", "").upper()
        node = payload.get("from", "unknown")
        if not mac:
            return

        whitelist = load_whitelist()
        status = "whitelisted" if mac in whitelist else "unknown"
        log_detection(mac, node, status)

        if status == "unknown":
            send_alert(mac, node)
    except Exception as e:
        print("Error processing message:", e)

client = mqtt.Client()
client.connect("localhost", 1883)
client.subscribe(TOPIC)
client.on_message = on_message
client.loop_forever()