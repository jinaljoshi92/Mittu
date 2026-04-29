import os
import sys
import json
from flask import Flask, request
from twilio.rest import Client
from supabase import create_client
from dotenv import load_dotenv
from datetime import date, timedelta
from groq import Groq

load_dotenv()
print("Mittu starting...", flush=True)

app = Flask(__name__)

# ─────────────────────────────────────────
# CREDENTIALS
# ─────────────────────────────────────────
TWILIO_SID    = os.getenv("TWILIO_SID")
TWILIO_TOKEN  = os.getenv("TWILIO_TOKEN")
TWILIO_NUMBER = os.getenv("TWILIO_NUMBER")
SUPABASE_URL  = os.getenv("SUPABASE_URL")
SUPABASE_KEY  = os.getenv("SUPABASE_KEY")
GROQ_KEY      = os.getenv("GROQ_KEY")

# ─────────────────────────────────────────
# CONNECTIONS
# ─────────────────────────────────────────
db          = create_client(SUPABASE_URL, SUPABASE_KEY)
groq_client = Groq(api_key=GROQ_KEY)

# ─────────────────────────────────────────
# PLAN LIMITS
# ─────────────────────────────────────────
PLAN_LIMITS = {
    "free": {
        "orders_per_day": 10,
        "udhaar_persons": 0,
        "reports":        "basic",
        "invoice":        False,
        "google_sheet":   False,
        "stock":          False,
        "languages":      ["ENGLISH", "HINDI", "HINGLISH"],
    },
    "plan99": {
        "orders_per_day": 999999,
        "udhaar_persons": 5,
        "reports":        "revenue",
        "invoice":        False,
        "google_sheet":   True,
        "stock":          False,
        "languages":      ["ENGLISH", "HINDI", "HINGLISH"],
    },
    "plan199": {
        "orders_per_day": 999999,
        "udhaar_persons": 999999,
        "reports":        "full",
        "invoice":        True,
        "google_sheet":   True,
        "stock":          True,
        "languages":      ["ENGLISH", "HINDI", "HINGLISH",
                           "GUJARATI", "MARATHI"],
    },
}

def get_limit(shop, feature):
    plan = shop.get("plan", "free")
    if plan not in PLAN_LIMITS:
        plan = "free"
    return PLAN_LIMITS[plan][feature]

def language_allowed(shop, language):
    return language.upper() in get_limit(shop, "languages")


# ─────────────────────────────────────────
# TWILIO SENDER
# ─────────────────────────────────────────
def send_whatsapp(to, message):
    if not message or not message.strip():
        message = "Something went wrong. Please try again. - Mittu"
    client = Client(TWILIO_SID, TWILIO_TOKEN)
    client.messages.create(
        from_=f"whatsapp:{TWILIO_NUMBER}",
        to=f"whatsapp:{to}",
        body=message.strip()
    )
    print(f"Sent: {message[:80]}", flush=True)


# ─────────────────────────────────────────
# GROQ AI HELPER
# ─────────────────────────────────────────
def ask_groq(prompt, max_tokens=300, temperature=0.7):
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are Mittu, a WhatsApp assistant. "
                        "You MUST follow the LANGUAGE instruction "
                        "in every prompt exactly. "
                        "If it says English only — every single word "
                        "must be English. No exceptions."
                    )
                },
                {"role": "user", "content": prompt}
            ],
            max_tokens=max_tokens,
            temperature=temperature
        )
        content = response.choices[0].message.content
        if not content or not content.strip():
            raise ValueError("Empty response from Groq")
        return content.strip()
    except Exception as e:
        print(f"Groq error: {e}", flush=True)
        raise e
# ─────────────────────────────────────────
# CORE PRINCIPLE — HOW LANGUAGE WORKS
#
# generate_reply() is the ONLY place that
# talks to Groq for final WhatsApp replies.
#
# The 'context' parameter must ALWAYS be
# written in neutral English — it describes
# what to say, not how to say it.
#
# The language instruction tells Groq which
# language to OUTPUT the reply in.
#
# NEVER put Hindi/Gujarati words in context.
# NEVER use "ka", "ki", "ne", "hai" in context.
# ─────────────────────────────────────────

