import paho.mqtt.client as mqtt
import json
import os
import sqlite3
import sys
import logging
import configparser
import threading
import time # Added for timestamp comparison
from datetime import datetime, timedelta, timezone
from notifications.alert_dispatch import send_alert
from mqtt.events import normalize, canonical, TYPE_REGISTRY

# --- Global State ---
config = None # To hold loaded configuration
ema_states = {} # Stores {'mac': (ema_value, last_seen_timestamp)}
message_counter = 0 # Counter for periodic cleanup
whitelist = set()
whitelist_mtime = 0.0
node_locations = {}
last_alert_times = {} # Stores {'mac': last_alert_unix_ts} for alert cooldown
dwell_states = {} # Stores {'mac': (first_seen_ts, last_seen_ts)} for dwell-time alerting
sensor_last_seen = {} # Stores {'node': last_ts} from detections/heartbeats, for the watchdog
sensor_offline = set() # Nodes currently flagged offline (alert once, until they return)
event_last_alerts = {} # Stores {(node, type, event): last_alert_unix_ts} for sensor-event cooldowns
correlation_events = [] # Recent alertable events as (unix_ts, type, node, event) for fusion
correlation_last_alert = 0.0 # last combined-alert time, for CorrelationCooldownSeconds
manual_armed = None # None = follow schedule; True/False = manual arm/disarm override
manual_armed_ts = 0.0 # when the override was set, for ControlOverrideTTL expiry
db_conn = None
db_cursor = None
last_db_commit = 0.0 # for the time-based commit that keeps dashboard reads fresh

# --- Configuration Loading ---
def load_app_config(config_path='config/config.ini'):
    """Loads configuration from INI file."""
    global config
    # interpolation=None: the Logging Format value contains %(...)s placeholders
    parser = configparser.ConfigParser(inline_comment_prefixes=('#', ';'), interpolation=None)
    if not os.path.exists(config_path):
        raise SystemExit(f"Configuration file not found: {config_path}. "
                         "Run from the project root or create config/config.ini.")

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
    # force=True: earlier module-level logging calls implicitly installed a default handler
    logging.basicConfig(level=log_level, format=log_format, force=True)
    logging.info(f"Logging configured to level {log_level_str}")


def load_whitelist():
    """Loads the whitelist and records its mtime for hot-reload."""
    global whitelist, whitelist_mtime
    whitelist_file = config.get('Files', 'Whitelist')
    try:
        whitelist_mtime = os.path.getmtime(whitelist_file)
        with open(whitelist_file) as f:
            whitelist = {line.strip().upper() for line in f if line.strip()}
        logging.info(f"Loaded {len(whitelist)} MACs from {whitelist_file}")
    except FileNotFoundError:
        # An empty whitelist would flag every MAC as unknown and flood alerts.
        raise SystemExit(f"Whitelist file not found: {whitelist_file}. "
                         "Create it (one MAC per line) or fix the path in config.ini.")


def maybe_reload_whitelist():
    """Reloads the whitelist if its file changed on disk (one stat per message)."""
    whitelist_file = config.get('Files', 'Whitelist')
    try:
        mtime = os.path.getmtime(whitelist_file)
    except OSError:
        return # File missing mid-run: keep the currently loaded list
    if mtime != whitelist_mtime:
        load_whitelist()


