# Alerts & Notifications

Every alert dispatches to all enabled channels, and **every delivery attempt
is logged** per channel — success or failure with the error — to the
`notifications` table, visible in the dashboard's notification log panel.

## Channels

### ntfy.sh

```ini
EnableNtfy = true
NtfyTopic = meshtripwire-<long-random-suffix>
# NtfyServer = https://ntfy.sh
# NtfyToken = tk_xxxxxxxxxxxxxxxxxxxx
```

On the public ntfy.sh the topic name IS the access control — keep it long and
random (`python -c "import secrets; print('meshtripwire-'+secrets.token_hex(12))"`),
or use an account/self-hosted server with an access token.

### Webhook

```ini
EnableWebhook = true
WebhookURL = https://example.com/webhook
```

POSTs `{"text": message}` — fits Slack/Discord/Matrix-style incoming webhooks.

### Twilio SMS

```ini
EnableTwilio = true
TwilioAccountSID = ACxxxx
TwilioAuthToken = ...
TwilioFromPhone = +1234567890
TwilioToPhone = +1987654321
```

### SMTP email — direct or via a relay

Built for relay-style submission the way AWS SES, Gmail, Mailgun, and
SendGrid actually work: STARTTLS + login on port 587 (the default), or
implicit TLS on port 465. Leave `SmtpUser` empty for an unauthenticated local
relay. Stdlib only — no extra dependency.

```ini
EnableSmtp = true
SmtpHost = email-smtp.us-east-1.amazonaws.com
SmtpPort = 587
SmtpUser = AKIAEXAMPLE
SmtpPassword = your-ses-smtp-password
SmtpFrom = tripwire@example.com
SmtpTo = you@example.com
SmtpStartTLS = true
```

### MQTT (the off-grid path)

```ini
EnableMqtt = true
MqttAlertTopic = meshtripwire/alerts
```

Republishes each alert as JSON (`mac`, `node`, `ts`, `message`) on the same
broker, for Node-RED-style consumers, Home Assistant, or — the reason it
exists — [RelayFabric carrying alerts over LoRa](off-grid.md#off-grid-alerts-relayfabric)
when there is no Internet at all.

## Alert types

| Alert | Trigger | Cooldown key |
|---|---|---|
| Unknown MAC | unknown device passed RSSI/whitelist/dwell while armed | `AlertCooldownSeconds` (per MAC) |
| Vehicle | magnetometer event | `VehicleAlertCooldownSeconds` (per node) |
| Impact/knock | piezo knock event | `KnockAlertCooldownSeconds` (per node) |
| Sustained shaking | piezo shake event | `ShakeAlertCooldownSeconds` (per node) |
| Contact | reed/PIR/beam trigger | `ContactAlertCooldownSeconds` (per node) |
| Sensor offline | watchdog: expected sensor silent | once until it returns |
| HIGH CONFIDENCE | ≥2 distinct sensor types within the correlation window | `CorrelationCooldownSeconds` |

All alert types respect [arming](security.md); cooldowns are independent per
(node, type), so a vehicle at the gate never masks a knock at the fence.

## The notification log

Each channel attempt records `(channel, target, ok, error, message)` — so an
alert's row set answers "did this actually reach me, and by which path?"
Failures show the concrete error (timeout, DNS, SMTP rejection). The dashboard
shows the latest 40; the full log is in SQLite
(`sqlite3 logs/detections.db 'SELECT * FROM notifications ORDER BY id DESC'`).
