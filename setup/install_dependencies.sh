#!/bin/bash

# install_dependencies.sh

echo "🔧 Updating system..."
sudo apt update

echo "📦 Installing dependencies..."
sudo apt install -y mosquitto mosquitto-clients python3-pip python3-venv sqlite3

# Get the directory where the script is located
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
PROJECT_ROOT=$(dirname "$SCRIPT_DIR") # Assumes setup is one level down from root

echo "🐍 Installing Python packages into venv..."
# System-wide pip installs fail on modern Raspberry Pi OS (PEP 668); use a venv
python3 -m venv "$PROJECT_ROOT/venv"
"$PROJECT_ROOT/venv/bin/pip" install -r "$PROJECT_ROOT/requirements.txt"

echo "✅ Enabling Mosquitto MQTT broker..."
sudo systemctl enable mosquitto
sudo systemctl start mosquitto

echo "📁 Creating logs directory relative to the script location..."
mkdir -p "$PROJECT_ROOT/logs"

echo "✅ Setup complete. Configure config/config.ini, then run:"
echo "   cd $PROJECT_ROOT && venv/bin/python -m mqtt.mac_alert_monitor"
echo "   cd $PROJECT_ROOT && venv/bin/python -m dashboard.server   # dashboard on :8080"
