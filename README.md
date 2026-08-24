*__NOTE: OaB is not directly associated with the Meshtastic project. This project is a proof-of-concept for creating additional usecases for Meshtastic and similar devices.__*

# meshtripwire

A Meshtastic-based wireless tripwire (Paxcounter-derived sensors).

This project provides a hybrid LoRa + WiFi BLE detection system to monitor for unknown wireless devices on remote properties using Meshtastic nodes and Paxcounter firmware. It integrates with MQTT, Node-RED, SQLite, and modern notification platforms.

## 🏗 System Architecture

- **ESP32 Sensor Nodes (Paxcounter + Meshtastic)**: Scans for MACs, sends over LoRa
- **Raspberry Pi Base Station**:
  - Runs Mosquitto MQTT broker
  - Runs `mqtt/mac_alert_monitor.py` to:
    - Parse MQTT messages from Meshtastic nodes
    - Filter sightings based on RSSI and whitelist (`config/whitelist.txt`)
    - Apply Exponential Moving Average (EMA) smoothing to RSSI
    - Log detections to SQLite (`logs/detections.db`)
    - Trigger alerts via `notifications/alert_dispatch.py`
  - Hosts a Node-RED dashboard (`node-red/flows.json`) for visualization

## 📍 Static Node Location Support

Each fixed node has predefined GPS coordinates stored in `config/nodes.json`. Used to map detections and estimate movement paths.

## 📊 Features

- Central configuration via `config/config.ini`
- Whitelist of known MACs (`config/whitelist.txt`)
- Node locations defined in `config/nodes.json`
- RSSI filtering (configurable threshold)
- Exponential Moving Average (EMA) smoothing for RSSI values
- SQLite logging (`logs/detections.db`) with periodic commits
- Node-RED dashboard with worldmap visualization
- Alert dispatch to:
  - `ntfy.sh`
  - Webhook endpoint
  - Twilio SMS

## 🚨 Alert Conditions

Unknown MACs are compared to whitelist and if above RSSI threshold, an alert is triggered.

## 🧠 Filtering & Heuristics

- **RSSI Threshold**: Configurable in `config/config.ini` (`[Filtering] RSSIMin`). Signals weaker than this are ignored.
- **EMA Smoothing**: Exponential Moving Average is applied to RSSI values to reduce noise. Alpha value configurable in `config/config.ini` (`[Filtering] EMAlpha`).
- **State Timeout**: Internal state for EMA smoothing is cleared for MACs not seen for a configurable duration (`[Filtering] StateTimeoutSeconds`).
- **Movement Path**: *Note: The current scripts focus on detection and alerting per node. Advanced path estimation (Kalman/heuristic) mentioned previously is not implemented in the provided Python scripts but could be added to Node-RED or a separate analysis script.*

## 🗺 Node-RED Visualization

A worldmap dashboard shows:
- Real-time MAC sightings
- Movement between nodes (with trails)
- Clickable history per MAC

## 🛠️ Setup

1.  **Install Dependencies**:
    ```bash
    bash setup/install_dependencies.sh
    ```
    *(Review this script to ensure it installs necessary packages like `paho-mqtt`, `requests`, etc.)*
2.  **Configure**:
    - Copy or rename `config/config.ini.example` to `config/config.ini` (if an example file exists, otherwise create it).
    - Edit `config/config.ini` to set your MQTT broker details, file paths, filtering thresholds, and notification service credentials/settings.
    - Populate `config/whitelist.txt` with known MAC addresses (one per line).
    - Populate `config/nodes.json` with your Meshtastic node IDs and their corresponding GPS coordinates.
3.  **Run Monitor** (from the project root, so relative config paths resolve):
    ```bash
    venv/bin/python -m mqtt.mac_alert_monitor
    ```
4.  **Setup Node-RED (Optional)**:
    - Import `node-red/flows.json` into your Node-RED instance.
    - Ensure Node-RED is configured to connect to your MQTT broker and the SQLite database (`logs/detections.db`).

## 📦 Folder Structure

```
meshtripwire/
├── config/
│   ├── config.ini          # Main configuration
│   ├── nodes.json          # Node ID to GPS mapping
│   └── whitelist.txt       # Known MAC addresses
├── logs/
│   └── detections.db       # SQLite database for logged detections
├── mqtt/
│   └── mac_alert_monitor.py  # Main monitoring script
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
