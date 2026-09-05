"""Check payload_for's modes. Run: python -m mqtt.test_serial_bridge"""
import json

from mqtt.serial_bridge import parse_compact, payload_for

NODES = {'!aabb': {'user': {'macaddr': '10:51:DB:29:DC:94'},
                   'position': {'latitude': 34.05, 'longitude': -118.24}}}

# Mode 1a: relayed JSON sighting, node DB ignored
relayed = '{"mac":"AA:BB:CC:11:22:33","from":"sensor-01","rssi":-64}'
out = payload_for({'decoded': {'text': relayed}, 'rxRssi': -90}, NODES)
assert json.loads(out)['mac'] == 'AA:BB:CC:11:22:33', out

# Mode 1b: compact "AABBCC112233,-64" -> reconstruct MAC, map relay node to name
assert parse_compact('AABBCC112233,-64') == ('AA:BB:CC:11:22:33', -64)
assert parse_compact('bad') is None
assert parse_compact('AABBCC112233,notint') is None
out = payload_for({'decoded': {'text': 'AABBCC112233,-64'}, 'fromId': '!aabb'},
                  NODES, sensor_map={'!aabb': 'gate'})
assert json.loads(out) == {'mac': 'AA:BB:CC:11:22:33', 'from': 'gate', 'rssi': -64}, out
# No sensor_map entry -> fall back to the raw relay node id
out = payload_for({'decoded': {'text': 'AABBCC112233,-64'}, 'fromId': '!zzzz'}, NODES)
assert json.loads(out)['from'] == '!zzzz', out

# Mode 1c: compact sensor events -> event JSON
# "V,123" vehicle (QMC5883L), "K,812" knock peak, "S,9" shake hit count (piezo)
out = payload_for({'decoded': {'text': 'V,123'}, 'fromId': '!aabb'},
                  NODES, sensor_map={'!aabb': 'gate'})
assert json.loads(out) == {'event': 'vehicle', 'from': 'gate', 'mag': 123}, out
out = payload_for({'decoded': {'text': 'K,812'}, 'fromId': '!aabb'},
                  NODES, sensor_map={'!aabb': 'fence-e'})
assert json.loads(out) == {'event': 'knock', 'from': 'fence-e', 'peak': 812}, out
out = payload_for({'decoded': {'text': 'S,9'}, 'fromId': '!aabb'},
                  NODES, sensor_map={'!aabb': 'fence-e'})
assert json.loads(out) == {'event': 'shake', 'from': 'fence-e', 'hits': 9}, out
out = payload_for({'decoded': {'text': 'L,12'}, 'fromId': '!aabb'},
                  NODES, sensor_map={'!aabb': 'gate'})
assert json.loads(out) == {'event': 'lightning', 'from': 'gate', 'km': 12}, out
out = payload_for({'decoded': {'text': 'D,-58'}, 'fromId': '!aabb'},
                  NODES, sensor_map={'!aabb': 'gate'})
assert json.loads(out) == {'event': 'drone', 'from': 'gate', 'rssi': -58}, out
for line, expect in [('A,47', {'event': 'deauth', 'from': 'gate', 'count': 47}),
                     ('R,-44', {'event': 'rogue_ap', 'from': 'gate', 'rssi': -44}),
                     ('Q,180', {'event': 'silence', 'from': 'gate', 'seconds': 180}),
                     ('T,-60', {'event': 'tracker', 'from': 'gate', 'rssi': -60}),
                     ('G,1900', {'event': 'glass', 'from': 'gate', 'peak': 1900})]:
    out = payload_for({'decoded': {'text': line}, 'fromId': '!aabb'},
                      NODES, sensor_map={'!aabb': 'gate'})
    assert json.loads(out) == expect, (line, out)
# Malformed value is not an event (falls through; no rssi -> skip)
assert payload_for({'decoded': {'text': 'V,notint'}}, NODES) is None
assert payload_for({'decoded': {'text': 'K,'}, 'fromId': '!aabb'}, NODES) is None

# Mode 1d: Meshtastic Detection Sensor module packet -> v1 contact event
out = payload_for({'decoded': {'portnum': 'DETECTION_SENSOR_APP', 'text': 'Back Gate'},
                   'fromId': '!aabb', 'rxRssi': -70}, NODES, sensor_map={'!aabb': 'back-gate'})
d = json.loads(out)
assert (d['v'], d['type'], d['event'], d['node']) == (1, 'contact', 'trigger', 'back-gate'), d
assert d['text'] == 'Back Gate'
# Without a sensor_map entry, the module's own text names the sensor
out = payload_for({'decoded': {'portnum': 'DETECTION_SENSOR_APP', 'text': 'Shed PIR'},
                   'fromId': '!zzzz'}, NODES)
assert json.loads(out)['node'] == 'Shed PIR'

# Mode 2: no text -> synthesize from the transmitting node's own MAC + GPS
out = payload_for({'fromId': '!aabb', 'rxRssi': -70}, NODES)
d = json.loads(out)
assert d == {'mac': '10:51:DB:29:DC:94', 'from': '!aabb', 'rssi': -70,
             'lat': 34.05, 'lon': -118.24}, d

# Non-JSON text falls through to mode 2 (here: unknown node, no rssi -> skip)
assert payload_for({'decoded': {'text': 'hello mesh'}}, NODES) is None
# Text JSON without a mac field is not a sighting -> falls through, skipped
assert payload_for({'decoded': {'text': '{"foo":1}'}}, NODES) is None
# Locally generated packet (no rxRssi, no text) -> skip
assert payload_for({'fromId': '!aabb'}, NODES) is None

print('serial_bridge test OK')
