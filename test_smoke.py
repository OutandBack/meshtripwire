"""Smoke test: run with `venv/bin/python test_smoke.py` from the project root."""
import json
import time
from unittest import mock

import mqtt.mac_alert_monitor as monitor

# Config parses (inline comments included) and required keys resolve
monitor.load_app_config()
assert monitor.config.getint('Filtering', 'StateTimeoutSeconds') == 3600
assert monitor.config.getint('Filtering', 'AlertCooldownSeconds') == 300

# flows.json is valid JSON
json.load(open('node-red/flows.json'))

# Message pipeline: parse -> process -> alert (dispatch mocked, DB skipped)
monitor.whitelist = {'AA:BB:CC:DD:EE:FF'}
payload = json.dumps({'mac': 'de:ad:be:ef:00:01', 'from': 'node01', 'rssi': -60}).encode()
parsed = monitor.parse_mqtt_message(payload, 'meshtastic/receive')
assert parsed and parsed['mac'] == 'DE:AD:BE:EF:00:01'
assert monitor.parse_mqtt_message(json.dumps({'mac': 'x', 'rssi': -99}).encode(), 't') is None  # below RSSIMin

assert monitor.process_detection(parsed) == 'unknown'
whitelisted = dict(parsed, mac='AA:BB:CC:DD:EE:FF')
assert monitor.process_detection(whitelisted) == 'whitelisted'

# GPS: payload fix wins, static nodes.json is the fallback
monitor.node_locations = {'node01': {'lat': 1.0, 'lon': 2.0}}
with mock.patch.object(monitor, 'log_to_sqlite') as logged:
    monitor.process_detection(dict(parsed, lat=9.9, lon=8.8))
    assert logged.call_args[0][4:6] == (9.9, 8.8)
    monitor.process_detection(dict(parsed, lat=None, lon=None))
    assert logged.call_args[0][4:6] == (1.0, 2.0)

# Alert fires once, then cooldown suppresses; whitelisted never alerts
with mock.patch.object(monitor, 'send_alert') as sa:
    monitor.trigger_alert_if_needed('DE:AD:BE:EF:00:01', 'node01', 'unknown')
    monitor.trigger_alert_if_needed('DE:AD:BE:EF:00:01', 'node01', 'unknown')
    monitor.trigger_alert_if_needed('AA:BB:CC:DD:EE:FF', 'node01', 'whitelisted')
    assert sa.call_count == 1
    # Cooldown expiry re-alerts
    monitor.last_alert_times['DE:AD:BE:EF:00:01'] = time.time() - 301
    monitor.trigger_alert_if_needed('DE:AD:BE:EF:00:01', 'node01', 'unknown')
    assert sa.call_count == 2

print('smoke test OK')
