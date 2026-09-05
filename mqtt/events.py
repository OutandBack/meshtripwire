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
    ("vibration", "glass"): {
        "cooldown_key": "GlassAlertCooldownSeconds", "cooldown_default": 300,
        "template": "ALERT: Glass break detected at node {node} (peak {val}).",
        "alertable": True},
    ("vehicle", "dark"): {
        "cooldown_key": "DarkVehicleAlertCooldownSeconds", "cooldown_default": 300,
        "template": "ALERT: Dark vehicle at node {node}: no wireless device seen within {val}s.",
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
    # RF-attack class: things done TO the sensor net, not near it
    ("attack", "deauth"): {
        "cooldown_key": "AttackAlertCooldownSeconds", "cooldown_default": 120,
        "template": "ALERT: WiFi deauth attack near node {node} ({val} frames).",
        "alertable": True},
    ("attack", "rogue_ap"): {
        "cooldown_key": "AttackAlertCooldownSeconds", "cooldown_default": 120,
        "template": "ALERT: Rogue AP broadcasting the protected SSID near node {node} ({val} dBm).",
        "alertable": True},
    ("attack", "silence"): {
        "cooldown_key": "AttackAlertCooldownSeconds", "cooldown_default": 120,
        "template": "ALERT: RF silence at node {node} ({val}s without frames) - possible jamming.",
        "alertable": True},
    ("attack", "blackout"): {
        "cooldown_key": "AttackAlertCooldownSeconds", "cooldown_default": 120,
        "template": "ALERT: {val} sensors offline simultaneously - possible jamming or power loss.",
        "alertable": True},
    ("tracker", "detected"): {
        "cooldown_key": "TrackerAlertCooldownSeconds", "cooldown_default": 300,
        "template": "ALERT: BLE tracker (AirTag/Tile-style) near node {node} ({val} dBm).",
        "alertable": True},
    ("asset", "missing"): {
        "cooldown_key": "AssetAlertCooldownSeconds", "cooldown_default": 3600,
        "template": "ALERT: Watched asset '{node}' not seen for {val} minutes.",
        "alertable": True},
    ("casing", "detected"): {
        "cooldown_key": "CasingAlertCooldownSeconds", "cooldown_default": 86400,
        "template": "ALERT: Repeat visitor at node {node}: {mac} seen on {val} different days.",
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
    "deauth": ("attack", "deauth", "esp32", "count"),
    "rogue_ap": ("attack", "rogue_ap", "esp32", "rssi"),
    "silence": ("attack", "silence", "esp32", "seconds"),
    "tracker": ("tracker", "detected", "ble", "rssi"),
    "glass": ("vibration", "glass", "piezo", "peak"),
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