def generate_reply(context, language, shop_name, shop_type="general"):
    lang_map = {
        "ENGLISH":  "ENGLISH ONLY — every word must be English",
        "HINDI":    "HINDI ONLY — Roman or Devanagari",
        "HINGLISH": "HINGLISH — natural Hindi-English mix",
        "GUJARATI": "GUJARATI ONLY",
        "MARATHI":  "MARATHI ONLY",
    }
    lang = lang_map.get(language, "HINDI ONLY")

    try:
        return ask_groq(
            f"""LANGUAGE INSTRUCTION: {lang}
LANGUAGE INSTRUCTION: {lang}

You are Mittu for {shop_name}.

Rules:
- Output language: {lang}
- Never say bhai or arre
- Talk to shop owner only, 3 lines max
- End with: - Mittu

Content to convey (in {lang}):
{context}""",
            max_tokens=200,
            temperature=0.5
        )
    except:
        fallbacks = {
            "ENGLISH":  "Something went wrong. Please try again. - Mittu",
            "HINDI":    "Kuch gadbad hua. Dobara try karein. - Mittu",
            "HINGLISH": "Kuch issue hua. Please try again. - Mittu",
            "GUJARATI": "Koi samasya aayi. Pachhi try karo. - Mittu",
            "MARATHI":  "Kahi problem aali. Punha try kara. - Mittu",
        }
        return fallbacks.get(language, fallbacks["ENGLISH"])

# ─────────────────────────────────────────
# JSON PARSER
# ─────────────────────────────────────────
def safe_json(text):
    try:
        clean = text.strip()
        if "```" in clean:
            parts = clean.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                try:
                    return json.loads(part)
                except:
                    continue
        return json.loads(clean)
    except:
        return None


# ─────────────────────────────────────────
# CONVERSATION MEMORY
# ─────────────────────────────────────────
def save_message(shop_id, role, message):
    try:
        db.table("conversations").insert({
            "shop_id": shop_id,
            "role":    role,
            "message": message
        }).execute()
        old = db.table("conversations")\
            .select("id")\
            .eq("shop_id", shop_id)\
            .order("created_at", desc=True)\
            .execute()
        if len(old.data) > 10:
            for r in old.data[10:]:
                db.table("conversations")\
                    .delete().eq("id", r["id"]).execute()
    except Exception as e:
        print(f"Memory error: {e}", flush=True)

def get_conversation_history(shop_id, limit=5):
    try:
        result = db.table("conversations")\
            .select("*")\
            .eq("shop_id", shop_id)\
            .order("created_at", desc=True)\
            .limit(limit)\
            .execute()
        return list(reversed(result.data))
    except:
        return []

def format_history(messages):
    if not messages:
        return "No previous messages."
    return "\n".join([
        f"{'Owner' if m['role']=='user' else 'Mittu'}: {m['message']}"
        for m in messages
    ])


# ─────────────────────────────────────────
# LANGUAGE DETECTOR
# ─────────────────────────────────────────
def detect_language(message, prev_language=None):
    # Script detection — always accurate, no AI needed
    if any('\u0900' <= c <= '\u097F' for c in message):
        return "HINDI"
    if any('\u0A80' <= c <= '\u0AFF' for c in message):
        return "GUJARATI"

    try:
        result = ask_groq(
            f"""Detect the language of this message. Return ONLY one word.

Message: "{message}"

Rules:
- Pure English sentences with no Hindi words = ENGLISH
- Hindi written in Roman letters (kaise ho, theek hai, aata, daal, kal, aaj) = HINDI
- Mix of Hindi and English words in same sentence = HINGLISH
- Gujarati words (kem chhe, su chhe, tamaro, chho, pan) = GUJARATI
- Marathi words (kay aahe, mala, tumhi, hoil) = MARATHI
- Short words alone: Hi, Hello, Ok, Yes, No, Thanks = ENGLISH
- Previous language was: {prev_language or 'unknown'}

Return exactly one word — ENGLISH, HINDI, HINGLISH, GUJARATI, or MARATHI:""",
            max_tokens=10,
            temperature=0
        ).upper().strip()

        result = result.replace(".", "").replace(",", "").strip()
        valid = ["ENGLISH", "HINDI", "HINGLISH", "GUJARATI", "MARATHI"]
        return result if result in valid else (prev_language or "HINDI")
    except:
        return prev_language or "HINDI"


