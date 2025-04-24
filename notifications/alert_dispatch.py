import requests
import logging
# Import the config object from the main script (assuming it's accessible)
# This relies on mac_alert_monitor.py setting up the global 'config'
from mqtt.mac_alert_monitor import config as app_config

# Get a logger instance (consistent with the main script's logging)
logger = logging.getLogger(__name__)

def send_alert(mac, node):
    """Sends alerts via configured channels (ntfy, webhook, Twilio)."""
    if not app_config:
        logger.error("Configuration not loaded in alert_dispatch. Cannot send alerts.")
        return

    message = f"ALERT: Unknown MAC {mac} detected by node {node}."
    logger.info(f"Dispatching alert: {message}")

    # --- ntfy.sh Alert ---
    if app_config.getboolean('Notifications', 'EnableNtfy', fallback=False):
        ntfy_topic = app_config.get('Notifications', 'NtfyTopic', fallback=None)
        if ntfy_topic:
            try:
                url = f"https://ntfy.sh/{ntfy_topic}"
                response = requests.post(url, data=message.encode('utf-8')) # Send as bytes
                response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
                logger.info(f"Sent alert to ntfy.sh topic: {ntfy_topic}")
            except requests.exceptions.RequestException as e:
                logger.error(f"Failed to send alert to ntfy.sh ({url}): {e}")
            except Exception as e:
                logger.exception(f"Unexpected error sending to ntfy.sh: {e}")
        else:
            logger.warning("Ntfy enabled but NtfyTopic not set in config.")

    # --- Webhook Alert ---
    if app_config.getboolean('Notifications', 'EnableWebhook', fallback=False):
        webhook_url = app_config.get('Notifications', 'WebhookURL', fallback=None)
        if webhook_url:
            try:
                response = requests.post(webhook_url, json={"text": message})
                response.raise_for_status()
                logger.info(f"Sent alert to webhook: {webhook_url}")
            except requests.exceptions.RequestException as e:
                logger.error(f"Failed to send alert to webhook ({webhook_url}): {e}")
            except Exception as e:
                logger.exception(f"Unexpected error sending to webhook: {e}")
        else:
            logger.warning("Webhook enabled but WebhookURL not set in config.")

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
                })
                response.raise_for_status()
                # Check Twilio response content for success/failure if needed
                # response_data = response.json()
                # if response_data.get('status') in ['queued', 'sent']:
                logger.info(f"Sent alert via Twilio SMS to {to_phone}")
                # else:
                #     logger.error(f"Twilio API reported failure: {response_data.get('message')}")

            except requests.exceptions.RequestException as e:
                logger.error(f"Failed to send alert via Twilio SMS: {e}")
                # Log response body if available and indicates an error
                if e.response is not None:
                    logger.error(f"Twilio Response: {e.response.text}")
            except Exception as e:
                logger.exception(f"Unexpected error sending Twilio SMS: {e}")
        else:
            logger.warning("Twilio enabled but one or more required settings (SID, Token, From, To) are missing in config.")
