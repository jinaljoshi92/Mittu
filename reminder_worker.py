"""
reminder_worker.py
Run as Render Cron Job every 15 minutes:
  Command:  python reminder_worker.py
  Schedule: */15 * * * *
"""
import os
from datetime import datetime, timezone, timedelta
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()
print(f"Reminder worker: {datetime.now().isoformat()}", flush=True)

db = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

TWILIO_SID    = os.getenv("TWILIO_SID")
TWILIO_TOKEN  = os.getenv("TWILIO_TOKEN")
TWILIO_NUMBER = os.getenv("TWILIO_NUMBER")
META_TOKEN    = os.getenv("META_TOKEN")
META_PHONE_ID = os.getenv("META_PHONE_ID")


def send_whatsapp(to, message):
    """Auto-detects Meta or Twilio and sends message"""
    if not message or not message.strip():
        return

    if META_TOKEN and META_PHONE_ID:
        import requests
        clean = to.replace("whatsapp:", "").replace("+", "").strip()
        r = requests.post(
            f"https://graph.facebook.com/v19.0/{META_PHONE_ID}/messages",
            headers={
                "Authorization": f"Bearer {META_TOKEN}",
                "Content-Type": "application/json"
            },
            json={
                "messaging_product": "whatsapp",
                "to": clean,
                "type": "text",
                "text": {"body": message.strip()}
            },
            timeout=15
        )
        print(f"Meta: {r.status_code} to {clean}", flush=True)
    else:
        from twilio.rest import Client
        client  = Client(TWILIO_SID, TWILIO_TOKEN)
        clean   = to if to.startswith("+") else f"+{to}"
        client.messages.create(
            from_=f"whatsapp:{TWILIO_NUMBER}",
            to=f"whatsapp:{clean}",
            body=message.strip()
        )
        print(f"Twilio: sent to {clean}", flush=True)


def build_message(reminder, language):
    """Build reminder message in correct language"""
    rtype  = reminder.get("reminder_type", "custom")
    msg    = reminder.get("message", "")
    cname  = reminder.get("customer_name", "")
    amount = reminder.get("amount", 0)
    lang   = (language or "hindi").upper()

    if rtype == "udhaar":
        templates = {
            "ENGLISH":  f"Reminder: {cname} has a pending payment of Rs {amount:.0f}. Consider following up today. - Mittu",
            "HINDI":    f"Reminder: {cname} ka Rs {amount:.0f} ka udhaar baaki hai. Aaj follow up kar sakte hain. - Mittu",
            "HINGLISH": f"Reminder: {cname} ka Rs {amount:.0f} pending hai. Follow up karo aaj. - Mittu",
            "GUJARATI": f"Reminder: {cname} no Rs {amount:.0f} no udhaar baaki chhe. - Mittu",
            "MARATHI":  f"Reminder: {cname} che Rs {amount:.0f} pending aahe. - Mittu",
        }
    elif rtype == "restock":
        templates = {
            "ENGLISH":  f"Restock reminder: Time to order {msg}. - Mittu",
            "HINDI":    f"Reminder: {msg} order karne ka time ho gaya hai. - Mittu",
            "HINGLISH": f"Reminder: {msg} order karna hai aaj. - Mittu",
            "GUJARATI": f"Reminder: {msg} nu order karva nu chhe. - Mittu",
            "MARATHI":  f"Reminder: {msg} chi order karnyachi vel aali. - Mittu",
        }
    else:
        templates = {
            "ENGLISH":  f"Reminder: {msg} - Mittu",
            "HINDI":    f"Reminder: {msg} - Mittu",
            "HINGLISH": f"Reminder: {msg} - Mittu",
            "GUJARATI": f"Reminder: {msg} - Mittu",
            "MARATHI":  f"Reminder: {msg} - Mittu",
        }

    return templates.get(lang, templates.get("ENGLISH", f"Reminder: {msg} - Mittu"))


def process_reminders():
    now = datetime.now(timezone.utc)
    # Check window: anything due in the last 20 minutes
    window_start = (now - timedelta(minutes=20)).isoformat()

    result = db.table("reminders")\
        .select("*, shops(phone, name, language)")\
        .eq("status", "pending")\
        .lte("remind_at", now.isoformat())\
        .gte("remind_at", window_start)\
        .execute()

    reminders = result.data
    print(f"Found {len(reminders)} due reminders", flush=True)

    for r in reminders:
        try:
            shop     = r.get("shops") or {}
            phone    = shop.get("phone", "")
            language = shop.get("language", "hindi")

            if not phone:
                print(f"No phone for reminder {r['id']}", flush=True)
                continue

            message = build_message(r, language)
            send_whatsapp(phone, message)

            db.table("reminders")\
                .update({"status": "sent"})\
                .eq("id", r["id"])\
                .execute()

            print(f"Sent reminder {r['id']}", flush=True)

        except Exception as e:
            print(f"Failed reminder {r['id']}: {e}", flush=True)
            db.table("reminders")\
                .update({"status": "failed"})\
                .eq("id", r["id"])\
                .execute()


if __name__ == "__main__":
    process_reminders()
    print("Done.", flush=True)
