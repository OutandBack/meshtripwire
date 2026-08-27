import json
import logging
import os
import smtplib
import time
from email.message import EmailMessage

import requests
import paho.mqtt.publish as mqtt_publish

# Get a logger instance (consistent with the main script's logging)
logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10 # seconds; alerts run on the MQTT thread, a hung POST would stall detection

def _report(on_result, channel, target, ok, error, message):
    """Deliver one channel outcome to the caller's on_result callback, safely."""
    if on_result is None:
        return
    try:
        on_result(channel, target, ok, error, message)
    except Exception:
        logger.exception(f"on_result callback failed for channel {channel}")


def send_alert(app_config, mac, node, message=None, on_result=None):
    """Sends alerts via configured channels (ntfy, webhook, Twilio, MQTT, SMTP).

    message overrides the default text (used for sensor-offline and other
    non-detection alerts); mac/node still tag the MQTT alert payload.
    on_result, if given, is called once per attempted channel with
    (channel, target, ok, error, message) — the monitor uses it to keep the
    notification log.
    """
    if message is None:
        message = f"ALERT: Unknown MAC {mac} detected by node {node}."
    logger.info(f"Dispatching alert: {message}")

    # --- MQTT Alert (off-grid relay / Node-RED / mesh downlinks on the same broker) ---
    # A local relay (e.g. RelayFabric) subscribes here and carries the alert
    # over LXMF/Reticulum/Meshtastic -- the paths that still work when there's
    # no Internet for ntfy/webhook/Twilio. Reuses the [MQTT] broker settings,
    # including auth/TLS (Mosquitto 2.x disables anonymous access by default).
    if app_config.getboolean('Notifications', 'EnableMqtt', fallback=False):
        alert_topic = app_config.get('Notifications', 'MqttAlertTopic', fallback='meshtripwire/alerts')
        try:
            auth = None
            mqtt_user = app_config.get('MQTT', 'Username', fallback=None)
            if mqtt_user:
                auth = {"username": mqtt_user,
                        "password": app_config.get('MQTT', 'Password', fallback=None)}
            tls = None
            if app_config.getboolean('MQTT', 'UseTLS', fallback=False):
                cafile = app_config.get('MQTT', 'CAFile', fallback=None) or None
                tls = {"ca_certs": cafile} if cafile else {}
            mqtt_publish.single(
                alert_topic,
                json.dumps({"mac": mac, "node": node, "ts": int(time.time()), "message": message}),
                qos=1,
                hostname=os.environ.get('MQTT_HOST') or app_config.get('MQTT', 'Host', fallback='localhost'),
                port=app_config.getint('MQTT', 'Port', fallback=1883),
                auth=auth, tls=tls,
            )
            logger.info(f"Published alert to MQTT topic: {alert_topic}")
            _report(on_result, 'mqtt', alert_topic, True, None, message)
        except Exception as e:
            logger.error(f"Failed to publish alert to MQTT ({alert_topic}): {e}")
            _report(on_result, 'mqtt', alert_topic, False, str(e), message)

    # --- ntfy.sh Alert ---
    if app_config.getboolean('Notifications', 'EnableNtfy', fallback=False):
        ntfy_topic = app_config.get('Notifications', 'NtfyTopic', fallback=None)
        if ntfy_topic:
            try:
                server = app_config.get('Notifications', 'NtfyServer', fallback='https://ntfy.sh').rstrip('/')
                url = f"{server}/{ntfy_topic}"
                # Token auth for private topics (ntfy account or self-hosted with ACLs).
                # Without it, privacy on ntfy.sh rests entirely on the topic being unguessable.
                headers = {}
                token = app_config.get('Notifications', 'NtfyToken', fallback=None)
                if token:
                    headers['Authorization'] = f"Bearer {token}"
                response = requests.post(url, data=message.encode('utf-8'),
                                         headers=headers, timeout=REQUEST_TIMEOUT)
                response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
                logger.info(f"Sent alert to ntfy.sh topic: {ntfy_topic}")
                _report(on_result, 'ntfy', ntfy_topic, True, None, message)
            except requests.exceptions.RequestException as e:
                logger.error(f"Failed to send alert to ntfy.sh ({url}): {e}")
                _report(on_result, 'ntfy', ntfy_topic, False, str(e), message)
            except Exception as e:
                logger.exception(f"Unexpected error sending to ntfy.sh: {e}")
                _report(on_result, 'ntfy', ntfy_topic, False, str(e), message)
        else:
            logger.warning("Ntfy enabled but NtfyTopic not set in config.")
            _report(on_result, 'ntfy', '', False, 'NtfyTopic not set', message)

    # --- Webhook Alert ---
    if app_config.getboolean('Notifications', 'EnableWebhook', fallback=False):
        webhook_url = app_config.get('Notifications', 'WebhookURL', fallback=None)
        if webhook_url:
            try:
                response = requests.post(webhook_url, json={"text": message}, timeout=REQUEST_TIMEOUT)
                response.raise_for_status()
                logger.info(f"Sent alert to webhook: {webhook_url}")
                _report(on_result, 'webhook', webhook_url, True, None, message)
            except requests.exceptions.RequestException as e:
                logger.error(f"Failed to send alert to webhook ({webhook_url}): {e}")
                _report(on_result, 'webhook', webhook_url, False, str(e), message)
            except Exception as e:
                logger.exception(f"Unexpected error sending to webhook: {e}")
                _report(on_result, 'webhook', webhook_url, False, str(e), message)
        else:
            logger.warning("Webhook enabled but WebhookURL not set in config.")
            _report(on_result, 'webhook', '', False, 'WebhookURL not set', message)

    # --- Twilio SMS Alert ---
    if app_config.getboolean('Notifications', 'EnableTwilio', fallback=False):
        account_sid = app_config.get('Notifications', 'TwilioAccountSID', fallback=None)
        auth_token = app_config.get('Notifications', 'TwilioAuthToken', fallback=None)
        from_phone = app_config.get('Notifications', 'TwilioFromPhone', fallback=None)
        to_phone = app_config.get('Notifications', 'TwilioToPhone', fallback=None)

        if all([account_sid, auth_token, from_phone, to_phone]):
            # Construct URL carefully using the SID
            twilio_url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
            try:
                response = requests.post(twilio_url, auth=(account_sid, auth_token), data={
                    "From": from_phone,
                    "To": to_phone,
                    "Body": message
                }, timeout=REQUEST_TIMEOUT)
                response.raise_for_status()
                logger.info(f"Sent alert via Twilio SMS to {to_phone}")
                _report(on_result, 'twilio', to_phone, True, None, message)
            except requests.exceptions.RequestException as e:
                logger.error(f"Failed to send alert via Twilio SMS: {e}")
                # Log response body if available and indicates an error
                if e.response is not None:
                    logger.error(f"Twilio Response: {e.response.text}")
                _report(on_result, 'twilio', to_phone, False, str(e), message)
            except Exception as e:
                logger.exception(f"Unexpected error sending Twilio SMS: {e}")
                _report(on_result, 'twilio', to_phone, False, str(e), message)
        else:
            logger.warning("Twilio enabled but one or more required settings (SID, Token, From, To) are missing in config.")
            _report(on_result, 'twilio', '', False, 'Twilio settings incomplete', message)

    # --- SMTP email (direct or via a relay: AWS SES, Gmail, Mailgun, SendGrid...) ---
    # Relay-style submission: STARTTLS + login on port 587 (the default), or
    # implicit TLS via SMTP_SSL on port 465. Leave SmtpUser empty for an
    # unauthenticated local relay.
    if app_config.getboolean('Notifications', 'EnableSmtp', fallback=False):
        host = app_config.get('Notifications', 'SmtpHost', fallback=None)
        to_addr = app_config.get('Notifications', 'SmtpTo', fallback=None)
        from_addr = app_config.get('Notifications', 'SmtpFrom', fallback=None)
        if host and to_addr and from_addr:
            port = app_config.getint('Notifications', 'SmtpPort', fallback=587)
            user = app_config.get('Notifications', 'SmtpUser', fallback=None)
            password = app_config.get('Notifications', 'SmtpPassword', fallback=None)
            starttls = app_config.getboolean('Notifications', 'SmtpStartTLS', fallback=True)
            try:
                msg = EmailMessage()
                msg['Subject'] = message.splitlines()[0][:120]
                msg['From'] = from_addr
                msg['To'] = to_addr
                msg.set_content(message)
                smtp_cls = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
                with smtp_cls(host, port, timeout=REQUEST_TIMEOUT) as server:
                    if starttls and smtp_cls is smtplib.SMTP:
                        server.starttls()
                    if user:
                        server.login(user, password or '')
                    server.send_message(msg)
                logger.info(f"Sent alert email to {to_addr} via {host}")
                _report(on_result, 'smtp', to_addr, True, None, message)
            except Exception as e:
                logger.error(f"Failed to send alert email via {host}: {e}")
                _report(on_result, 'smtp', to_addr, False, str(e), message)
        else:
            logger.warning("SMTP enabled but SmtpHost/SmtpFrom/SmtpTo missing in config.")
            _report(on_result, 'smtp', to_addr or '', False, 'SMTP settings incomplete', message)
