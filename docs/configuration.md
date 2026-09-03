# Configuration

All settings live in `config/config.ini`. The file ships with working
defaults; everything below is the full reference.

## `[MQTT]`

| Key | Default | Meaning |
|---|---|---|
| `Host` / `Port` | `localhost` / `1883` | broker address |
| `Topic` | `meshtastic/receive` | the single ingest topic |
| `Username` / `Password` | unset | broker credentials (Mosquitto 2.x disables anonymous access by default) |
| `UseTLS` / `CAFile` | off | TLS to the broker (typically port 8883) |

## `[Files]`

| Key | Default | Meaning |
|---|---|---|
| `Whitelist` | `config/whitelist.txt` | known MACs, one per line, hot-reloaded on change |
| `Nodes` | `config/nodes.json` | node id → static GPS coordinates |
| `Database` | `logs/detections.db` | SQLite path |
| `RetentionDays` | `0` | prune detections/events/notifications older than this (0 = keep forever) |

## `[Filtering]`

| Key | Default | Meaning |
|---|---|---|
| `RSSIMin` | `-120` | drop weaker sightings (-75 suits WiFi/BLE proximity; LoRa-relayed sightings arrive at -90..-120) |
| `EMAlpha` | `0.6` | RSSI smoothing factor |
| `StateTimeoutSeconds` | `3600` | forget unseen MACs after this |
| `AlertCooldownSeconds` | `300` | per-MAC re-alert suppression |
| `DwellSeconds` | `0` | alert only once a device persisted this long (0 = first sighting) |
| `VehicleAlertCooldownSeconds` | `300` | per-node vehicle re-alert suppression |
| `KnockAlertCooldownSeconds` | `300` | per-node knock re-alert suppression |
| `ShakeAlertCooldownSeconds` | `120` | per-node shake re-alert suppression (lower: high-confidence) |
| `ContactAlertCooldownSeconds` | `60` | per-node contact re-alert suppression; also absorbs Detection Sensor state re-broadcasts |
| `LightningLabelSeconds` | `120` | after an AS3935 strike event, vibration alerts in this window are labeled as possible thunder and kept out of correlation (0 disables) |
| `DroneAlertCooldownSeconds` | `300` | per-node drone Remote ID re-alert suppression |

## `[Sensors]`

| Key | Default | Meaning |
|---|---|---|
| `ExpectedSensors` | empty | comma-separated node ids the watchdog monitors |
| `SensorTimeoutSeconds` | `900` | alert if an expected sensor is silent this long |
| `HeartbeatTopic` | `meshtripwire/heartbeat` | sensors may publish liveness here |

## `[Arming]`

| Key | Default | Meaning |
|---|---|---|
| `Schedule` | empty | armed window `HH:MM-HH:MM` local (wraps midnight); empty = always armed |
| `ControlTopic` | `meshtripwire/arm` | publish `armed`/`disarmed`/`auto` to override the schedule |
| `ControlSecret` | empty | shared secret: control messages become `{"cmd":...,"secret":...}` |
| `ControlOverrideTTL` | `3600` | manual overrides auto-revert to the schedule (0 = never) |

## `[Correlation]`

| Key | Default | Meaning |
|---|---|---|
| `CorrelationMinTypes` | `2` | distinct sensor types within the window that trigger a combined alert |
| `CorrelationWindowSeconds` | `120` | the clustering window |
| `CorrelationCooldownSeconds` | `600` | minimum seconds between combined alerts |

## `[Notifications]`

Channel switches: `EnableNtfy`, `EnableWebhook`, `EnableTwilio`, `EnableSmtp`,
`EnableMqtt`. Per-channel settings are documented in
[Alerts & Notifications](alerts.md).

## Cutting false alarms

Alerting on *every* unknown MAC is unusable: MAC randomization means constant
strangers. Three gates, all in `config.ini`:

- **Dwell** (`DwellSeconds`): only alert once a device has *persisted*, so a
  passing car is ignored and someone loitering isn't. The single biggest
  false-positive reducer; try 120–300 s.
- **Arming** (`Schedule = 22:00-06:00`): only alert during away/asleep hours,
  or drive it live over `ControlTopic` (e.g. a phone-presence automation
  disarms while you're home). Set `ControlSecret` so a random broker client
  can't disarm you; overrides auto-revert after `ControlOverrideTTL`.
- **Sensor watchdog** (`ExpectedSensors = gate,fence`): alerts if a listed
  sensor goes silent, so a dead node or downed mesh link doesn't become a
  silent blind spot.
