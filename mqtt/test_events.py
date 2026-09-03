"""Check normalize() and the type registry. Run: python -m mqtt.test_events"""
from mqtt.events import normalize, TYPE_REGISTRY

# v1 JSON passes through; unknown keys land in meta
ev = normalize({"v": 1, "type": "contact", "node": "back-gate", "sensor": "gpio",
                "event": "trigger", "battery_mv": 3872})
assert ev["type"] == "contact" and ev["node"] == "back-gate"
assert ev["event"] == "trigger" and ev["meta"] == {"battery_mv": 3872}
assert ev["value"] is None and ev["ts"]

# Current sensor-event JSON maps to canonical types
ev = normalize({"event": "vehicle", "from": "gate", "mag": 123})
assert (ev["type"], ev["event"], ev["sensor"], ev["value"]) == ("vehicle", "detected", "qmc5883l", 123)
ev = normalize({"event": "knock", "from": "fence-e", "peak": 812})
assert (ev["type"], ev["event"], ev["sensor"], ev["value"]) == ("vibration", "knock", "piezo", 812)
ev = normalize({"event": "shake", "from": "fence-e", "hits": 9})
assert (ev["type"], ev["event"], ev["value"]) == ("vibration", "shake", 9)

ev = normalize({"event": "lightning", "from": "gate", "km": 12})
assert (ev["type"], ev["event"], ev["sensor"], ev["value"]) == ("weather", "lightning", "as3935", 12)

# Not events: MAC sightings (handled by the MAC pipeline), junk, unknown types
assert normalize({"mac": "AA:BB", "from": "n", "rssi": -60}) is None
assert normalize({"event": "nope", "from": "x"}) is None
assert normalize({"v": 1, "type": "warp-core", "node": "x", "event": "breach"}) is None
assert normalize("not a dict") is None

# Registry covers every alertable event with template + cooldown
for key in [("vehicle", "detected"), ("vibration", "knock"), ("vibration", "shake"),
            ("contact", "trigger"), ("wireless_presence", "detected")]:
    assert key in TYPE_REGISTRY, key
assert TYPE_REGISTRY[("contact", "trigger")]["alertable"] is True
# Lightning is informational: logged for thunder-labeling, never alerts itself
assert TYPE_REGISTRY[("weather", "lightning")]["alertable"] is False
assert TYPE_REGISTRY[("contact", "trigger")]["cooldown_key"] == "ContactAlertCooldownSeconds"
assert "{node}" in TYPE_REGISTRY[("vehicle", "detected")]["template"]

print('events test OK')