def load_data_files():
    """Loads whitelist and node locations using paths from config."""
    global node_locations
    nodes_file = config.get('Files', 'Nodes')

    load_whitelist()

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
        db_cursor.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL, node TEXT, type TEXT, sensor TEXT,
                event TEXT, value REAL, lat REAL, lon REAL, meta TEXT
            )
        """)
        db_cursor.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL, channel TEXT, target TEXT,
                ok INTEGER, error TEXT, message TEXT
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


def prune_old_detections():
    """Deletes detections older than RetentionDays (0 = keep forever)."""
    days = config.getint('Files', 'RetentionDays', fallback=0)
    if days <= 0 or not db_conn:
        return
    # Timestamps are UTC isoformat, so lexical comparison against a same-format cutoff works
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        db_cursor.execute("DELETE FROM detections WHERE timestamp < ?", (cutoff,))
        if db_cursor.rowcount > 0:
            logging.info(f"Pruned {db_cursor.rowcount} detection(s) older than {days} day(s).")
        db_cursor.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
        db_cursor.execute("DELETE FROM notifications WHERE ts < ?", (cutoff,))
        db_conn.commit()
    except sqlite3.Error as e:
        logging.error(f"Failed to prune old detections: {e}")


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
    # Prune alert-cooldown and dwell state on the same schedule (randomized MACs would grow forever)
    for mac in [m for m, ts in last_alert_times.items() if now_ts - ts > timeout_seconds]:
        del last_alert_times[mac]
    for mac in [m for m, (_first, last) in dwell_states.items() if now_ts - last > timeout_seconds]:
        del dwell_states[mac]


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


def correlation_note(ev_type, node, event_name):
    """Feed one alertable event into the correlation buffer; escalate when
    distinct sensor types cluster inside the window.

    Individual alerts are never held back — correlation is pure escalation.
    """
    global correlation_last_alert
    window = config.getint('Correlation', 'CorrelationWindowSeconds', fallback=120)
    min_types = config.getint('Correlation', 'CorrelationMinTypes', fallback=2)
    cooldown = config.getint('Correlation', 'CorrelationCooldownSeconds', fallback=600)
    now_ts = time.time()
    correlation_events.append((now_ts, ev_type, node, event_name))
    correlation_events[:] = [e for e in correlation_events if now_ts - e[0] < window]
    types = {e[1] for e in correlation_events}
    if len(types) < min_types or now_ts - correlation_last_alert < cooldown:
        return
    correlation_last_alert = now_ts
    lines = [f"  {t} {time.strftime('%H:%M:%S', time.localtime(ts))} {n} ({ev})"
             for ts, t, n, ev in correlation_events]
    message = ("HIGH CONFIDENCE EVENT: multiple sensor types triggered within "
               f"{window}s:\n" + "\n".join(lines))
    logging.warning(message)
    try:
        send_alert(config, "correlated", ",".join(sorted({e[2] for e in correlation_events})),
                   message=message, on_result=record_notification)
    except Exception as e:
        logging.error(f"Error sending correlation alert: {e}")


def log_event(ev):
    """Inserts a canonical event into the events table (commit is periodic)."""
    if not (db_cursor and db_conn):
        logging.warning(f"Database connection not available, skipping event log for {ev['type']}.")
        return
    try:
        db_cursor.execute(
            "INSERT INTO events (ts, node, type, sensor, event, value, lat, lon, meta) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ev["ts"], ev["node"], ev["type"], ev["sensor"], ev["event"],
             float(ev["value"]) if ev["value"] is not None else None,
             ev["meta"].get("lat"), ev["meta"].get("lon"), json.dumps(ev["meta"])))
    except sqlite3.Error as e:
        logging.error(f"Failed to insert event for {ev['node']}: {e}")


def on_connect(client, userdata, flags, reason_code, properties):
    """Callback for when the client connects to MQTT (paho v2 API)."""
    mqtt_topic = config.get('MQTT', 'Topic', fallback='meshtastic/receive')
    if not reason_code.is_failure:
        logging.info("Connected successfully to MQTT Broker.")
        topics = [mqtt_topic]
        hb = config.get('Sensors', 'HeartbeatTopic', fallback='').strip()
        ctrl = config.get('Arming', 'ControlTopic', fallback='').strip()
        topics += [t for t in (hb, ctrl) if t]
        for t in topics:
            try:
                client.subscribe(t)
                logging.info(f"Subscribed to topic: {t}")
            except Exception as e:
                logging.error(f"Error subscribing to topic {t}: {e}")
    else:
        logging.error(f"Failed to connect to MQTT Broker: {reason_code}")


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
            "timestamp_iso": timestamp_iso,
            # GPS from the node's own fix, when the payload carries one
            "lat": payload.get("lat"),
            "lon": payload.get("lon")
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

    # Use the payload's GPS fix if present, else fall back to static node location
    lat = detection_data.get("lat")
    lon = detection_data.get("lon")
    if lat is None or lon is None:
        node_info = node_locations.get(node_id, {})
        lat = node_info.get("lat")
        lon = node_info.get("lon")

    # Log to database
    log_to_sqlite(mac, node_id, smoothed_rssi, timestamp_iso, lat, lon)
    log_event(canonical("wireless_presence", node_id, "detected",
                        meta={"mac": mac, "rssi": smoothed_rssi, "status": status,
                              "lat": lat, "lon": lon}))
    logging.info(f"Processed: MAC={mac}, Node={node_id}, RSSI={smoothed_rssi:.1f}, Status={status}, Loc=({lat},{lon})")

    # Return status for alert check
    return status


def passes_dwell(mac):
    """True once a MAC has persisted at least DwellSeconds (0 = alert on first sight).

    Filters out devices that merely pass by (a car on the road) versus ones that
    linger (someone on the property). A gap longer than StateTimeoutSeconds resets
    the timer, so a device that leaves and returns is treated as a fresh arrival.
    """
    dwell = config.getint('Filtering', 'DwellSeconds', fallback=0)
    timeout = config.getint('Filtering', 'StateTimeoutSeconds', fallback=3600)
    now = time.time()
    first, last = dwell_states.get(mac, (now, now))
    if now - last > timeout:
        first = now # gone and returned -> new arrival
    dwell_states[mac] = (first, now)
    return now - first >= dwell


def _in_arm_window(schedule, now):
    """schedule is 'HH:MM-HH:MM' (local time); handles windows that wrap midnight."""
    try:
        start_s, end_s = schedule.split('-')
        sh, sm = (int(x) for x in start_s.split(':'))
        eh, em = (int(x) for x in end_s.split(':'))
    except (ValueError, AttributeError):
        logging.warning(f"Invalid Arming Schedule '{schedule}'; treating as always armed.")
        return True
    cur = now.hour * 60 + now.minute
    start, end = sh * 60 + sm, eh * 60 + em
    if start <= end:
        return start <= cur < end
    return cur >= start or cur < end # wraps past midnight (e.g. 22:00-06:00)


def is_armed():
    """Whether alerts should fire now: a (non-expired) manual override wins, else
    the schedule. A manual override auto-reverts after ControlOverrideTTL so a
    stray or malicious 'disarmed' can't silently disable alerts forever."""
    global manual_armed
    if manual_armed is not None:
        ttl = config.getint('Arming', 'ControlOverrideTTL', fallback=3600)
        if ttl > 0 and time.time() - manual_armed_ts > ttl:
            logging.info("Arming override expired; reverting to schedule.")
            manual_armed = None
        else:
            return manual_armed
    schedule = config.get('Arming', 'Schedule', fallback='').strip()
    if not schedule:
        return True
    return _in_arm_window(schedule, datetime.now())


