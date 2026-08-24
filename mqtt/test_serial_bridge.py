"""Check payload_for's two modes. Run: python -m mqtt.test_serial_bridge"""
import json

from mqtt.serial_bridge import payload_for

NODES = {'!aabb': {'user': {'macaddr': '10:51:DB:29:DC:94'},
                   'position': {'latitude': 34.05, 'longitude': -118.24}}}

# Mode 1: relayed sniffer text message republished verbatim, node DB ignored
relayed = '{"mac":"AA:BB:CC:11:22:33","from":"sensor-01","rssi":-64}'
out = payload_for({'decoded': {'text': relayed}, 'rxRssi': -90}, NODES)
assert json.loads(out)['mac'] == 'AA:BB:CC:11:22:33', out

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
