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

echo "📁 Creating logs directory if it doesn't exist..."
mkdir -p ~/tripwire-system/logs

echo "✅ Setup complete. You can now run the monitor and import the Node-RED flow."