def note_sensor_seen(node):
    """Record that a sensor node is alive (from a detection or a heartbeat)."""
    sensor_last_seen[node] = time.time()


def check_sensors():
    """Watchdog: alert if any expected sensor goes silent past SensorTimeoutSeconds.

    Runs on its own timer, not on message traffic — the whole point is to notice
    silence, which by definition produces no messages.
    """
    expected = [s.strip() for s in config.get('Sensors', 'ExpectedSensors', fallback='').split(',') if s.strip()]
    if not expected:
        return
    timeout = config.getint('Sensors', 'SensorTimeoutSeconds', fallback=900)
    now = time.time()
    for node in expected:
        silent = now - sensor_last_seen.get(node, 0)
        if silent > timeout and node not in sensor_offline:
            sensor_offline.add(node)
            if is_armed():
                logging.warning(f"Sensor '{node}' offline: no data for {int(silent)}s.")
                try:
                    send_alert(config, node, node,
                               message=f"Sensor '{node}' offline: no data for {int(silent)}s.",
                               on_result=record_notification)
                except Exception as e:
                    logging.error(f"Error sending sensor-offline alert for {node}: {e}")
        elif silent <= timeout and node in sensor_offline:
            sensor_offline.discard(node)
            logging.info(f"Sensor '{node}' back online.")


def trigger_alert_if_needed(mac, node_id, status):
    """Sends an alert if the detection status is 'unknown', gated by arming, dwell,
    and a per-MAC cooldown."""
    if status != "unknown":
        return
    if not is_armed():
        logging.debug(f"Disarmed; alert for {mac} suppressed.")
        return
    if not passes_dwell(mac):
        logging.debug(f"Dwell not met for {mac}; alert deferred.")
        return
    correlation_note("wireless_presence", node_id, "detected")
    cooldown = config.getint('Filtering', 'AlertCooldownSeconds', fallback=300)
    now_ts = time.time()
    if now_ts - last_alert_times.get(mac, 0) < cooldown:
        logging.debug(f"Alert for {mac} suppressed (cooldown {cooldown}s).")
        return
    last_alert_times[mac] = now_ts
    logging.warning(f"Unknown MAC detected: {mac} from Node {node_id}. Sending alert.")
    try:
        send_alert(config, mac, node_id, on_result=record_notification) # send_alert handles its own errors
    except Exception as e:
        logging.error(f"Error calling send_alert for MAC {mac}, Node {node_id}: {e}")


