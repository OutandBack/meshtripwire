# Meshtastic + Paxcounter Tripwire System

This project provides a hybrid LoRa + WiFi BLE detection system to monitor for unknown wireless devices on remote properties using Meshtastic nodes and Paxcounter firmware. It integrates with MQTT, Node-RED, SQLite, and modern notification platforms.

## 🏗 System Architecture

- **ESP32 Sensor Nodes (Paxcounter + Meshtastic)**: Scans for MACs, sends over LoRa
- **Raspberry Pi Base Station**: 
  - Runs Mosquitto MQTT broker
  - Parses MAC sightings
  - Filters and logs sightings to SQLite
  - Sends alerts via ntfy.sh, webhook, or SMS
  - Hosts a Node-RED dashboard with real-time and historical views

## 📍 Static Node Location Support

Each fixed node has predefined GPS coordinates stored in `config/nodes.json`. Used to map detections and estimate movement paths.

## 📊 Features

- Whitelist of known MACs (`config/whitelist.txt`)
- RSSI filtering to reduce false positives
- Kalman or heuristic filtering to infer movement paths
- SQLite logging (`logs/detections.db`)
- Node-RED dashboard with worldmap visualization
- Alert dispatch to:
  - `ntfy.sh`
  - Webhook endpoint
  - Twilio SMS

## 🚨 Alert Conditions

Unknown MACs are compared to whitelist and if above RSSI threshold, an alert is triggered.

## 🧠 Filtering & Heuristics

- RSSI threshold can be set in monitor script
- Simple distance + timestamp logic or Kalman filter to estimate actual path across sensors

## 🗺 Node-RED Visualization

A worldmap dashboard shows:
- Real-time MAC sightings
- Movement between nodes (with trails)
- Clickable history per MAC

## 🛠️ Setup

```bash
bash setup/install_dependencies.sh
python3 mqtt/mac_alert_monitor.py
# Optional: import flows.json into Node-RED UI
```

## 📦 Folder Structure

```
tripwire-system/
├── config/
│   ├── whitelist.txt
│   └── nodes.json
├── logs/
│   ├── sightings.log
│   └── detections.db
├── mqtt/
│   └── mac_alert_monitor.py
├── notifications/
│   └── alert_dispatch.py
├── node-red/
│   └── flows.json
├── setup/
│   └── install_dependencies.sh
└── README.md
```

## 🧪 TODO

- [ ] Add advanced filtering toggle in dashboard
- [ ] Sync detection logs to cloud storage
- [ ] GPS fallback handling if Meshtastic lacks signal

---

Developed for off-grid and remote security by combining open-source tools and wireless mesh tech. This project is provided as-is for educartiona purposes only. Do not email author for support please.
