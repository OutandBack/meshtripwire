import requests

NTFY_TOPIC = "tripwire-alerts"
WEBHOOK_URL = "https://example.com/webhook"
TWILIO_SMS_URL = "https://api.twilio.com/2010-04-01/Accounts/ACXXXXXXXXXXXXXXXXX/Messages.json"
TWILIO_AUTH = ("ACXXXXXXXXXXXXXXXXX", "your_auth_token")
FROM_PHONE = "+1234567890"
TO_PHONE = "+1987654321"

def send_alert(mac, node):
    message = f"ALERT: Unknown MAC {mac} detected by node {node}."

    # ntfy.sh alert
    try:
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=message)
    except Exception as e:
        print("ntfy.sh failed:", e)

    # Webhook
    try:
        requests.post(WEBHOOK_URL, json={"text": message})
    except Exception as e:
        print("Webhook failed:", e)

    # Twilio SMS (optional)
    try:
        requests.post(TWILIO_SMS_URL, auth=TWILIO_AUTH, data={
            "From": FROM_PHONE,
            "To": TO_PHONE,
            "Body": message
        })
    except Exception as e:
        print("Twilio SMS failed:", e)