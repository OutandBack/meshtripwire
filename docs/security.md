# Arming & Security

## Arming

Alerts fire only while armed. Three layers, from static to live:

1. **Schedule** — `[Arming] Schedule = 22:00-06:00` (local time, wraps
   midnight). Empty = always armed.
2. **Manual override** — publish `armed`, `disarmed`, or `auto` to
   `ControlTopic`. Useful from automations: a phone-presence rule can disarm
   while you're home.
3. **TTL fail-safe** — a manual override auto-reverts to the schedule after
   `ControlOverrideTTL` seconds (default 1 hour), so a stray or malicious
   "disarmed" can never disable alerting forever.

Detections and events are **always logged** regardless of arming — arming
gates notifications, not history.

## The control secret

Anyone who can publish to `ControlTopic` can disarm alerting, and anyone who
can publish to `HeartbeatTopic` can mask a dead sensor. Set `ControlSecret`
and control messages must be `{"cmd":"disarmed","secret":"..."}` — bare
commands and wrong secrets are rejected and logged.

## Broker hardening

The bundled `setup/mosquitto.conf` allows anonymous LAN publishes so sensors
work out of the box. Treat that as a bootstrap default:

- add broker auth (`[MQTT] Username`/`Password`) and per-topic ACLs — sensors
  need publish-only on the ingest topic; nothing but you needs `ControlTopic`
- enable TLS (`UseTLS`, port 8883) if the broker is reachable beyond a
  trusted LAN
- never port-forward the broker to the Internet

## Trust boundaries

- **Sensor payloads are untrusted.** Anything on the LAN (or mesh) can claim
  to be a sensor. The dashboard HTML-escapes every MQTT-sourced string before
  rendering, and the monitor treats payloads as data only. Whitelist and
  correlate — don't assume a sighting is honest.
- **The dashboard is read-only.** No state-changing endpoints exist; serving
  it on the LAN adds visibility without adding control surface. Front it with
  a reverse proxy + auth if it must leave the LAN.
- **LoRa is broadcast.** Meshtastic channels encrypt with a shared key —
  anyone with the key can inject sensor lines. LXMF messages are end-to-end
  encrypted to the bridge's destination identity.
- **Alert channel credentials** (Twilio, SMTP, ntfy tokens) live in
  `config.ini` — keep it out of world-readable backups; `RetentionDays` also
  bounds how much detection history a stolen SD card yields.

## What the sensors leak

A sniffer node transmits *unknown* MAC sightings over the mesh. The on-device
whitelist keeps your own devices' MACs off the air, and the compact format
carries no location — node identity maps to location only in the base
station's private `nodes.json`.
