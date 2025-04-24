#!/bin/bash

# install_dependencies.sh

echo "🔧 Updating system..."
sudo apt update

echo "📦 Installing dependencies..."
sudo apt install -y mosquitto mosquitto-clients python3-pip sqlite3 node-red

echo "🐍 Installing Python packages..."
pip3 install paho-mqtt requests

echo "✅ Enabling Mosquitto MQTT broker..."
sudo systemctl enable mosquitto
sudo systemctl start mosquitto

echo "✅ Enabling Node-RED..."
sudo systemctl enable nodered.service
sudo systemctl start nodered.service

echo "📁 Creating logs directory relative to the script location..."
# Get the directory where the script is located
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
PROJECT_ROOT=$(dirname "$SCRIPT_DIR") # Assumes setup is one level down from root
mkdir -p "$PROJECT_ROOT/logs"

echo "✅ Setup complete. You can now configure config/config.ini and run the monitor."
