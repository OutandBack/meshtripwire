"""Canonical event schema for meshtripwire v0.2.

Every accepted wire format normalizes to one canonical dict; storage,
alerting, and correlation consume only canonical events. MAC sightings are
NOT normalized here -- the monitor's EMA/whitelist/dwell pipeline owns them
and builds its canonical event after those checks.
"""
from datetime import datetime, timezone

# (type, event) -> alerting behavior. Adding a sensor type = adding a row.
TYPE_REGISTRY = {
    ("vehicle", "detected"): {
        "cooldown_key": "VehicleAlertCooldownSeconds", "cooldown_default": 300,
        "template": "ALERT: Vehicle detected by node {node} (magnitude {val}).",
        "alertable": True},
    ("vibration", "knock"): {
        "cooldown_key": "KnockAlertCooldownSeconds", "cooldown_default": 300,
        "template": "ALERT: Impact/knock detected by node {node} (peak {val}).",
        "alertable": True},
    ("vibration", "shake"): {
        "cooldown_key": "ShakeAlertCooldownSeconds", "cooldown_default": 120,
        "template": "ALERT: Sustained shaking/climbing at node {node} ({val} hits).",
        "alertable": True},
    ("contact", "trigger"): {
        "cooldown_key": "ContactAlertCooldownSeconds", "cooldown_default": 60,
        "template": "ALERT: Contact sensor triggered at node {node}.",
        "alertable": True},
    ("wireless_presence", "detected"): {
        "cooldown_key": "AlertCooldownSeconds", "cooldown_default": 300,
        "template": "ALERT: Unknown MAC detected by node {node}.",
        "alertable": True},
    ("drone", "detected"): {
        "cooldown_key": "DroneAlertCooldownSeconds", "cooldown_default": 300,
        "template": "ALERT: Drone Remote ID broadcast near node {node} ({val} dBm).",
        "alertable": True},
    # Informational: logged so vibration alerts can be thunder-labeled; never alerts
    ("weather", "lightning"): {
        "cooldown_key": "AlertCooldownSeconds", "cooldown_default": 300,
        "template": "Lightning detected by node {node} ({val} km).",
        "alertable": False},
}

# Legacy event-JSON name -> (type, event, sensor, value field)
_LEGACY_EVENTS = {
    "vehicle": ("vehicle", "detected", "qmc5883l", "mag"),
    "knock": ("vibration", "knock", "piezo", "peak"),
    "shake": ("vibration", "shake", "piezo", "hits"),
    "lightning": ("weather", "lightning", "as3935", "km"),
    "drone": ("drone", "detected", "remoteid", "rssi"),
}

_CANONICAL_KEYS = {"v", "type", "node", "sensor", "event", "value"}


def canonical(type_, node, event, sensor=None, value=None, meta=None):
    """Build a canonical event dict (also used by the monitor for MAC events)."""
    return {"type": type_, "node": str(node), "sensor": sensor, "event": event,
            "value": value, "ts": datetime.now(timezone.utc).isoformat(),
            "meta": meta or {}}


def normalize(payload):
    """Map any accepted wire payload to a canonical event, or None."""
    if not isinstance(payload, dict):
        return None
    if payload.get("v") == 1 and "type" in payload:
        type_, event = payload.get("type"), payload.get("event")
        if (type_, event) not in TYPE_REGISTRY:
            return None
        meta = {k: v for k, v in payload.items() if k not in _CANONICAL_KEYS}
        return canonical(type_, payload.get("node", "unknown"), event,
                         payload.get("sensor"), payload.get("value"), meta)
    legacy = _LEGACY_EVENTS.get(payload.get("event"))
    if legacy:
        type_, event, sensor, field = legacy
        return canonical(type_, payload.get("from", "unknown"), event, sensor,
                         payload.get(field, 0))
    return None