def handle_sensor_event(payload_bytes):
    """Consume a sensor-event message (vehicle/vibration/contact, any accepted
    wire format). Returns True if consumed.

    No whitelist/EMA/dwell — these sensors classify on-device and are
    identity-blind. Gated by arming and a per-(node, type, event) cooldown.
    """
    try:
        payload = json.loads(payload_bytes.decode())
    except (ValueError, UnicodeDecodeError):
        return False
    ev = normalize(payload)
    if ev is None or ev["type"] == "wireless_presence":
        return False  # MAC sightings belong to the detection pipeline

    reg = TYPE_REGISTRY[(ev["type"], ev["event"])]
    node, val = ev["node"], ev["value"]
    note_sensor_seen(node)
    log_event(ev)
    logging.info(f"Sensor event: {ev['type']}/{ev['event']} Node={node} value={val}")

    if not reg["alertable"] or not is_armed():
        return True
    correlation_note(ev["type"], node, ev["event"])
    cooldown = config.getint('Filtering', reg["cooldown_key"], fallback=reg["cooldown_default"])
    now_ts = time.time()
    key = (node, ev["type"], ev["event"])
    if now_ts - event_last_alerts.get(key, 0) < cooldown:
        logging.debug(f"{ev['type']} alert from {node} suppressed (cooldown {cooldown}s).")
        return True
    event_last_alerts[key] = now_ts
    message = reg["template"].format(node=node, val=val)
    logging.warning(f"{message} Sending alert.")
    try:
        send_alert(config, ev["type"], node, message=message, on_result=record_notification)
    except Exception as e:
        logging.error(f"Error calling send_alert for {ev['type']} event from {node}: {e}")
    return True


def record_notification(channel, target, ok, error, message):
    """on_result callback for send_alert: one row per channel attempt."""
    if not (db_cursor and db_conn):
        return
    try:
        db_cursor.execute(
            "INSERT INTO notifications (ts, channel, target, ok, error, message) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), channel, target,
             1 if ok else 0, error, message))
    except sqlite3.Error as e:
        logging.error(f"Failed to log notification ({channel}): {e}")


def maybe_commit():
    """Commit at most every 5s, so events reach readers (the dashboard) quickly.

    The per-100-message batch commit alone could leave rows pending for hours
    at a quiet site; this bounds staleness without a commit per message.
    """
    global last_db_commit
    if db_conn and time.time() - last_db_commit > 5:
        try:
            db_conn.commit()
            last_db_commit = time.time()
        except sqlite3.Error as e:
            logging.error(f"Failed time-based commit: {e}")


def on_message(client, userdata, msg):
    """Callback for when a message is received from MQTT."""
    global message_counter # Access global counter
    cleanup_interval = 100 # Run cleanup & commit every N messages

    # --- Periodic Cleanup & Commit ---
    message_counter += 1
    if message_counter >= cleanup_interval:
        cleanup_ema_states()
        prune_old_detections()
        if db_conn:
            try:
                db_conn.commit()
                logging.debug(f"Committed {message_counter} detection(s) to database.")
            except sqlite3.Error as e:
                logging.error(f"Failed to commit batch to SQLite: {e}")
        message_counter = 0 # Reset counter

    # --- Route control/heartbeat topics away from the detection pipeline ---
    try:
        if msg.topic == config.get('Arming', 'ControlTopic', fallback='').strip():
            handle_control(msg.payload)
            return
        if msg.topic == config.get('Sensors', 'HeartbeatTopic', fallback='').strip():
            handle_heartbeat(msg.payload)
            return
    except Exception as e:
        logging.exception(f"Error handling control/heartbeat on {msg.topic}: {e}")
        return

    # --- Message Handling Pipeline ---
    try:
        # Sensor events (vehicle/knock/shake nodes) bypass the MAC pipeline entirely
        if handle_sensor_event(msg.payload):
            maybe_commit()
            return

        # 1. Parse and Validate
        maybe_reload_whitelist()
        parsed_data = parse_mqtt_message(msg.payload, msg.topic)
        if not parsed_data:
            return # Skip if parsing failed or data below threshold

        # A detection is also proof the reporting sensor is alive
        note_sensor_seen(parsed_data["node_id"])

        # 2. Process Detection (EMA, Whitelist, Location, Log)
        status = process_detection(parsed_data)

        # 3. Trigger Alert (if needed)
        trigger_alert_if_needed(parsed_data["mac"], parsed_data["node_id"], status)
        maybe_commit()

    except Exception as e:
        # Catch-all for unexpected errors during the processing pipeline
        logging.exception(f"Unexpected error in on_message handler for topic {msg.topic}: {e}")