# ─────────────────────────────────────────
# INTENT DETECTOR
# ─────────────────────────────────────────
def detect_intent(message, history=""):
    try:
        result = ask_groq(
            f"""Classify this message from a small business owner.
Return ONLY one word — no punctuation, no explanation.

Conversation so far:
{history}

New message: "{message}"

GREETING = greetings like hello, hi, how are you, namaste, kem chhe
ORDER    = recording a sale or customer purchase
           "Suresh took 2kg flour", "Nia order 1L milk Rs 30"
           "customer bought goods", "note order for X"
REPORT   = asking for sales data, order count, revenue
UPDATE   = adding price or changing details of a PREVIOUS order
           (only if previous message was an ORDER)
UDHAAR   = credit tracking — someone owes money
HELP     = asking what Mittu can do
CHAT     = anything else

Key rules:
- "take an order" or "note an order" alone with no items = ORDER
  (Mittu will ask for details)
- Price mentioned after a previous ORDER = UPDATE
- "yesterday", "kal", "last week" in report = REPORT

Return one word:""",
            max_tokens=10,
            temperature=0
        ).upper().strip()

        result = result.replace(".", "").replace(",", "").strip()
        valid = ["GREETING", "ORDER", "REPORT", "UDHAAR",
                 "UPDATE", "HELP", "CHAT"]
        for word in valid:
            if word in result:
                return word
        return "CHAT"
    except:
        return "CHAT"


# ─────────────────────────────────────────
# SHOP MANAGER
# ─────────────────────────────────────────
def get_or_create_shop(phone):
    result = db.table("shops")\
        .select("*").eq("phone", phone).execute()
    if result.data:
        return result.data[0]
    new_shop = db.table("shops").insert({
        "phone":        phone,
        "plan":         "free",
        "onboarded":    False,
        "onboard_step": 0,
        "udhaar_limit": 0,
        "udhaar_count": 0,
        "shop_type":    "general",
    }).execute()
    print(f"New shop: {phone}", flush=True)
    return new_shop.data[0]

def update_shop(shop_id, data):
    db.table("shops").update(data).eq("id", shop_id).execute()


# ─────────────────────────────────────────
# GREETING HANDLER
# ─────────────────────────────────────────
def greeting_reply(shop, language):
    shop_name  = shop.get("name", "your shop")
    shop_type  = shop.get("shop_type", "general")
    owner_name = shop.get("owner_name", "")
    plan       = shop.get("plan", "free")

    features = {
        "free":    "orders and basic daily report",
        "plan99":  "orders, reports and udhaar tracking",
        "plan199": "orders, reports, udhaar and stock management",
    }.get(plan, "orders and reports")

    name_part = f"Hello {owner_name}" if (owner_name and language == "ENGLISH") \
                else (f"Namaste {owner_name}ji" if owner_name else "")

    return generate_reply(
        f"The shop owner just greeted you. "
        f"{'Address them as: ' + name_part + '.' if name_part else 'Greet them warmly.'} "
        f"In one short sentence say you are ready to help with {features}.",
        language, shop_name, shop_type
    )


# ─────────────────────────────────────────
# LANGUAGE UPGRADE
# ─────────────────────────────────────────
def language_upgrade_reply(language, shop, shop_name):
    lang_display = {"GUJARATI": "Gujarati", "MARATHI": "Marathi"}.get(
        language, language.title())
    plan = shop.get("plan", "free")
    return generate_reply(
        f"{lang_display} language support requires the Rs 199 plan. "
        f"Currently on {plan} plan which supports Hindi and English. "
        f"Let the owner know politely.",
        "ENGLISH", shop_name
    )


# ─────────────────────────────────────────
# CONFUSION HANDLER
# ─────────────────────────────────────────
def confusion_reply(language, shop_name, shop_type="general"):
    return generate_reply(
        "You did not understand the message. "
        "Apologize briefly and ask them to repeat with more detail.",
        language, shop_name, shop_type
    )


