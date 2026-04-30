"""
reminder_worker.py
──────────────────
Runs every hour as a separate process on Render.
Checks reminders table for due reminders and sends WhatsApp messages.

Deploy as a separate Render Cron Job:
  Command: python reminder_worker.py
  Schedule: 0 * * * *  (every hour)
"""

import os
import requests
from datetime import datetime, timezone
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()
print("Reminder worker starting...", flush=True)

# ─────────────────────────────────────────
# CONNECTIONS
# ─────────────────────────────────────────
SUPABASE_URL  = os.getenv("SUPABASE_URL")
SUPABASE_KEY  = os.getenv("SUPABASE_KEY")
META_TOKEN    = os.getenv("META_TOKEN")
META_PHONE_ID = os.getenv("META_PHONE_ID")
TWILIO_SID    = os.getenv("TWILIO_SID")
TWILIO_TOKEN  = os.getenv("TWILIO_TOKEN")
TWILIO_NUMBER = os.getenv("TWILIO_NUMBER")

db = create_client(SUPABASE_URL, SUPABASE_KEY)


def send_whatsapp(to, message):
    """
    Send WhatsApp message.
    Uses Meta API if META_TOKEN is set, otherwise falls back to Twilio.
    """
    if META_TOKEN and META_PHONE_ID:
        # Meta Cloud API
        clean_to = to.replace("whatsapp:", "").replace("+", "").strip()
        url      = f"https://graph.facebook.com/v19.0/{META_PHONE_ID}/messages"
        payload  = {
            "messaging_product": "whatsapp",
            "recipient_type":    "individual",
            "to":                clean_to,
            "type":              "text",
            "text":              {"body": message.strip()}
        }
        headers = {
            "Authorization": f"Bearer {META_TOKEN}",
            "Content-Type":  "application/json"
        }
        r = requests.post(url, json=payload, headers=headers, timeout=15)
        print(f"Meta API: {r.status_code} to {clean_to}", flush=True)

    else:
        # Twilio fallback
        from twilio.rest import Client
        client = Client(TWILIO_SID, TWILIO_TOKEN)
        clean_to = to if to.startswith("+") else f"+{to}"
        client.messages.create(
            from_=f"whatsapp:{TWILIO_NUMBER}",
            to=f"whatsapp:{clean_to}",
            body=message.strip()
        )
        print(f"Twilio: sent to {clean_to}", flush=True)


def process_reminders():
    """Check for due reminders and send them."""
    now = datetime.now(timezone.utc).isoformat()

    # Fetch all pending reminders that are due
    result = db.table("reminders")\
        .select("*, shops(phone, name, owner_name, language)")\
        .eq("status", "pending")\
        .lte("remind_at", now)\
        .execute()

    reminders = result.data
    print(f"Found {len(reminders)} due reminders", flush=True)

    for reminder in reminders:
        try:
            shop       = reminder.get("shops", {})
            phone      = shop.get("phone", "")
            shop_name  = shop.get("name", "your shop")
            owner_name = shop.get("owner_name", "")
            language   = (shop.get("language") or "hindi").upper()

            if not phone:
                print(f"No phone for reminder {reminder['id']}", flush=True)
                continue

            reminder_type = reminder.get("reminder_type", "custom")
            message_text  = reminder.get("message", "")
            customer_name = reminder.get("customer_name", "")
            amount        = reminder.get("amount", 0)

            # Build reminder message based on type
            if reminder_type == "udhaar":
                if language == "ENGLISH":
                    msg = (
                        f"Reminder from Mittu: "
                        f"{customer_name} has a pending payment of "
                        f"Rs {amount:.0f}. "
                        f"You may want to follow up today. - Mittu"
                    )
                elif language == "GUJARATI":
                    msg = (
                        f"Mittu reminder: "
                        f"{customer_name} no Rs {amount:.0f} no "
                        f"udhaar baaki chhe. - Mittu"
                    )
                else:
                    msg = (
                        f"Mittu reminder: "
                        f"{customer_name} ka Rs {amount:.0f} ka "
                        f"udhaar baaki hai. Aaj follow up kar sakte hain. "
                        f"- Mittu"
                    )

            elif reminder_type == "restock":
                if language == "ENGLISH":
                    msg = (
                        f"Restock reminder from Mittu: "
                        f"Time to order {message_text}. - Mittu"
                    )
                else:
                    msg = (
                        f"Mittu reminder: "
                        f"{message_text} order karne ka time ho gaya hai. "
                        f"- Mittu"
                    )

            else:
                # Custom reminder — use message as is
                if language == "ENGLISH":
                    msg = f"Reminder from Mittu: {message_text} - Mittu"
                else:
                    msg = f"Mittu reminder: {message_text} - Mittu"

            # Send the reminder
            send_whatsapp(phone, msg)

            # Mark as sent
            db.table("reminders")\
                .update({"status": "sent"})\
                .eq("id", reminder["id"])\
                .execute()

            print(f"Reminder sent: {reminder['id']} to {phone}", flush=True)

        except Exception as e:
            print(f"Error sending reminder {reminder['id']}: {e}", flush=True)
            # Mark as failed so we know
            db.table("reminders")\
                .update({"status": "failed"})\
                .eq("id", reminder["id"])\
                .execute()


if __name__ == "__main__":
    process_reminders()
    print("Reminder worker done.", flush=True)
