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

# Sensor events (vehicle/knock/shake): consumed before the MAC pipeline,
# alert when armed, cooldown independent per (node, event type)
monitor.sensor_last_seen.clear()
monitor.correlation_events.clear()             # earlier MAC alerts seeded the buffer
monitor.correlation_last_alert = time.time()   # hold combined alerts during this section
with mock.patch.object(monitor, 'send_alert') as sa:
    assert monitor.handle_sensor_event(b'{"event":"vehicle","from":"gate","mag":123}') is True
    assert sa.call_count == 1 and 'vehicle' in sa.call_args.kwargs['message'].lower()
    monitor.handle_sensor_event(b'{"event":"vehicle","from":"gate","mag":99}')
    assert sa.call_count == 1                      # cooldown suppresses
    monitor.event_last_alerts[('gate', 'vehicle', 'detected')] = time.time() - 301
    monitor.handle_sensor_event(b'{"event":"vehicle","from":"gate","mag":99}')
    assert sa.call_count == 2                      # cooldown expiry re-alerts
    assert 'gate' in monitor.sensor_last_seen      # counts for the sensor watchdog
    assert monitor.handle_sensor_event(b'{"mac":"aa"}') is False   # not a sensor event
    assert monitor.handle_sensor_event(b'{"event":"nope"}') is False  # unknown type
    assert monitor.handle_sensor_event(b'not json') is False

    # knock and shake alert with distinct messages; cooldowns don't cross types
    assert monitor.handle_sensor_event(b'{"event":"knock","from":"fence-e","peak":812}') is True
    assert sa.call_count == 3 and 'knock' in sa.call_args.kwargs['message'].lower()
    assert monitor.handle_sensor_event(b'{"event":"shake","from":"fence-e","hits":9}') is True
    assert sa.call_count == 4 and 'shaking' in sa.call_args.kwargs['message'].lower()
    monitor.handle_sensor_event(b'{"event":"knock","from":"fence-e","peak":500}')
    monitor.handle_sensor_event(b'{"event":"shake","from":"fence-e","hits":5}')
    assert sa.call_count == 4                      # both in their own cooldown

    # v1 contact events (Detection Sensor via bridge) alert with their own cooldown
    assert monitor.handle_sensor_event(b'{"v":1,"type":"contact","node":"back-gate","sensor":"gpio","event":"trigger"}') is True
    assert sa.call_count == 5 and 'contact' in sa.call_args.kwargs['message'].lower()
monitor.manual_armed = False
monitor.manual_armed_ts = time.time()
with mock.patch.object(monitor, 'send_alert') as sa:
    monitor.handle_sensor_event(b'{"event":"vehicle","from":"gate","mag":5}')
    monitor.handle_sensor_event(b'{"event":"shake","from":"fence-e","hits":9}')
    assert sa.call_count == 0                      # disarmed suppresses
monitor.manual_armed = None
monitor.event_last_alerts.clear()

# Correlation: distinct sensor types inside the window escalate once
monitor.config.set('Correlation', 'CorrelationWindowSeconds', '120')
monitor.config.set('Correlation', 'CorrelationMinTypes', '2')
monitor.config.set('Correlation', 'CorrelationCooldownSeconds', '600')
monitor.correlation_events.clear()
monitor.correlation_last_alert = 0.0
with mock.patch.object(monitor, 'send_alert') as sa:
    monitor.correlation_note('vehicle', 'gate', 'detected')
    assert sa.call_count == 0                     # one type: nothing
    monitor.correlation_note('vehicle', 'gate', 'detected')
    assert sa.call_count == 0                     # same type twice != 2 types
    monitor.correlation_note('vibration', 'fence-e', 'shake')
    assert sa.call_count == 1                     # two distinct types: escalate
    assert 'HIGH CONFIDENCE' in sa.call_args.kwargs['message']
    assert 'gate' in sa.call_args.kwargs['message'] and 'fence-e' in sa.call_args.kwargs['message']
    monitor.correlation_note('contact', 'back-gate', 'trigger')
    assert sa.call_count == 1                     # correlation cooldown holds
    # Events outside the window don't count
    monitor.correlation_events[:] = [(time.time() - 121, 'vehicle', 'gate', 'detected')]
    monitor.correlation_last_alert = 0.0
    monitor.correlation_note('vibration', 'fence-e', 'shake')
    assert sa.call_count == 1                     # stale vehicle event expired

# Alertable sensor events feed correlation even when their own cooldown mutes them
monitor.correlation_events.clear()
monitor.correlation_last_alert = 0.0
monitor.event_last_alerts.clear()
with mock.patch.object(monitor, 'send_alert') as sa:
    monitor.handle_sensor_event(b'{"event":"vehicle","from":"gate","mag":50}')
    monitor.handle_sensor_event(b'{"event":"vehicle","from":"gate","mag":60}')  # muted by cooldown
    monitor.handle_sensor_event(b'{"event":"shake","from":"fence-e","hits":6}')
    combined = [c for c in sa.call_args_list if 'HIGH CONFIDENCE' in c.kwargs.get('message', '')]
    assert len(combined) == 1, sa.call_args_list
monitor.correlation_events.clear()
monitor.event_last_alerts.clear()

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

    # Events table: canonical events land as rows, meta round-trips, pruning applies
    monitor.handle_sensor_event(b'{"event":"knock","from":"fence-e","peak":700}')
    row = monitor.db_cursor.execute(
        "SELECT node, type, sensor, event, value, meta FROM events "
        "WHERE type='vibration' ORDER BY id DESC LIMIT 1").fetchone()
    assert row[:5] == ('fence-e', 'vibration', 'piezo', 'knock', 700.0), row
    monitor.db_cursor.execute("UPDATE events SET ts = ? WHERE type='vibration'",
                              ((datetime.now(timezone.utc) - timedelta(days=10)).isoformat(),))
    monitor.prune_old_detections()
    assert monitor.db_cursor.execute(
        "SELECT COUNT(*) FROM events WHERE type='vibration'").fetchone()[0] == 0

    # MAC detections also produce a wireless_presence event row with meta
    monitor.process_detection(dict(parsed, lat=None, lon=None))
    row = monitor.db_cursor.execute(
        "SELECT node, event, meta FROM events WHERE type='wireless_presence' "
        "ORDER BY id DESC LIMIT 1").fetchone()
    assert row[0] == 'node01' and row[1] == 'detected' and 'DE:AD:BE:EF:00:01' in row[2], row
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