# ─────────────────────────────────────────
# ONBOARDING FLOW
# Step 0 → ask shop name
# Step 1 → ask shop type
# Step 2 → ask owner name
# Step 3 → complete
# ─────────────────────────────────────────
def handle_onboarding(message, shop, language):
    step = shop.get("onboard_step", 0)

    # Onboarding language — match what owner used
    ob_lang = language

    if step == 0:
        update_shop(shop["id"], {"onboard_step": 1})
        return generate_reply(
            "Welcome the owner warmly. "
            "Say Mittu helps any small business with: "
            "recording orders, daily sales reports, and credit tracking. "
            "Ask: What is the name of their shop or business?",
            ob_lang, "Mittu"
        )

    elif step == 1:
        raw       = message.strip()
        extracted = ask_groq(
            f"""Extract only the shop or business name from this message.
Message: "{raw}"
Examples:
"Sharma Medical Store" → Sharma Medical Store
"my shop is JRH" → JRH
"ABC Hardware" → ABC Hardware
"naam hai Mittu Store" → Mittu Store
Return ONLY the name — nothing else:""",
            max_tokens=30, temperature=0
        ).strip().title()

        shop_name = extracted if 0 < len(extracted) < 60 else raw.title()
        update_shop(shop["id"], {"name": shop_name, "onboard_step": 2})

        return generate_reply(
            f"Shop name saved: {shop_name}. "
            f"Welcome them using the shop name. "
            f"Now ask what type of business it is — "
            f"for example: kirana, medical, dairy, hardware, clothing, salon, etc.",
            ob_lang, shop_name
        )

    elif step == 2:
        raw       = message.strip()
        extracted = ask_groq(
            f"""Identify the business type from this message.
Message: "{raw}"
Return one short label like:
kirana, medical, dairy, vegetables, hardware, clothing,
restaurant, salon, electronics, bakery, general
Return ONLY the label:""",
            max_tokens=15, temperature=0
        ).strip().lower()

        shop_type = extracted if extracted else "general"
        shop_name = shop.get("name", "your shop")
        update_shop(shop["id"], {"shop_type": shop_type, "onboard_step": 3})

        return generate_reply(
            f"Business type noted: {shop_type}. "
            f"Acknowledge warmly. "
            f"Now ask the owner their name.",
            ob_lang, shop_name, shop_type
        )

    else:
        raw       = message.strip()
        extracted = ask_groq(
            f"""Extract the person's first name from this message.
Message: "{raw}"
Examples: "my name is Rahul" → Rahul | "main Priya hoon" → Priya
Return ONLY the first name:""",
            max_tokens=15, temperature=0
        ).strip().title()

        owner_name = extracted if 0 < len(extracted) < 30 else raw.title()
        shop_name  = shop.get("name", "your shop")
        shop_type  = shop.get("shop_type", "general")

        update_shop(shop["id"], {"owner_name": owner_name, "onboarded": True})

        return generate_reply(
            f"Onboarding complete. Owner name: {owner_name}. "
            f"Greet them by name warmly. "
            f"Say Mittu is ready for their {shop_type} business. "
            f"Give 3 short examples of what they can type naturally: "
            f"1) recording a customer order "
            f"2) asking for today's report "
            f"3) tracking credit given to a customer (paid plan). "
            f"Tell them no special commands needed — just type naturally.",
            ob_lang, shop_name, shop_type
        )