def handle_control(payload_bytes):
    """Arm/disarm control message: 'armed', 'disarmed', or 'auto' (follow schedule).

    If ControlSecret is set, the message must be JSON {"cmd": ..., "secret": ...}
    with a matching secret — otherwise anyone able to publish to the broker could
    disarm the system. A bare-string command is accepted only when no secret is set.
    """
    global manual_armed, manual_armed_ts
    raw = payload_bytes.decode(errors='replace').strip()
    secret = config.get('Arming', 'ControlSecret', fallback='').strip()
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        obj = None
    if isinstance(obj, dict):
        if secret and obj.get('secret') != secret:
            logging.warning("Rejected arming command: missing or wrong secret.")
            return
        cmd = str(obj.get('cmd', '')).lower()
    elif secret:
        logging.warning("Rejected unauthenticated arming command (ControlSecret is set).")
        return
    else:
        cmd = raw.lower()

    if cmd == 'armed':
        manual_armed = True
    elif cmd == 'disarmed':
        manual_armed = False
    elif cmd == 'auto':
        manual_armed = None
    else:
        logging.warning(f"Unknown arming command: {cmd!r} (expected armed/disarmed/auto).")
        return
    manual_armed_ts = time.time()
    # Disarming reduces protection — log it loudly so it's visible in the record.
    level = logging.WARNING if cmd == 'disarmed' else logging.INFO
    logging.log(level, f"Arming override set to {cmd} (armed now: {is_armed()}).")


def handle_heartbeat(payload_bytes):
    """Heartbeat message: JSON {'node': id} or a bare node id string."""
    text = payload_bytes.decode(errors='replace').strip()
    node = None
    try:
        obj = json.loads(text)
        node = obj.get('node') if isinstance(obj, dict) else None
    except (ValueError, TypeError):
        node = text or None
    if node:
        note_sensor_seen(str(node))
        logging.debug(f"Heartbeat from sensor '{node}'.")


# --- Main Execution ---

def main():
    """Main execution function."""
    global config # Ensure main uses the global config

    # Load configuration first (raises SystemExit if missing/unreadable)
    config = load_app_config()

    # Setup logging based on config
    setup_logging()

    # Warn if the arm/disarm control path is exposed without a shared secret —
    # on an anonymous broker anyone on the network could then disarm alerting.
    if config.get('Arming', 'ControlTopic', fallback='').strip() and \
            not config.get('Arming', 'ControlSecret', fallback='').strip():
        logging.warning("Arming ControlTopic is set without ControlSecret — anyone who can "
                        "publish to the broker can disarm alerts. Set ControlSecret and/or "
                        "restrict the broker with auth/ACLs.")

    # Load data files and setup database using config paths
    load_data_files()
    setup_database()

    # Check if database setup failed
    if not db_conn or not db_cursor:
        logging.error("Database setup failed. Cannot proceed.")
        return 1

    prune_old_detections()

    # Get MQTT details from config (MQTT_HOST env overrides, for Docker)
    mqtt_host = os.environ.get('MQTT_HOST') or config.get('MQTT', 'Host', fallback='localhost')
    mqtt_port = config.getint('MQTT', 'Port', fallback=1883)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if config.getboolean('MQTT', 'UseTLS', fallback=False):
        # CAFile optional: omit to use the system trust store
        client.tls_set(ca_certs=config.get('MQTT', 'CAFile', fallback=None) or None)
    mqtt_user = config.get('MQTT', 'Username', fallback=None)
    if mqtt_user:
        client.username_pw_set(mqtt_user, config.get('MQTT', 'Password', fallback=None))
    client.on_connect = on_connect
    client.on_message = on_message

    # Sensor watchdog runs on its own timer — silence produces no messages, so it
    # can't be driven by the message loop. Only started if sensors are expected.
    stop_watchdog = threading.Event()
    if config.get('Sensors', 'ExpectedSensors', fallback='').strip():
        timeout = config.getint('Sensors', 'SensorTimeoutSeconds', fallback=900)
        interval = max(30, timeout // 4)

        def watchdog():
            while not stop_watchdog.wait(interval):
                try:
                    check_sensors()
                except Exception as e:
                    logging.error(f"Sensor watchdog error: {e}")

        threading.Thread(target=watchdog, daemon=True).start()
        logging.info(f"Sensor watchdog active (every {interval}s).")

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
        stop_watchdog.set()
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
    sys.exit(main()) # Exit with the return code from main()
