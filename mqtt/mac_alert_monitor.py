import paho.mqtt.client as mqtt
import json
import os
import sqlite3
import logging
import configparser
import time # Added for timestamp comparison
from datetime import datetime, timezone # Added timezone
from notifications.alert_dispatch import send_alert

# --- Global State ---
config = None # To hold loaded configuration
ema_states = {} # Stores {'mac': (ema_value, last_seen_timestamp)}
message_counter = 0 # Counter for periodic cleanup
whitelist = set()
node_locations = {}
db_conn = None
db_cursor = None

# --- Configuration Loading ---
def load_app_config(config_path='config/config.ini'):
    """Loads configuration from INI file."""
    global config
    parser = configparser.ConfigParser()
    if not os.path.exists(config_path):
        logging.error(f"Configuration file not found: {config_path}")
        # Provide default values or exit? For now, let's try defaults.
        # This part could be expanded to create a default config if missing.
        config = { # Basic defaults if file is missing
            'MQTT': {'Host': 'localhost', 'Port': 1883, 'Topic': 'meshtastic/receive'},
            'Files': {'Whitelist': 'config/whitelist.txt', 'Nodes': 'config/nodes.json', 'Database': 'logs/detections.db'},
            'Filtering': {'RSSIMin': -75, 'EMAlpha': 0.6},
            'Logging': {'Level': 'INFO', 'Format': '%(asctime)s - %(levelname)s - %(message)s'}
        }
        logging.warning("Using default configuration values.")
        return config # Return the default dict

    try:
        parser.read(config_path)
        config = parser # Store the parser object directly
        logging.info(f"Loaded configuration from {config_path}")
        return config
    except configparser.Error as e:
        logging.error(f"Error reading configuration file {config_path}: {e}")
        # Exit or use defaults? Exiting might be safer if config is crucial.
        raise SystemExit(f"Failed to load configuration: {config_path}")


def setup_logging():
    """Configures logging based on the loaded configuration."""
    log_level_str = config.get('Logging', 'Level', fallback='INFO').upper()
    log_format = config.get('Logging', 'Format', fallback='%(asctime)s - %(levelname)s - %(message)s')
    log_level = getattr(logging, log_level_str, logging.INFO) # Convert string to logging level
    logging.basicConfig(level=log_level, format=log_format)
    logging.info(f"Logging configured to level {log_level_str}")


def load_data_files():
    """Loads whitelist and node locations using paths from config."""
    global whitelist, node_locations
    whitelist_file = config.get('Files', 'Whitelist')
    nodes_file = config.get('Files', 'Nodes')

    try:
        with open(whitelist_file) as f:
            whitelist = {line.strip().upper() for line in f if line.strip()}
        logging.info(f"Loaded {len(whitelist)} MACs from {whitelist_file}")
    except FileNotFoundError:
        logging.warning(f"Whitelist file not found: {whitelist_file}. Proceeding with empty whitelist.")
        whitelist = set()

    try:
        with open(nodes_file) as f:
            node_locations = json.load(f)
        logging.info(f"Loaded {len(node_locations)} node locations from {nodes_file}")
    except FileNotFoundError:
        logging.warning(f"Nodes file not found: {nodes_file}. Proceeding with empty node locations.")
        node_locations = {}
    except json.JSONDecodeError:
        logging.error(f"Error decoding JSON from {nodes_file}. Proceeding with empty node locations.")
        node_locations = {}


def setup_database():
    """Initializes the SQLite database connection using path from config."""
    global db_conn, db_cursor
    db_path = config.get('Files', 'Database')
    try:
        # Ensure the directory exists
        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir)
            logging.info(f"Created log directory: {db_dir}")

        db_conn = sqlite3.connect(db_path, check_same_thread=False) # Allow access from MQTT thread
        db_cursor = db_conn.cursor()
        db_cursor.execute("""
            CREATE TABLE IF NOT EXISTS detections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mac TEXT NOT NULL, -- Removed UNIQUE constraint to allow historical logging
                node TEXT,
                rssi REAL, -- Store the smoothed RSSI
                timestamp TEXT NOT NULL,
                lat REAL,
                lon REAL
                -- Consider adding raw_rssi if needed later
            )
        """)
        db_conn.commit()
        logging.info(f"Connected to SQLite database: {db_path}")
    except sqlite3.Error as e:
        logging.error(f"Database error connecting to {db_path}: {e}")
        db_conn = None
        db_cursor = None
    except OSError as e:
        logging.error(f"OS error setting up database directory {db_path}: {e}")
        db_conn = None
        db_cursor = None