# ─────────────────────────────────────────
# AGENT 1 — ORDER AGENT
# ─────────────────────────────────────────
def order_agent(message, shop, language, history=""):
    shop_name = shop.get("name", "your shop")
    shop_type = shop.get("shop_type", "general")
    limit     = get_limit(shop, "orders_per_day")
    today     = date.today().isoformat()

    count_result = db.table("orders")\
        .select("id", count="exact")\
        .eq("shop_id", shop["id"])\
        .gte("created_at", today).execute()
    today_count = count_result.count or 0

    if today_count >= limit:
        return generate_reply(
            f"Daily order limit of {limit} reached on "
            f"{shop.get('plan','free')} plan. "
            f"Tell them politely and suggest upgrading to Rs 99 for unlimited orders.",
            language, shop_name, shop_type
        )

    # Extract order — use only the current message for new orders
    # History is provided only for reference resolution not extraction
    extracted = ask_groq(
        f"""Extract order details from this message.
Business type: {shop_type}

IMPORTANT: Extract from the CURRENT message only.
Use history ONLY to resolve pronouns like "her", "his", "their".
Do NOT carry over items from previous orders.

History (for pronoun resolution only):
{history}

Current message: "{message}"

Auto-calculate total if multiple items have individual prices:
Example: "milk Rs 30 and biscuits Rs 45" → amount = 75.0

Return ONLY valid JSON:
{{"customer_name": "name",
  "items": "items exactly as written in current message",
  "amount": 0.0,
  "item_breakdown": "item1 Rs X, item2 Rs Y"}}

Rules:
- items: copy EXACTLY from the current message, do not translate
- amount: sum of all item prices mentioned in current message
- item_breakdown: each item with its individual price
- 0.0 if no price mentioned

Return ONLY JSON:""",
        max_tokens=200, temperature=0
    )

    details = safe_json(extracted) or {
        "customer_name": "Customer",
        "items":         message,
        "amount":        0.0,
        "item_breakdown": ""
    }

    cust      = str(details.get("customer_name", "Customer")).strip()
    items     = str(details.get("items", message)).strip()
    amount    = float(details.get("amount", 0))
    breakdown = str(details.get("item_breakdown", "")).strip()

    # If no real order details extracted — ask for them
    vague = [
        "can you take", "please take", "note order", "take order",
        "ek order", "order lena", "order lo", "order chahiye",
        "order please", "take one order"
    ]
    if any(p in message.lower() for p in vague) and (
        items.lower() == message.lower() or len(items) > len(message) * 0.8
    ):
        return generate_reply(
            "The owner wants to record an order but has not provided details yet. "
            "Ask them: who is the customer and what items do they want to order?",
            language, shop_name, shop_type
        )

    # Save order
    db.table("orders").insert({
        "shop_id":       shop["id"],
        "customer_name": cust,
        "items":         items,
        "amount":        amount,
        "status":        "new"
    }).execute()

    print(f"Order: {cust} | {items} | Rs {amount}", flush=True)

    # Build confirmation — ALL in neutral English, no Hindi words
    if amount > 0 and breakdown:
        confirm = (
            f"Order saved successfully. "
            f"Customer: {cust}. "
            f"Items: {breakdown}. "
            f"Total: Rs {amount:.0f}. "
            f"Confirmed. Keep items exactly as written, do not translate them."
        )
    elif amount > 0:
        confirm = (
            f"Order saved successfully. "
            f"Customer: {cust}. "
            f"Items: {items}. "
            f"Amount: Rs {amount:.0f}. "
            f"Confirmed. Keep items exactly as written, do not translate them."
        )
    else:
        confirm = (
            f"Order saved for customer {cust}: {items}. "
            f"No price was mentioned. "
            f"Tell owner they can add price anytime by sending: "
            f"'{cust} order price is Rs [amount]'. "
            f"Keep items exactly as written, do not translate them."
        )

    return generate_reply(confirm, language, shop_name, shop_type)


# ─────────────────────────────────────────
# ORDER UPDATE AGENT
# ─────────────────────────────────────────
def order_update_agent(message, shop, language, history=""):
    shop_name = shop.get("name", "your shop")
    shop_type = shop.get("shop_type", "general")

    extracted = ask_groq(
        f"""Owner is updating a previous order.
Use history to find which customer.

History:
{history}

Message: "{message}"

Return ONLY valid JSON:
{{"customer_name": "name", "field": "amount", "value": "new value"}}
field: amount / items / status
Return ONLY JSON:""",
        max_tokens=100, temperature=0
    )

    details = safe_json(extracted)
    if not details or not details.get("customer_name"):
        return confusion_reply(language, shop_name, shop_type)

    customer    = str(details.get("customer_name", "")).strip()
    field       = details.get("field", "amount")
    value       = details.get("value", "")
    today       = date.today().isoformat()

    orders = db.table("orders")\
        .select("*").eq("shop_id", shop["id"])\
        .ilike("customer_name", f"%{customer}%")\
        .gte("created_at", today)\
        .order("created_at", desc=True).limit(1).execute()

    if not orders.data:
        return generate_reply(
            f"No order found for {customer} today. Tell owner clearly.",
            language, shop_name, shop_type
        )

    order_id    = orders.data[0]["id"]
    order_items = orders.data[0]["items"]
    update_data = {}

    if field == "amount":
        try:
            clean_val = str(value).lower()\
                .replace("rs", "").replace("₹", "")\
                .replace(",", "").strip()
            update_data["amount"] = float(clean_val)
        except:
            update_data["amount"] = 0.0
    elif field == "items":
        update_data["items"] = str(value)

    db.table("orders").update(update_data)\
        .eq("id", order_id).execute()

    print(f"Updated order {order_id}: {update_data}", flush=True)

    new_amount = update_data.get("amount", value)

    # Context in pure English — no Hindi words
    return generate_reply(
        f"Order updated. "
        f"Customer: {customer}. "
        f"Items: {order_items}. "
        f"Final price: Rs {new_amount}. "
        f"Confirmed. Keep items exactly as written, do not translate.",
        language, shop_name, shop_type
    )


