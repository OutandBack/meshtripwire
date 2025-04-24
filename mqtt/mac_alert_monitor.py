import paho.mqtt.client as mqtt
import json
import os
import sqlite3
from datetime import datetime
from notifications.alert_dispatch import send_alert

WHITELIST_FILE = 'config/whitelist.txt'
NODES_FILE = 'config/nodes.json'
SQLITE_DB = 'logs/detections.db'
RSSI_THRESHOLD = -75  # Ignore weak signals
KALMAN_ALPHA = 0.6

kalman_states = {}

def load_whitelist():
    with open(WHITELIST_FILE) as f:
        return {line.strip().upper() for line in f if line.strip()}

def load_node_locations():
    import json
    with open(NODES_FILE) as f:
        return json.load(f)

def kalman_filter(mac, value):
    if mac not in kalman_states:
        kalman_states[mac] = value
    else:
        kalman_states[mac] = KALMAN_ALPHA * value + (1 - KALMAN_ALPHA) * kalman_states[mac]
    return kalman_states[mac]

def log_to_sqlite(mac, node, rssi, timestamp, lat, lon):
    conn = sqlite3.connect(SQLITE_DB)
    cur = conn.cursor()
    cur.execute("""INSERT INTO detections (mac, node, rssi, timestamp, lat, lon) VALUES (?, ?, ?, ?, ?, ?)""",
                (mac, node, rssi, timestamp, lat, lon))
    conn.commit()
    conn.close()

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
        mac = payload.get("mac", "").upper()
        node = payload.get("from", "unknown")
        rssi = int(payload.get("rssi", -100))
        timestamp = datetime.utcnow().isoformat() + "Z"

        if not mac or rssi < RSSI_THRESHOLD:
            return

        smoothed_rssi = kalman_filter(mac, rssi)
        whitelist = load_whitelist()
        status = "whitelisted" if mac in whitelist else "unknown"

        node_info = load_node_locations().get(node, {})
        lat = node_info.get("lat")
        lon = node_info.get("lon")

        log_to_sqlite(mac, node, smoothed_rssi, timestamp, lat, lon)

        if status == "unknown":
            send_alert(mac, node)
    except Exception as e:
        print("Error processing message:", e)

client = mqtt.Client()
client.connect("localhost", 1883)
client.subscribe("meshtastic/receive")
client.on_message = on_message
client.loop_forever()