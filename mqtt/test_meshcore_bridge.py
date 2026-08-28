"""Check payload_from_text's mapping. Run: python -m mqtt.test_meshcore_bridge"""
import json

from mqtt.meshcore_bridge import payload_from_text

# MeshCore channel messages carry no sender id, so the sensor name rides in the
# text as a "node:" prefix (the firmware prepends NODE_ID).
out = payload_from_text('gate:V,123')
assert json.loads(out) == {'event': 'vehicle', 'from': 'gate', 'mag': 123}, out
out = payload_from_text('fence-e:K,812')
assert json.loads(out) == {'event': 'knock', 'from': 'fence-e', 'peak': 812}, out
out = payload_from_text('fence-e:S,9')
assert json.loads(out) == {'event': 'shake', 'from': 'fence-e', 'hits': 9}, out

# Compact MAC sightings work the same way
out = payload_from_text('gate:AABBCC112233,-64')
assert json.loads(out) == {'mac': 'AA:BB:CC:11:22:33', 'from': 'gate', 'rssi': -64}, out

# sensor_map remaps the prefix name
out = payload_from_text('!a1b2:V,55', sensor_map={'!a1b2': 'driveway'})
assert json.loads(out)['from'] == 'driveway', out

# Chatter on the channel is not sensor data
assert payload_from_text('hello mesh') is None          # no prefix
assert payload_from_text('gate:') is None               # empty line
assert payload_from_text('gate:not a line') is None     # unparseable line
assert payload_from_text('12:34') is None               # not a sensor line either
assert payload_from_text('') is None

print('meshcore_bridge test OK')