# ─────────────────────────────────────────
# AGENT 2 — REPORT AGENT
# Now handles: today, yesterday, this week,
# last week, this month
# ─────────────────────────────────────────
def report_agent(message, shop, language):
    shop_name = shop.get("name", "your shop")
    shop_type = shop.get("shop_type", "general")
    plan      = shop.get("plan", "free")
    msg_lower = message.lower()

    # Determine date range
    today = date.today()

    if any(w in msg_lower for w in ["yesterday", "kal", "kal ka", "kal ke"]):
        start_date   = (today - timedelta(days=1)).isoformat()
        end_date     = today.isoformat()
        period_label = "Yesterday"
    elif any(w in msg_lower for w in ["last week", "pichle hafte", "pichla hafta"]):
        start_date   = (today - timedelta(days=14)).isoformat()
        end_date     = (today - timedelta(days=7)).isoformat()
        period_label = "Last week"
    elif any(w in msg_lower for w in ["week", "hafte", "weekly", "hafta", "saptah"]):
        start_date   = (today - timedelta(days=7)).isoformat()
        end_date     = (today + timedelta(days=1)).isoformat()
        period_label = "This week"
    elif any(w in msg_lower for w in ["month", "mahine", "monthly", "mahina"]):
        start_date   = today.replace(day=1).isoformat()
        end_date     = (today + timedelta(days=1)).isoformat()
        period_label = "This month"
    else:
        start_date   = today.isoformat()
        end_date     = (today + timedelta(days=1)).isoformat()
        period_label = "Today"

    orders = db.table("orders")\
        .select("*")\
        .eq("shop_id", shop["id"])\
        .gte("created_at", start_date)\
        .lt("created_at", end_date)\
        .execute()

    total_orders  = len(orders.data)
    total_revenue = sum(float(o.get("amount", 0)) for o in orders.data)

    customer_names = list(set([
        o.get("customer_name", "")
        for o in orders.data
        if o.get("customer_name") and o.get("customer_name") != "Customer"
    ]))
    names_text = ", ".join(customer_names[:5]) if customer_names else "none recorded"

    if plan == "free":
        return generate_reply(
            f"Report for {period_label}: "
            f"{total_orders} orders recorded. "
            f"Customers: {names_text}. "
            f"Revenue tracking is available on the Rs 99 plan. "
            f"Share this information warmly.",
            language, shop_name, shop_type
        )

    # Paid plans
    item_counts = {}
    for o in orders.data:
        for item in o.get("items", "").split(","):
            item = item.strip()
            if item:
                item_counts[item] = item_counts.get(item, 0) + 1

    top_items = sorted(item_counts.items(), key=lambda x: x[1], reverse=True)[:3]
    top_text  = ", ".join([f"{i[0]} ({i[1]}x)" for i in top_items]) \
                if top_items else "no data"

    recent = " | ".join([
        f"{o.get('customer_name','?')}: {o.get('items','?')} Rs {o.get('amount',0)}"
        for o in orders.data[-5:]
    ]) if orders.data else "no orders"

    return generate_reply(
        f"Report for {period_label}: "
        f"{total_orders} orders, Rs {total_revenue:.0f} total revenue. "
        f"Customers: {names_text}. "
        f"Top items: {top_text}. "
        f"Recent: {recent}. "
        f"Give a warm encouraging summary.",
        language, shop_name, shop_type
    )


