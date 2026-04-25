import os
import sys
import json
from flask import Flask, request
from twilio.rest import Client
from supabase import create_client
from dotenv import load_dotenv
from datetime import date
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
# Free/99  → English + Hindi + Hinglish
# Rs 199   → + Gujarati + Marathi
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
        message = "Kuch technical problem aayi. Thodi der mein try karein. - Mittu"
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
                        "You are Mittu, a helpful WhatsApp assistant "
                        "for small Indian businesses. "
                        "Follow all instructions in the user prompt exactly, "
                        "especially the language instruction."
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
        f"{'Shop owner' if m['role']=='user' else 'Mittu'}: {m['message']}"
        for m in messages
    ])


# ─────────────────────────────────────────
# LANGUAGE DETECTOR — Groq based
# More accurate than rule-based for Roman
# ─────────────────────────────────────────
def detect_language(message, prev_language=None):
    # Script detection first — always accurate
    if any('\u0900' <= c <= '\u097F' for c in message):
        return "HINDI"
    if any('\u0A80' <= c <= '\u0AFF' for c in message):
        return "GUJARATI"

    # Groq for Roman script
    try:
        result = ask_groq(
            f"""What language is this message written in?

Message: "{message}"

Choose exactly one:
- ENGLISH: pure English, no Hindi words
- HINDI: Hindi in Roman letters (kaise ho, theek hai, aata, daal)
- HINGLISH: mix of Hindi and English in same message
- GUJARATI: Gujarati words (kem chhe, su chhe, tamaro, chho)
- MARATHI: Marathi words (kay aahe, mala, tumhi, hoil)

Previous language: {prev_language or 'unknown'}
Note: "Hi", "Hello", "Ok", "Yes", "No" alone = ENGLISH

Return ONLY one word:""",
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
            f"""Classify this WhatsApp message from a small business owner.
Return ONLY one word — no explanation.

Recent conversation:
{history}

Message: "{message}"

GREETING = hello, hi, how are you, namaste, kem chhe, kaise ho
ORDER    = recording that someone bought/took something
           Examples: "Suresh 2kg aata le gaya", "Priya ka order 1L milk",
           "anil ne dawa li", "customer took goods"
REPORT   = asking for sales summary, daily/weekly report
UDHAAR   = tracking credit/udhaar given to customer
UPDATE   = adding price or updating details of a PREVIOUS order
           (usually follows an ORDER in conversation history)
HELP     = asking what Mittu can do
CHAT     = general questions, anything else

Important:
- If message mentions price/Rs for something from history → UPDATE
- Natural business sentences like "X ne Y liya" or "X took Y" → ORDER

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
# REPLY GENERATOR
# ─────────────────────────────────────────
def generate_reply(context, language, shop_name, shop_type="general"):
    lang_instruction = {
        "ENGLISH":  "ENGLISH ONLY. Every single word must be English. Do NOT use aap, ki, ka, hai, aur, aapka, hoga, mil, gaya, or any Hindi/Urdu word whatsoever.",
        "HINDI":    "Hindi only. Roman Hindi is fine. Do not use English sentences.",
        "HINGLISH": "Natural Hinglish — mix Hindi and English naturally like Indians talk.",
        "GUJARATI": "Gujarati only.",
        "MARATHI":  "Marathi only.",
    }.get(language, "Hindi only.")

    try:
        return ask_groq(
            f"""⚠️ STRICT LANGUAGE RULE — YOU MUST REPLY IN {language} ONLY: {lang_instruction}

You are Mittu — a respectful WhatsApp assistant for small Indian businesses.
Shop: {shop_name} ({shop_type})

Rules:
- LANGUAGE: {language} ONLY — Do NOT switch to any other language under any circumstance.
- NEVER say "bhai" or "arre"
- Talk TO the shop owner only — never address their customers
- Warm and professional — like a trusted assistant
- MAX 3 lines — short and clear
- No bullet points
- End with: - Mittu

What to say: {context}

Write one short reply in {language} only:""",
            max_tokens=200,
            temperature=0.3
        )
    except:
        fallbacks = {
            "HINDI":    "Kuch technical gadbad hua. Thodi der baad try karein. - Mittu",
            "GUJARATI": "Thodi technical samasya. Pachhi try karo. - Mittu",
            "MARATHI":  "Thodi technical samasya. Punha try kara. - Mittu",
            "ENGLISH":  "Something went wrong. Please try again. - Mittu",
            "HINGLISH": "Kuch issue aa gayi. Thodi der mein try karo. - Mittu",
        }
        return fallbacks.get(language, fallbacks["HINDI"])


# ─────────────────────────────────────────
# GREETING HANDLER
# Mittu greets back warmly
# ─────────────────────────────────────────
def greeting_reply(shop, language):
    shop_name  = shop.get("name", "aapki dukaan")
    shop_type  = shop.get("shop_type", "general")
    owner_name = shop.get("owner_name", "")
    plan       = shop.get("plan", "free")

    name_text  = f"namaste {owner_name}ji" if owner_name else "namaste"

    features_hint = {
        "free":    "orders aur basic report",
        "plan99":  "orders, reports aur udhaar tracking",
        "plan199": "orders, reports, udhaar aur stock management",
    }.get(plan, "orders aur reports")

    return generate_reply(
        f"Owner greeted Mittu. Greet back warmly. "
        f"Address them as '{name_text}' if name known, else just greet warmly. "
        f"In 1-2 lines say you are ready to help with {features_hint}. "
        f"Do not list every feature — keep it conversational.",
        language, shop_name, shop_type
    )


# ─────────────────────────────────────────
# LANGUAGE UPGRADE PROMPT
# ─────────────────────────────────────────
def language_upgrade_reply(language, shop, shop_name):
    lang_display = {
        "GUJARATI": "Gujarati",
        "MARATHI":  "Marathi",
    }.get(language, language.title())
    plan = shop.get("plan", "free")

    # Always reply in English for upgrade messages
    # to avoid garbled mixed-language text
    return generate_reply(
        f"{lang_display} language support is available on the Rs 199 plan. "
        f"Currently on {plan} plan — Hindi and English are supported. "
        f"Tell owner politely and suggest upgrading for {lang_display} support.",
        "ENGLISH", shop_name
    )


# ─────────────────────────────────────────
# CONFUSION HANDLER
# ─────────────────────────────────────────
def confusion_reply(language, shop_name):
    return generate_reply(
        "Did not understand this message clearly. "
        "Apologize briefly and ask to repeat with more detail. "
        "Give one example appropriate for their business.",
        language, shop_name
    )


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
# ONBOARDING FLOW
#
# Step 0 → Welcome + ask shop name
# Step 1 → Save name + ask shop type
# Step 2 → Save shop type + ask owner name
# Step 3 → Save owner name + complete
#
# Now collects: shop name, shop type, owner name
# Works for all business types not just kirana
# ─────────────────────────────────────────
def handle_onboarding(message, shop, language):
    step         = shop.get("onboard_step", 0)
    onboard_lang = "ENGLISH" if language == "ENGLISH" else "HINDI"
    lang_text    = "English" if onboard_lang == "ENGLISH" else "Hindi (Roman script)"

    if step == 0:
        update_shop(shop["id"], {"onboard_step": 1})
        return ask_groq(
            f"""You are Mittu, a WhatsApp assistant for small Indian businesses.
Reply in: {lang_text}

Write a warm short welcome message.
Say Mittu helps any small business with:
orders tracking, daily sales reports, udhaar (credit) tracking.
Ask: "Aapki dukaan ya business ka naam kya hai?"
Use "aap". Never "bhai". Max 3 lines. End: - Mittu""",
            max_tokens=150, temperature=0.5
        )

    elif step == 1:
        # Save shop name
        raw       = message.strip()
        extracted = ask_groq(
            f"""Extract only the business or shop name from this message.
Message: "{raw}"
Examples:
"Sharma Medical Store" → Sharma Medical Store
"mera naam JRH hai" → JRH
"ABC Hardware" → ABC Hardware
Return ONLY the name, nothing else:""",
            max_tokens=30, temperature=0
        ).strip().title()

        shop_name = extracted if 0 < len(extracted) < 60 else raw.title()
        update_shop(shop["id"], {"name": shop_name, "onboard_step": 2})

        return ask_groq(
            f"""You are Mittu.
Reply in: {lang_text}
Shop name: {shop_name}

Great, you have the shop name. Welcome them.
Now ask what TYPE of business it is.
Give examples: kirana, medical/pharmacy, dairy, 
vegetables, hardware, clothing, restaurant, salon, etc.
Say "Aapka business kis type ka hai?"
Use "aap". Max 3 lines. End: - Mittu""",
            max_tokens=150, temperature=0.5
        )

    elif step == 2:
        # Save shop type
        raw       = message.strip()
        # Extract a clean shop type
        extracted = ask_groq(
            f"""Identify the type of business from this message.
Message: "{raw}"

Return one of these or a similar short label:
kirana, medical, dairy, vegetables, hardware, 
clothing, restaurant, salon, electronics, stationery,
bakery, furniture, auto-parts, general

Return ONLY the type word — nothing else:""",
            max_tokens=15, temperature=0
        ).strip().lower()

        shop_type = extracted if extracted else "general"
        shop_name = shop.get("name", "aapki dukaan")
        update_shop(shop["id"], {"shop_type": shop_type, "onboard_step": 3})

        return ask_groq(
            f"""You are Mittu.
Reply in: {lang_text}
Shop: {shop_name} ({shop_type})

Acknowledge the shop type warmly.
Now ask the owner's name:
"Aapka naam kya hai?"
Use "aap". Max 2 lines. End: - Mittu""",
            max_tokens=100, temperature=0.5
        )

    else:
        # Save owner name and complete onboarding
        raw       = message.strip()
        extracted = ask_groq(
            f"""Extract only the person's first name.
Message: "{raw}"
Examples: "mera naam Rahul hai" → Rahul | "I am Priya" → Priya
Return ONLY the first name:""",
            max_tokens=15, temperature=0
        ).strip().title()

        owner_name = extracted if 0 < len(extracted) < 30 else raw.title()
        shop_name  = shop.get("name", "aapki dukaan")
        shop_type  = shop.get("shop_type", "general")

        update_shop(shop["id"], {
            "owner_name": owner_name,
            "onboarded":  True
        })

        return ask_groq(
            f"""You are Mittu.
Reply in: {lang_text}
Owner: {owner_name}
Shop: {shop_name} ({shop_type})

CRITICAL LANGUAGE RULE:
- If lang_text is English → use ONLY pure English. Never write "aap", "ki", "ka", "hai", "aur" in English replies.
- If lang_text is Hindi → use Hindi or Hinglish naturally.

Welcome owner by name warmly. Say Mittu is ready for their {shop_type} business.
Give 3 natural examples relevant to their business type:
- For order: "[Customer name] ne [item] liya" or "[Customer] ka order [items]"
- For report: "today's report" or "aaj ka report dikhao"
- For udhaar: "[Customer] ko Rs [amount] ka udhaar diya" (paid plan)
Say type naturally — no special format needed.
Max 5 lines. End: - Mittu""",
            max_tokens=280, temperature=0.5
        )
# ─────────────────────────────────────────
# AGENT 1 — ORDER AGENT
#
# Auto-calculates total when multiple items
# have individual prices mentioned
# ─────────────────────────────────────────
def order_agent(message, shop, language, history=""):
    shop_name = shop.get("name", "aapki dukaan")
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
            f"Tell politely. Suggest Rs 99 for unlimited.",
            language, shop_name, shop_type
        )

    # Extract order with auto-calculation
    extracted = ask_groq(
        f"""Extract order details from this business owner message.
Business type: {shop_type}
Use history for references like "uska", "iska".

History: {history}
Message: "{message}"

IMPORTANT — Auto calculate total:
If multiple items each with their own price are mentioned,
add them up and put total in "amount" field.
Example: "2kg daal Rs 100 aur dudh Rs 50" → amount = 150.0

Return ONLY valid JSON:
{{"customer_name": "name",
  "items": "full items description with individual prices",
  "amount": 0.0,
  "item_breakdown": "item1 Rs X, item2 Rs Y"}}

- customer_name: who bought / who the order is for
- items: what was bought (keep original text)
- amount: TOTAL price (sum of all items if multiple prices given)
- item_breakdown: each item with its price if multiple items
- Use 0.0 if no price mentioned at all

Return ONLY JSON:""",
        max_tokens=200, temperature=0
    )

    details = safe_json(extracted) or {
        "customer_name": "Customer",
        "items":         message,
        "amount":        0.0,
        "item_breakdown": ""
    }

    cust       = str(details.get("customer_name", "Customer")).strip()
    items      = str(details.get("items", message)).strip()
    amount     = float(details.get("amount", 0))
    breakdown  = str(details.get("item_breakdown", "")).strip()

     # NEW — if items is just the original vague message
    # it means no real order was found — ask for details
    vague_phrases = [
        "can you take", "please take", "note order",
        "ek order", "order lena", "order lo", "order chahiye"
    ]
    if any(p in items.lower() for p in vague_phrases) or items.lower() == message.lower():
        return generate_reply(
            "Owner wants to place an order but did not give details yet. "
            "Ask them: who is the customer and what do they want to order? "
            "Be warm and brief. Do NOT save anything.",
            language, shop_name, shop_type
        )

    db.table("orders").insert({
        "shop_id":       shop["id"],
        "customer_name": cust,
        "items":         items,
        "amount":        amount,
        "status":        "new"
    }).execute()

    print(f"Order: {cust} | {items} | Rs {amount}", flush=True)

    # Build confirmation context
    if amount > 0 and breakdown:
        confirm_context = (
            f"Order confirmed. Tell SHOP OWNER in their language: "
            f"Customer '{cust}' ordered {breakdown}. Total is Rs {amount:.0f}. Confirmed."
            f"Keep to 1 line. Use ONLY the language specified."
        )
    elif amount > 0:
        confirm_context = (
            f"Order saved. Tell SHOP OWNER in their language: "
            f"'{cust}' ordered {items}. Amount Rs {amount:.0f}. Confirmed."
            f"1 line only. Use ONLY the language specified."
        )
    else:
        confirm_context = (
            f"Order saved, no price yet. Tell SHOP OWNER in their language: "
            f"'{cust}' order saved for {items}. "
            f"Ask them to add price like: '{cust} order price is Rs XX'. "
            f"2 lines max. Use ONLY the language specified."
        )

    return generate_reply(confirm_context, language, shop_name, shop_type)


# ─────────────────────────────────────────
# ORDER UPDATE AGENT
# Links price back to original order items
# ─────────────────────────────────────────
def order_update_agent(message, shop, language, history=""):
    shop_name = shop.get("name", "aapki dukaan")
    shop_type = shop.get("shop_type", "general")

    extracted = ask_groq(
        f"""Owner is updating a previous order — likely adding price.
Use history to identify which customer's order.

History:
{history}

Message: "{message}"

Return ONLY valid JSON:
{{"customer_name": "name", "field": "amount", "value": "new value"}}

field: amount / items / status
- price/Rs mentioned → field is "amount"
- Use history to find customer name
Return ONLY JSON:""",
        max_tokens=100, temperature=0
    )

    details = safe_json(extracted)
    if not details or not details.get("customer_name"):
        return confusion_reply(language, shop_name)

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

    print(f"Updated: {order_id} {update_data}", flush=True)

    new_amount = update_data.get("amount", value)

    return generate_reply(
        f"Order price updated. Confirm to SHOP OWNER: "
        f"Customer '{customer}' ordered {order_items}, total Rs {new_amount}. Confirmed. "
        f"Show both items and final price. 1 line only.",
        language, shop_name, shop_type
    )


# ─────────────────────────────────────────
# AGENT 2 — REPORT AGENT
# ─────────────────────────────────────────
def report_agent(message, shop, language):
    shop_name = shop.get("name", "aapki dukaan")
    shop_type = shop.get("shop_type", "general")
    plan      = shop.get("plan", "free")
    msg_lower = message.lower()

    if any(w in msg_lower for w in ["week", "hafte", "weekly", "hafta", "saptah"]):
        report_type  = "weekly"
        period_label = "This week"
        start_date   = __import__('datetime').date.today().__class__.today()
        from datetime import timedelta
        start_date = (date.today() - timedelta(days=7)).isoformat()
    elif any(w in msg_lower for w in ["month", "mahine", "monthly", "mahina"]):
        report_type  = "monthly"
        period_label = "This month"
        start_date   = date.today().replace(day=1).isoformat()
    else:
        report_type  = "daily"
        period_label = "Today"
        start_date   = date.today().isoformat()

    orders = db.table("orders")\
        .select("*").eq("shop_id", shop["id"])\
        .gte("created_at", start_date).execute()

    total_orders  = len(orders.data)
    total_revenue = sum(float(o.get("amount", 0)) for o in orders.data)

    if plan == "free":
        return generate_reply(
            f"{period_label}: {total_orders} orders recorded via WhatsApp. "
            f"Revenue details available on Rs 99 plan. "
            f"Tell count warmly — no pushy upgrade message.",
            language, shop_name, shop_type
        )

    item_counts = {}
    for o in orders.data:
        for item in o.get("items", "").split(","):
            item = item.strip()
            if item:
                item_counts[item] = item_counts.get(item, 0) + 1

    top_items = sorted(item_counts.items(), key=lambda x: x[1], reverse=True)[:3]
    top_text  = ", ".join([f"{i[0]} ({i[1]}x)" for i in top_items]) \
                if top_items else "no data yet"

    return generate_reply(
        f"{period_label}: {total_orders} orders, "
        f"Rs {total_revenue:.0f} total revenue. "
        f"Top items: {top_text}. "
        f"Give warm encouraging summary. Max 4 lines.",
        language, shop_name, shop_type
    )


# ─────────────────────────────────────────
# AGENT 3 — UDHAAR AGENT
# ─────────────────────────────────────────
def udhaar_agent(message, shop, language, history=""):
    shop_name = shop.get("name", "aapki dukaan")
    shop_type = shop.get("shop_type", "general")
    plan      = shop.get("plan", "free")

    if plan == "free":
        return generate_reply(
            "Udhaar tracking not on free plan. "
            "Tell politely. Rs 99 plan mein 5 customers ka udhaar milega.",
            language, shop_name, shop_type
        )

    extracted = ask_groq(
        f"""Extract udhaar details.
History: {history}
Message: "{message}"
Return ONLY valid JSON:
{{"customer_name": "name", "amount": 0.0,
  "action": "add", "description": "reason"}}
action: add / paid / check / list
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
        ]) if all_udhaar.data else "no pending udhaar"
        return generate_reply(
            f"Udhaar list: {names}. Total: Rs {total}. Present clearly.",
            language, shop_name, shop_type
        )

    if action == "check":
        rows = db.table("udhaar")\
            .select("*").eq("shop_id", shop["id"])\
            .ilike("customer_name", f"%{customer_name}%")\
            .eq("status", "pending").execute()
        total = sum(float(u.get("amount", 0)) for u in rows.data)
        return generate_reply(
            f"{customer_name} owes Rs {total} total. Tell owner clearly.",
            language, shop_name, shop_type
        )

    if action == "paid":
        db.table("udhaar")\
            .update({"status": "paid"})\
            .eq("shop_id", shop["id"])\
            .ilike("customer_name", f"%{customer_name}%")\
            .eq("status", "pending").execute()
        return generate_reply(
            f"{customer_name} paid their udhaar. Good news for owner!",
            language, shop_name, shop_type
        )

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
                f"Udhaar limit of {udhaar_limit} reached on {plan}. "
                f"Tell politely. Rs 199 for unlimited.",
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
        f"Udhaar saved. {customer_name} owes Rs {amount}. "
        f"Digital bahi khata updated. 1 line.",
        language, shop_name, shop_type
    )