def cleanup_ema_states():
    """Removes old entries from the EMA state dictionary."""
    global ema_states
    timeout_seconds = config.getint('Filtering', 'StateTimeoutSeconds', fallback=3600)
    now_ts = time.time()
    expired_macs = [
        mac for mac, (value, last_seen) in ema_states.items()
        if now_ts - last_seen > timeout_seconds
    ]
    if expired_macs:
        for mac in expired_macs:
            del ema_states[mac]
        logging.info(f"Cleaned up EMA state for {len(expired_macs)} expired MAC(s).")


def exponential_moving_average(mac, value):
    """Applies EMA smoothing and updates the timestamp."""
    global ema_states
    ema_alpha = config.getfloat('Filtering', 'EMAlpha', fallback=0.6)
    now_ts = time.time() # Use Unix timestamp for comparison

    if mac not in ema_states:
        ema_states[mac] = (value, now_ts)
        smoothed_value = value
    else:
        # Apply EMA formula to the stored value
        current_ema, _ = ema_states[mac]
        smoothed_value = ema_alpha * value + (1 - ema_alpha) * current_ema
        # Update state with new value and timestamp
        ema_states[mac] = (smoothed_value, now_ts)

    return smoothed_value


def log_to_sqlite(mac, node, smoothed_rssi, timestamp_iso, lat, lon):
    """Logs detection data to the SQLite database using the global cursor."""
    # Note: Parameter name changed to timestamp_iso for clarity
    if db_cursor and db_conn:
        try:
            db_cursor.execute("""INSERT INTO detections (mac, node, rssi, timestamp, lat, lon) VALUES (?, ?, ?, ?, ?, ?)""",
                              (mac, node, smoothed_rssi, timestamp_iso, lat, lon))
            # REMOVED: db_conn.commit() - Commit will happen periodically
        except sqlite3.Error as e:
            logging.error(f"Failed to execute insert for MAC {mac} to SQLite: {e}")
    else:
        logging.warning(f"Database connection not available, skipping log for MAC {mac}.")


def on_connect(client, userdata, flags, rc):
    """Callback for when the client connects to MQTT."""
    mqtt_topic = config.get('MQTT', 'Topic', fallback='meshtastic/receive')
    if rc == 0:
        logging.info(f"Connected successfully to MQTT Broker.")
        try:
            client.subscribe(mqtt_topic)
            logging.info(f"Subscribed to topic: {mqtt_topic}")
        except Exception as e:
            logging.error(f"Error subscribing to topic {mqtt_topic}: {e}")
    else:
        logging.error(f"Failed to connect to MQTT Broker, return code {rc}")


# --- Message Processing Logic ---

def parse_mqtt_message(payload_bytes, topic):
    """Parses MQTT message payload, validates, and extracts data."""
    rssi_min_threshold = config.getint('Filtering', 'RSSIMin', fallback=-75)
    try:
        payload = json.loads(payload_bytes.decode())
        mac = payload.get("mac", "").strip().upper()
        node = payload.get("from", "unknown")
        rssi = int(payload.get("rssi", -100)) # Use a default that's likely below threshold

        if not mac:
            logging.debug("Message missing MAC address, skipping.")
            return None
        if rssi < rssi_min_threshold:
            logging.debug(f"Signal from {mac} ({rssi} dBm) below threshold ({rssi_min_threshold} dBm), skipping.")
            return None

        timestamp_dt = datetime.now(timezone.utc)
        timestamp_iso = timestamp_dt.isoformat()

        return {
            "mac": mac,
            "node_id": str(node), # Ensure node ID is string
            "rssi": rssi,
            "timestamp_iso": timestamp_iso
        }

    except json.JSONDecodeError:
        logging.warning(f"Received non-JSON message on {topic}: {payload_bytes[:80]}...")
        return None
    except ValueError:
        # Log the problematic payload for debugging if possible
        logging.warning(f"Could not parse numeric value (likely RSSI) from payload: {payload}")
        return None
    except Exception as e:
        logging.exception(f"Unexpected error parsing message payload: {e}")
        return None