# ─────────────────────────────────────────
# AGENT 3 — UDHAAR AGENT
# ─────────────────────────────────────────
def udhaar_agent(message, shop, language, history=""):
    shop_name = shop.get("name", "your shop")
    shop_type = shop.get("shop_type", "general")
    plan      = shop.get("plan", "free")

    if plan == "free":
        return generate_reply(
            "Udhaar tracking is not available on the free plan. "
            "It is available on the Rs 99 plan for up to 5 customers.",
            language, shop_name, shop_type
        )

    extracted = ask_groq(
        f"""Extract credit/udhaar tracking details.
History: {history}
Message: "{message}"
Return ONLY valid JSON:
{{"customer_name": "name", "amount": 0.0,
  "action": "add", "description": "reason"}}
action options: add / paid / check / list
Return ONLY JSON:""",
        max_tokens=100, temperature=0
    )

    details = safe_json(extracted) or {
        "customer_name": "Customer",
        "amount": 0.0,
        "action": "add",
        "description": message
    }

    action        = details.get("action", "add")
    customer_name = details.get("customer_name", "Customer")
    amount        = float(details.get("amount", 0))

    if action == "list":
        all_udhaar = db.table("udhaar")\
            .select("*").eq("shop_id", shop["id"])\
            .eq("status", "pending").execute()
        total = sum(float(u.get("amount", 0)) for u in all_udhaar.data)
        names = ", ".join([
            f"{u['customer_name']} Rs {u['amount']}"
            for u in all_udhaar.data
        ]) if all_udhaar.data else "no pending credit"
        return generate_reply(
            f"Credit list: {names}. Total outstanding: Rs {total}.",
            language, shop_name, shop_type
        )

    if action == "check":
        rows = db.table("udhaar")\
            .select("*").eq("shop_id", shop["id"])\
            .ilike("customer_name", f"%{customer_name}%")\
            .eq("status", "pending").execute()
        total = sum(float(u.get("amount", 0)) for u in rows.data)
        return generate_reply(
            f"{customer_name} has a total pending credit of Rs {total}.",
            language, shop_name, shop_type
        )

    if action == "paid":
        db.table("udhaar")\
            .update({"status": "paid"})\
            .eq("shop_id", shop["id"])\
            .ilike("customer_name", f"%{customer_name}%")\
            .eq("status", "pending").execute()
        return generate_reply(
            f"{customer_name} has cleared their credit. Share this good news.",
            language, shop_name, shop_type
        )

    # ADD
    udhaar_limit   = get_limit(shop, "udhaar_persons")
    existing       = db.table("udhaar")\
        .select("customer_name")\
        .eq("shop_id", shop["id"])\
        .eq("status", "pending").execute()
    existing_names = list(set([
        u["customer_name"].lower() for u in existing.data
    ]))

    if customer_name.lower() not in existing_names:
        if len(existing_names) >= udhaar_limit:
            return generate_reply(
                f"Credit tracking limit of {udhaar_limit} customers reached "
                f"on the {plan} plan. "
                f"The Rs 199 plan offers unlimited credit tracking.",
                language, shop_name, shop_type
            )

    db.table("udhaar").insert({
        "shop_id":       shop["id"],
        "customer_name": customer_name,
        "amount":        amount,
        "description":   details.get("description", ""),
        "status":        "pending"
    }).execute()

    print(f"Udhaar: {customer_name} Rs {amount}", flush=True)
    return generate_reply(
        f"Credit entry saved. "
        f"Customer {customer_name} now owes Rs {amount}. "
        f"Digital ledger updated.",
        language, shop_name, shop_type
    )


# ─────────────────────────────────────────
# AGENT 4 — HELP AGENT
# ─────────────────────────────────────────
def help_agent(shop, language):
    shop_name = shop.get("name", "your shop")
    shop_type = shop.get("shop_type", "general")
    plan      = shop.get("plan", "free")

    features = {
        "free": (
            "record orders (10 per day), "
            "see today's order count and customer names"
        ),
        "plan99": (
            "unlimited orders, "
            "daily, weekly and monthly sales reports with revenue, "
            "top selling items, "
            "credit tracking for up to 5 customers"
        ),
        "plan199": (
            "everything in the Rs 99 plan, "
            "plus Gujarati and Marathi language support, "
            "unlimited credit tracking, "
            "stock management, "
            "GST invoices"
        ),
    }

    return generate_reply(
        f"Explain what Mittu can do for this {shop_type} business on their {plan} plan: "
        f"{features.get(plan, features['free'])}. "
        f"Give 2 natural examples. "
        f"Tell them to just type naturally — no special format needed.",
        language, shop_name, shop_type
    )


