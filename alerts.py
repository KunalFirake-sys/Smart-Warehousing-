import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from twilio.rest import Client
import datetime

# ── Twilio Configuration ──────────────────────────────────────────────────────
TWILIO_ACCOUNT_SID = "ACc73af7faa47c51dba8a4e9e960538429"
TWILIO_AUTH_TOKEN  = "c7e5ce4f95df105bf5ae8084ff5cffd1"
TWILIO_FROM        = "whatsapp:+16814446524"   # Twilio sandbox / your number
TWILIO_TO          = "whatsapp:+919920929012"  # Recipient WhatsApp number
FAST2SMS_API_KEY = "64FoGBqrfOjbJmsIZgX5nupiUQy2dhzDWAak9K8xcVRlY73CvHycCpUjD9nsITxRO7Jz4gblAYF8tKNe"

# ── Email Configuration ───────────────────────────────────────────────────────
EMAIL_SENDER   = "digitaltwinwarehouse@gmail.com"
EMAIL_PASSWORD = "jqji evpr qlcw ciok"           # Gmail App Password (not account password)
EMAIL_RECEIVERS = ["kunalfirake12@gmail.com","akshaymohan632@gmail.com","shamitdarbari@gmail.com"]
SMTP_HOST      = "smtp.gmail.com"
SMTP_PORT      = 587

# ── Alert Thresholds ──────────────────────────────────────────────────────────
STOCK_LOW_THRESHOLD  = 1
STOCK_MAX_CAPACITY   = 6

# ── State Tracking ────────────────────────────────────────────────────────────
# Tracks last known alert state per shelf: "LOW" | "NORMAL" | "FULL"
_shelf_states: dict[str, str] = {}


client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

def send_sms(message: str) -> bool:
    try:
        url = "https://www.fast2sms.com/dev/bulkV2"

        payload = {
            "route": "q",                # quick route (testing)
            "message": message,
            "language": "english",
            "flash": 0,
            "numbers": "9920929012"      # your number WITHOUT +91
        }

        headers = {
            "authorization": FAST2SMS_API_KEY,
            "Content-Type": "application/json"
        }

        response = requests.post(url, json=payload, headers=headers)

        print("[SMS RESPONSE]", response.json())

        return response.status_code == 200

    except Exception as e:
        print("❌ SMS failed:", e)
        return False
def send_email(subject: str, body: str) -> bool:
    try:
        import smtplib
        from email.mime.text import MIMEText

        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = EMAIL_SENDER
        msg['To'] = ", ".join(EMAIL_RECEIVERS)   # 👈 multiple recipients

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(
                EMAIL_SENDER,
                EMAIL_RECEIVERS,   # 👈 send to list
                msg.as_string()
            )

        print(f"✅ Email sent to: {EMAIL_RECEIVERS}")
        return True

    except Exception as e:
        print(f"❌ Email failed: {e}")
        return False

def send_alert(message: str) -> None:
    """
    Multi-channel alert system:
    - Sends SMS (primary)
    - Sends Email (secondary / logging)
    - Provides debug output for reliability tracking
    """

    print("🚨 Sending alert...")

    sms_success = send_sms(message)
    email_success = send_email("Warehouse Inventory Alert", message)

    if sms_success:
        print("✅ SMS sent successfully")
    else:
        print("❌ SMS failed")

    if email_success:
        print("✅ Email sent successfully")
    else:
        print("❌ Email failed")

    if not sms_success and not email_success:
        print("🚨 CRITICAL: All alert channels failed!")

def check_alert(shelf_id: str, count: int) -> None:
    """
    Evaluate inventory count for a shelf and fire an alert only when
    the alert state changes (LOW → NORMAL, NORMAL → FULL, etc.).

    Call this function whenever fresh inventory data is received.
    """
    if count <= STOCK_LOW_THRESHOLD:
        new_state = "LOW"
    elif count >= STOCK_MAX_CAPACITY:
        new_state = "FULL"
    else:
        new_state = "NORMAL"

    previous_state = _shelf_states.get(shelf_id)

    # Only act on a genuine state transition
    if new_state == previous_state:
        return

    _shelf_states[shelf_id] = new_state

    if new_state == "LOW":
        message = (
            f"⚠️ LOW STOCK ALERT\n"
            f"Shelf   : {shelf_id}\n"
            f"Count   : {count} item(s)\n"
            f"Action  : Restock required.\n"
            f"Time: {datetime.datetime.now()}\n"
        )
        send_alert(message)

    elif new_state == "FULL":
        message = (
            f"📦 SHELF FULL ALERT\n"
            f"Shelf   : {shelf_id}\n"
            f"Count   : {count} item(s)\n"
            f"Action  : No more items can be placed.\n"
            f"Time: {datetime.datetime.now()}\n"
        )
        send_alert(message)

    # NORMAL state — no notification needed, state recorded for future transitions