def process_detection(detection_data):
    """Processes parsed data: smoothing, whitelist check, location lookup, logging."""
    global whitelist, node_locations # Access globals

    mac = detection_data["mac"]
    node_id = detection_data["node_id"]
    rssi = detection_data["rssi"]
    timestamp_iso = detection_data["timestamp_iso"]

    # Apply smoothing
    smoothed_rssi = exponential_moving_average(mac, rssi)

    # Check against whitelist
    status = "whitelisted" if mac in whitelist else "unknown"

    # Get node location
    node_info = node_locations.get(node_id, {})
    lat = node_info.get("lat")
    lon = node_info.get("lon")

    # Log to database
    log_to_sqlite(mac, node_id, smoothed_rssi, timestamp_iso, lat, lon)
    logging.info(f"Processed: MAC={mac}, Node={node_id}, RSSI={smoothed_rssi:.1f}, Status={status}, Loc=({lat},{lon})")

    # Return status for alert check
    return status


def trigger_alert_if_needed(mac, node_id, status):
    """Sends an alert if the detection status is 'unknown'."""
    if status == "unknown":
        logging.warning(f"Unknown MAC detected: {mac} from Node {node_id}. Sending alert.")
        try:
            send_alert(mac, node_id) # Assuming send_alert handles its own errors
        except Exception as e:
            logging.error(f"Error calling send_alert for MAC {mac}, Node {node_id}: {e}")


def on_message(client, userdata, msg):
    """Callback for when a message is received from MQTT."""
    global message_counter # Access global counter
    cleanup_interval = 100 # Run cleanup & commit every N messages

    # --- Periodic Cleanup & Commit ---
    message_counter += 1
    if message_counter >= cleanup_interval:
        cleanup_ema_states()
        if db_conn:
            try:
                db_conn.commit()
                logging.debug(f"Committed {message_counter} detection(s) to database.")
            except sqlite3.Error as e:
                logging.error(f"Failed to commit batch to SQLite: {e}")
        message_counter = 0 # Reset counter

    # --- Message Handling Pipeline ---
    try:
        # 1. Parse and Validate
        parsed_data = parse_mqtt_message(msg.payload, msg.topic)
        if not parsed_data:
            return # Skip if parsing failed or data below threshold

        # 2. Process Detection (EMA, Whitelist, Location, Log)
        status = process_detection(parsed_data)

        # 3. Trigger Alert (if needed)
        trigger_alert_if_needed(parsed_data["mac"], parsed_data["node_id"], status)

    except Exception as e:
        # Catch-all for unexpected errors during the processing pipeline
        logging.exception(f"Unexpected error in on_message handler for topic {msg.topic}: {e}")


# --- Main Execution ---

def main():
    """Main execution function."""
    global config # Ensure main uses the global config

    # Load configuration first
    config = load_app_config()
    if not config:
        # load_app_config already logged the error, maybe exit here
        print("Exiting due to configuration load failure.", file=sys.stderr) # Use stderr for errors before logging is set up
        return 1 # Indicate error exit status

    # Setup logging based on config
    setup_logging()

    # Load data files and setup database using config paths
    load_data_files()
    setup_database()

    # Check if database setup failed
    if not db_conn or not db_cursor:
        logging.error("Database setup failed. Cannot proceed.")
        return 1

    # Get MQTT details from config
    mqtt_host = config.get('MQTT', 'Host', fallback='localhost')
    mqtt_port = config.getint('MQTT', 'Port', fallback=1883)

    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message

    try:
        logging.info(f"Attempting to connect to MQTT broker at {mqtt_host}:{mqtt_port}...")
        client.connect(mqtt_host, mqtt_port, 60)
        client.loop_forever()
    except ConnectionRefusedError:
        logging.error(f"MQTT connection refused. Is the broker running at {mqtt_host}:{mqtt_port}?")
    except OSError as e: # Catch potential network errors during connect
        logging.error(f"Network error connecting to MQTT broker: {e}")
    except KeyboardInterrupt:
        logging.info("Script interrupted by user.")
    except Exception as e:
        logging.exception(f"An unexpected error occurred in the main loop: {e}")
    finally:
        logging.info("Shutting down...")
        if client.is_connected():
            logging.info("Disconnecting MQTT client...")
            client.disconnect()
            client.loop_stop() # Ensure loop stops cleanly
        if db_conn:
            try:
                logging.info("Committing final batch before closing...")
                db_conn.commit() # Commit any remaining changes
            except sqlite3.Error as e:
                logging.error(f"Failed to commit final batch to SQLite: {e}")
            finally:
                logging.info("Closing database connection...")
                db_conn.close()
        logging.info("Script finished.")
    return 0 # Indicate successful exit

if __name__ == "__main__":
    import sys
    sys.exit(main()) # Exit with the return code from main()