# ─────────────────────────────────────────
# MAIN WEBHOOK — ORCHESTRATOR
# ─────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    incoming_msg = request.form.get("Body", "").strip()
    sender_phone = request.form.get("From", "").replace("whatsapp:", "")

    print(f"\n{'='*50}", flush=True)
    print(f"From:    {sender_phone}", flush=True)
    print(f"Message: {incoming_msg}", flush=True)

    # Step 1 — get or create shop
    shop = get_or_create_shop(sender_phone)

    # Step 2 — detect language
    prev_lang = (shop.get("language") or "HINDI").upper()
    language  = detect_language(incoming_msg, prev_lang)
    print(f"Language: {language}", flush=True)

    update_shop(shop["id"], {"language": language.lower()})
    shop["language"] = language.lower()

    # Step 3 — conversation history
    history_raw = get_conversation_history(shop["id"], limit=5)
    history     = format_history(history_raw)

    # Step 4 — save incoming message
    save_message(shop["id"], "user", incoming_msg)

    # Step 5 — onboarding check
    if not shop.get("onboarded", False):
        step = shop.get("onboard_step", 0)
        if step <= 2:
            reply = handle_onboarding(incoming_msg, shop, language)
            save_message(shop["id"], "mittu", reply)
            send_whatsapp(sender_phone, reply)
            return "OK", 200

        intent_check = detect_intent(incoming_msg, history)
        if intent_check in ["ORDER", "REPORT", "UDHAAR"]:
            update_shop(shop["id"], {"onboarded": True})
            shop = get_or_create_shop(sender_phone)
        else:
            reply = handle_onboarding(incoming_msg, shop, language)
            save_message(shop["id"], "mittu", reply)
            send_whatsapp(sender_phone, reply)
            return "OK", 200

    shop_name = shop.get("name", "your shop")
    shop_type = shop.get("shop_type", "general")

    # Step 6 — language plan check
    if not language_allowed(shop, language):
        reply = language_upgrade_reply(language, shop, shop_name)
        save_message(shop["id"], "mittu", reply)
        send_whatsapp(sender_phone, reply)
        return "OK", 200

    # Step 7 — detect intent and route
    intent = detect_intent(incoming_msg, history)
    print(f"Intent: {intent}", flush=True)

    try:
        if intent == "GREETING":
            reply = greeting_reply(shop, language)
        elif intent == "ORDER":
            reply = order_agent(incoming_msg, shop, language, history)
        elif intent == "UPDATE":
            reply = order_update_agent(incoming_msg, shop, language, history)
        elif intent == "REPORT":
            reply = report_agent(incoming_msg, shop, language)
        elif intent == "UDHAAR":
            reply = udhaar_agent(incoming_msg, shop, language, history)
        elif intent == "HELP":
            reply = help_agent(shop, language)
        else:
            reply = generate_reply(
                f"Respond warmly to this message: '{incoming_msg}'. "
                f"If not business related, gently guide the owner to use Mittu "
                f"for recording orders, getting reports, or tracking credit.",
                language, shop_name, shop_type
            )

    except Exception as e:
        print(f"Agent error: {e}", flush=True)
        sys.stdout.flush()
        fallbacks = {
            "ENGLISH":  "Something went wrong. Please try again. - Mittu",
            "HINDI":    "Kuch gadbad hua. Dobara try karein. - Mittu",
            "HINGLISH": "Kuch issue hua. Please try again. - Mittu",
            "GUJARATI": "Koi samasya aayi. Pachhi try karo. - Mittu",
            "MARATHI":  "Kahi problem aali. Punha try kara. - Mittu",
        }
        reply = fallbacks.get(language, fallbacks["ENGLISH"])

    # Step 8 — save and send
    save_message(shop["id"], "mittu", reply)
    send_whatsapp(sender_phone, reply)
    print(f"Reply sent", flush=True)

    return "OK", 200


@app.route("/", methods=["GET"])
def home():
    return "Mittu is running!", 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
