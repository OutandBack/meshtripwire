"""Smoke test: run with `venv/bin/python test_smoke.py` from the project root."""
import json
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from unittest import mock

import mqtt.mac_alert_monitor as monitor
import notifications.alert_dispatch as dispatch

# Config parses (inline comments included) and required keys resolve
monitor.load_app_config()
monitor.setup_logging()  # would raise if interpolation mangled the %(asctime)s format
assert monitor.config.getint('Filtering', 'StateTimeoutSeconds') == 3600
assert monitor.config.getint('Filtering', 'AlertCooldownSeconds') == 300

# flows.json is valid JSON
json.load(open('node-red/flows.json'))

# Message pipeline: parse -> process -> alert (dispatch mocked, DB skipped)
monitor.whitelist = {'AA:BB:CC:DD:EE:FF'}
payload = json.dumps({'mac': 'de:ad:be:ef:00:01', 'from': 'node01', 'rssi': -60}).encode()
parsed = monitor.parse_mqtt_message(payload, 'meshtastic/receive')
assert parsed and parsed['mac'] == 'DE:AD:BE:EF:00:01'
too_weak = monitor.config.getint('Filtering', 'RSSIMin') - 1
assert monitor.parse_mqtt_message(json.dumps({'mac': 'x', 'rssi': too_weak}).encode(), 't') is None  # below RSSIMin

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

# Dwell-time: with DwellSeconds set, a first sighting defers; alert once dwelled
monitor.last_alert_times.clear()
monitor.dwell_states.clear()
monitor.config.set('Filtering', 'DwellSeconds', '300')
with mock.patch.object(monitor, 'send_alert') as sa:
    monitor.trigger_alert_if_needed('DW:E1:00:00:00:01', 'node01', 'unknown')
    assert sa.call_count == 0  # just arrived, hasn't dwelled
    first, _ = monitor.dwell_states['DW:E1:00:00:00:01']
    monitor.dwell_states['DW:E1:00:00:00:01'] = (first - 301, time.time())  # pretend it's lingered
    monitor.trigger_alert_if_needed('DW:E1:00:00:00:01', 'node01', 'unknown')
    assert sa.call_count == 1
monitor.config.set('Filtering', 'DwellSeconds', '0')

# Arming: disarmed suppresses; schedule window gates; manual override wins
monitor.last_alert_times.clear()
monitor.dwell_states.clear()
monitor.manual_armed = False
monitor.manual_armed_ts = time.time()
with mock.patch.object(monitor, 'send_alert') as sa:
    monitor.trigger_alert_if_needed('AR:00:00:00:00:01', 'node01', 'unknown')
    assert sa.call_count == 0  # disarmed
monitor.manual_armed = None
assert monitor._in_arm_window('22:00-06:00', datetime(2026, 1, 1, 23, 0)) is True   # inside wrap
assert monitor._in_arm_window('22:00-06:00', datetime(2026, 1, 1, 12, 0)) is False  # outside wrap
assert monitor._in_arm_window('09:00-17:00', datetime(2026, 1, 1, 12, 0)) is True   # normal window
monitor.config.set('Arming', 'Schedule', '')
assert monitor.is_armed() is True  # empty schedule = always armed

# Control override auto-expires (fail-safe: a stray disarm can't persist forever)
monitor.config.set('Arming', 'ControlOverrideTTL', '3600')
monitor.handle_control(b'disarmed')
assert monitor.is_armed() is False
monitor.manual_armed_ts = time.time() - 3601  # pretend the override aged out
assert monitor.is_armed() is True              # reverted to schedule (always armed)
assert monitor.manual_armed is None

# Control secret: bad/absent secret rejected, correct secret accepted
monitor.config.set('Arming', 'ControlSecret', 's3cret')
monitor.manual_armed = None
monitor.handle_control(b'disarmed')                                  # bare cmd, secret set -> rejected
assert monitor.manual_armed is None
monitor.handle_control(b'{"cmd":"disarmed","secret":"wrong"}')       # wrong secret -> rejected
assert monitor.manual_armed is None
monitor.handle_control(b'{"cmd":"disarmed","secret":"s3cret"}')      # correct -> applied
assert monitor.manual_armed is False
monitor.config.set('Arming', 'ControlSecret', '')
monitor.manual_armed = None