# ─────────────────────────────────────────
# AGENT 4 — HELP AGENT
# ─────────────────────────────────────────
def help_agent(shop, language):
    shop_name = shop.get("name", "aapki dukaan")
    shop_type = shop.get("shop_type", "general")
    plan      = shop.get("plan", "free")

    features = {
        "free": "orders (10/day) in Hindi+English, basic daily count",
        "plan99": (
            "unlimited orders, daily+weekly+monthly sales report, "
            "top selling items, udhaar for 5 customers"
        ),
        "plan199": (
            "everything in Rs 99 + Gujarati+Marathi language, "
            "unlimited udhaar, stock management, "
            "GST invoice, monthly profit report"
        ),
    }

    return generate_reply(
        f"Tell owner Mittu features on their {plan} plan: "
        f"{features.get(plan, features['free'])}. "
        f"Give 2 natural examples for {shop_type} business. "
        f"Say type naturally — no format needed. Max 5 lines.",
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

    # Step 1 — shop
    shop = get_or_create_shop(sender_phone)

    # Step 2 — language
    prev_lang = (shop.get("language") or "HINDI").upper()
    language  = detect_language(incoming_msg, prev_lang)
    print(f"Language: {language}", flush=True)

    update_shop(shop["id"], {"language": language.lower()})
    shop["language"] = language.lower()

    # Step 3 — history
    history_raw = get_conversation_history(shop["id"], limit=5)
    history     = format_history(history_raw)

    # Step 4 — save message
    save_message(shop["id"], "user", incoming_msg)

    # Step 5 — ONBOARDING
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

    shop_name = shop.get("name", "aapki dukaan")
    shop_type = shop.get("shop_type", "general")

    # Step 6 — language plan check
    if not language_allowed(shop, language):
        reply = language_upgrade_reply(language, shop, shop_name)
        save_message(shop["id"], "mittu", reply)
        send_whatsapp(sender_phone, reply)
        return "OK", 200

    # Step 7 — ORCHESTRATOR
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
                f"Respond warmly to: '{incoming_msg}'. "
                f"If not business related, guide to use Mittu for "
                f"recording orders, getting reports, or tracking udhaar.",
                language, shop_name, shop_type
            )

    except Exception as e:
        print(f"Agent error: {e}", flush=True)
        sys.stdout.flush()
        fallbacks = {
            "HINDI":    "Kuch technical gadbad hua. Thodi der baad try karein. - Mittu",
            "GUJARATI": "Thodi technical samasya. Pachhi try karo. - Mittu",
            "MARATHI":  "Thodi technical samasya. Punha try kara. - Mittu",
            "ENGLISH":  "Something went wrong. Please try again. - Mittu",
            "HINGLISH": "Kuch issue aa gayi. Thodi der mein try karo. - Mittu",
        }
        reply = fallbacks.get(language, fallbacks["HINDI"])

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
