"""Check payload_from_lxmf's mapping. Run: python -m mqtt.test_rns_bridge"""
import json

from mqtt.rns_bridge import payload_from_lxmf

# Prefixed "node:line" text (the field relay adds the prefix)
out = payload_from_lxmf(b'gate:V,123', 'aabbccdd00112233')
assert json.loads(out) == {'event': 'vehicle', 'from': 'gate', 'mag': 123}, out

# Bare compact line: the LXMF source hash names the sensor, sensor_map first
out = payload_from_lxmf(b'K,812', 'aabbccdd00112233', {'aabbccdd00112233': 'fence-e'})
assert json.loads(out) == {'event': 'knock', 'from': 'fence-e', 'peak': 812}, out
out = payload_from_lxmf(b'AABBCC112233,-64', 'aabbccdd00112233')
assert json.loads(out) == {'mac': 'AA:BB:CC:11:22:33', 'from': 'aabbccdd', 'rssi': -64}, out

# Chatter and junk are not sensor data
assert payload_from_lxmf(b'hello from the mesh', 'aabbccdd00112233') is None
assert payload_from_lxmf(b'', 'aabbccdd00112233') is None
assert payload_from_lxmf(b'\xff\xfe\x00', 'aabbccdd00112233') is None

print('rns_bridge test OK')