# Vehicle events: consumed before the MAC pipeline, alert when armed, own cooldown
monitor.sensor_last_seen.clear()
with mock.patch.object(monitor, 'send_alert') as sa:
    assert monitor.handle_vehicle_event(b'{"event":"vehicle","from":"gate","mag":123}') is True
    assert sa.call_count == 1 and 'vehicle' in sa.call_args.kwargs['message'].lower()
    monitor.handle_vehicle_event(b'{"event":"vehicle","from":"gate","mag":99}')
    assert sa.call_count == 1                      # cooldown suppresses
    monitor.vehicle_last_alerts['gate'] = time.time() - 301
    monitor.handle_vehicle_event(b'{"event":"vehicle","from":"gate","mag":99}')
    assert sa.call_count == 2                      # cooldown expiry re-alerts
    assert 'gate' in monitor.sensor_last_seen      # counts for the sensor watchdog
    assert monitor.handle_vehicle_event(b'{"mac":"aa"}') is False  # not a vehicle event
    assert monitor.handle_vehicle_event(b'not json') is False
monitor.manual_armed = False
monitor.manual_armed_ts = time.time()
with mock.patch.object(monitor, 'send_alert') as sa:
    monitor.handle_vehicle_event(b'{"event":"vehicle","from":"gate","mag":5}')
    assert sa.call_count == 0                      # disarmed suppresses
monitor.manual_armed = None
monitor.vehicle_last_alerts.clear()

# Sensor watchdog: expected sensor silent -> one offline alert, then back-online clears it
monitor.config.set('Sensors', 'ExpectedSensors', 'gate')
monitor.config.set('Sensors', 'SensorTimeoutSeconds', '900')
monitor.sensor_offline.clear()
monitor.sensor_last_seen.clear()
with mock.patch.object(monitor, 'send_alert') as sa:
    monitor.check_sensors()                      # never seen -> offline
    assert sa.call_count == 1 and 'gate' in monitor.sensor_offline
    monitor.check_sensors()                       # still offline -> no repeat
    assert sa.call_count == 1
    monitor.note_sensor_seen('gate')              # heartbeat/detection arrives
    monitor.check_sensors()
    assert 'gate' not in monitor.sensor_offline
monitor.config.set('Sensors', 'ExpectedSensors', '')

# Whitelist hot-reload: edits on disk apply without a restart
with tempfile.TemporaryDirectory() as tmp:
    wl_path = os.path.join(tmp, 'whitelist.txt')
    open(wl_path, 'w').write('11:11:11:11:11:11\n')
    monitor.config.set('Files', 'Whitelist', wl_path)
    monitor.load_whitelist()
    assert monitor.whitelist == {'11:11:11:11:11:11'}
    open(wl_path, 'w').write('22:22:22:22:22:22\n')
    os.utime(wl_path, (time.time() + 5,) * 2)  # force a distinct mtime
    monitor.maybe_reload_whitelist()
    assert monitor.whitelist == {'22:22:22:22:22:22'}

    # Retention pruning: old rows deleted, recent rows kept, 0 = keep forever
    monitor.config.set('Files', 'Database', os.path.join(tmp, 'test.db'))
    monitor.setup_database()
    old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    monitor.log_to_sqlite('OL:D0:00:00:00:00', 'node01', -60, old_ts, None, None)
    monitor.log_to_sqlite('NE:W0:00:00:00:00', 'node01', -60, datetime.now(timezone.utc).isoformat(), None, None)
    monitor.db_conn.commit()
    def count():
        return monitor.db_cursor.execute('SELECT COUNT(*) FROM detections').fetchone()[0]
    monitor.config.set('Files', 'RetentionDays', '0')
    monitor.prune_old_detections()
    assert count() == 2
    monitor.config.set('Files', 'RetentionDays', '7')
    monitor.prune_old_detections()
    assert count() == 1
    monitor.db_conn.close()
    monitor.db_conn = monitor.db_cursor = None

# MQTT alert output: publishes the JSON alert to the broker when EnableMqtt is on
for ch in ('EnableNtfy', 'EnableWebhook', 'EnableTwilio'):
    monitor.config.set('Notifications', ch, 'false')  # isolate the MQTT path
monitor.config.set('Notifications', 'EnableMqtt', 'true')
monitor.config.set('Notifications', 'MqttAlertTopic', 'meshtripwire/alerts')
with mock.patch('paho.mqtt.publish.single') as single:
    dispatch.send_alert(monitor.config, 'DE:AD:BE:EF:00:01', 'node01')
    assert single.call_count == 1
    assert single.call_args.args[0] == 'meshtripwire/alerts'
    published = json.loads(single.call_args.args[1])
    assert published['mac'] == 'DE:AD:BE:EF:00:01'
    assert published['node'] == 'node01'
    assert 'ALERT' in published['message']
# Off by default: existing installs don't publish
monitor.config.set('Notifications', 'EnableMqtt', 'false')
with mock.patch('paho.mqtt.publish.single') as single:
    dispatch.send_alert(monitor.config, 'DE:AD:BE:EF:00:01', 'node01')
    assert single.call_count == 0

print('smoke test OK')
