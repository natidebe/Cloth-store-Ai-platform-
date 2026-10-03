"""Every fixed message our code sends to customers, in Amharic and English.

This is the ONE config for the scripted order flow (D28): every question,
button label, and fixed reply lives here, never hard-coded in the handlers.
The AI only writes short answers to side questions.

To correct a translation, change the text in TEXTS below. Keep the
{placeholders} exactly as they are.

Per-store texts: for now all stores share TEXTS. t() already looks for a
store's own version first (store.text_overrides, {key: {language: text}}),
so a store can later replace any text by adding that field (a migration and
a dashboard form), without changing the flow.

The store's own texts (payment instructions, return policy) are sent as the
store wrote them. Staff alerts stay in English.
"""
import re
from collections.abc import Iterable
from decimal import Decimal
from typing import Literal

from app.agents.shop_types import text_values

Language = Literal["am", "en"]

TEXTS: dict[str, dict[Language, str]] = {
    # --- The scripted order flow (D28): one question per step ----------------
    "ask_product": {
        "en": "What are you looking for? Pick a category, or type the product name.",
        "am": "ምን እየፈለጉ ነው? ምድብ ይምረጡ፣ ወይም የምርቱን ስም ይጻፉ።",
    },
    "ask_product_no_categories": {
        "en": "What are you looking for? Type the product name.",
        "am": "ምን እየፈለጉ ነው? የምርቱን ስም ይጻፉ።",
    },
    "choose_language": {
        # Shown before the language is known, so it's in both languages.
        "en": "ቋንቋ ይምረጡ / Please choose your language",
        "am": "ቋንቋ ይምረጡ / Please choose your language",
    },
    "btn_lang_am": {"en": "አማርኛ", "am": "አማርኛ"},
    "btn_lang_en": {"en": "English", "am": "English"},
    "btn_change_language": {"en": "🌐 ቋንቋ / Language", "am": "🌐 ቋንቋ / Language"},
    "ask_product_pick": {"en": "Which one?", "am": "የትኛውን?"},
    "ask_color": {
        "en": "{product} — {price}. Which {opt1}?",
        "am": "{product} — {price}። የትኛው {opt1}?",
    },
    "price_from": {"en": "from {price}", "am": "ከ{price} ጀምሮ"},
    "ask_size": {"en": "Which {opt2}?", "am": "የትኛው {opt2}?"},
    "ask_quantity": {"en": "How many?", "am": "ስንት ይፈልጋሉ?"},
    "ask_delivery": {
        "en": "Delivery or pickup?",
        "am": "እናድርስልዎት ወይስ ከሱቁ ይወስዳሉ?",
    },
    "ask_address": {"en": "What's the delivery address?", "am": "የሚደርስበት አድራሻ የት ነው?"},
    "ask_name": {
        "en": "What's your name? ✍️ Type it in the message box below.",
        "am": "ስምዎ ማን ነው? ✍️ ከታች ባለው የመልእክት ሳጥን ውስጥ ይጻፉት።",
    },
    # Shown under the name question when we already know their name (a button).
    "ask_name_known": {
        "en": "Tap the button to use the name below, or type another one.",
        "am": "ከታች ያለውን ስም ለመጠቀም ቁልፉን ይጫኑ፣ ወይም ሌላ ስም ይጻፉ።",
    },
    "ask_phone": {
        "en": "📞 What's your phone number? Type it in the message box below, e.g. 0911 223 344.\n"
              "Our team uses it only to contact you about this order.",
        "am": "📞 ስልክ ቁጥርዎ ስንት ነው? ከታች ባለው የመልእክት ሳጥን ውስጥ ይጻፉት፣ ለምሳሌ 0911 223 344።\n"
              "ቡድናችን ስለዚህ ትዕዛዝ ብቻ ሊያገኝዎ ይጠቀምበታል።",
    },
    "ask_edit": {"en": "What would you like to change?", "am": "ምን መቀየር ይፈልጋሉ?"},
    # The cart (Phase 8d, D32): several items in one order.
    "ask_more": {
        "en": "🛒 Your cart:\n{items}\n\nWould you like to add another item?",
        "am": "🛒 የያዙት:\n{items}\n\nሌላ ዕቃ መጨመር ይፈልጋሉ?",
    },
    "ask_edit_items": {
        "en": "🛒 Your cart:\n{items}\n\nTap an item to remove it, or add another one.",
        "am": "🛒 የያዙት:\n{items}\n\nለማስወገድ ዕቃውን ይጫኑ፣ ወይም ሌላ ይጨምሩ።",
    },
    "btn_add_item": {"en": "➕ Add another item", "am": "➕ ሌላ ዕቃ ጨምር"},
    "btn_continue": {"en": "➡️ Continue", "am": "➡️ ቀጥል"},
    "btn_remove": {"en": "❌ {item}", "am": "❌ {item}"},
    "btn_edit_items": {"en": "🛒 Items", "am": "🛒 ዕቃዎች"},
    "ask_switch": {
        "en": "You're still choosing {current}. Finish it first ({new} comes next), or switch to {new} now?",
        "am": "{current}ን መምረጥ ገና አልጨረሱም። መጀመሪያ እሱን ይጨርሱ ({new} ይከተላል)፣ ወይስ አሁን ወደ {new} ይቀይሩ?",
    },
    "btn_switch_finish": {"en": "✅ Finish {product} first", "am": "✅ መጀመሪያ {product}ን ልጨርስ"},
    "btn_switch_now": {"en": "🔁 Switch to {product}", "am": "🔁 ወደ {product} ቀይር"},
    # From the channel (Phase 8d, D34)
    "sold_out_product": {
        "en": "Sorry, {product} is sold out.",
        "am": "ይቅርታ፣ {product} ተሽጧል።",
    },
    "similar_products": {"en": "Similar items you might like:", "am": "ሊወዷቸው የሚችሉ ተመሳሳይ ዕቃዎች:"},
    "summary_buttons": {
        "en": "Tap ✅ Confirm to place the order, or ✏️ Edit to change something.",
        "am": "ለማዘዝ ✅ አረጋግጥ ይጫኑ፤ ለመቀየር ✏️ አስተካክል ይጫኑ።",
    },
    "payment_waiting": {
        "en": "When you've paid, please send the payment screenshot here.",
        "am": "ከከፈሉ በኋላ እባክዎ የክፍያውን ስክሪንሾት እዚህ ይላኩ።",
    },

    # Buttons
    "btn_delivery": {"en": "🚚 Delivery", "am": "🚚 ይድረስልኝ"},
    "btn_pickup": {"en": "🏪 Pickup", "am": "🏪 ከሱቁ እወስዳለሁ"},
    "btn_use": {"en": "Use: {value}", "am": "ይሄን ይጠቀሙ: {value}"},
    "btn_confirm": {"en": "✅ Confirm", "am": "✅ አረጋግጥ"},
    "btn_edit": {"en": "✏️ Edit", "am": "✏️ አስተካክል"},
    "btn_start_over": {"en": "🔄 Start over", "am": "🔄 እንደገና ጀምር"},
    "btn_edit_product": {"en": "Product", "am": "ምርት"},
    "btn_edit_size": {"en": "{Opt2}", "am": "{Opt2}"},
    "btn_edit_color": {"en": "{Opt1}", "am": "{Opt1}"},
    "btn_edit_quantity": {"en": "Quantity", "am": "ብዛት"},
    "btn_edit_delivery": {"en": "Delivery / pickup", "am": "አደራረስ"},
    "btn_edit_contact": {"en": "Name & phone", "am": "ስም እና ስልክ"},
    "btn_back_to_summary": {"en": "↩️ Back to summary", "am": "↩️ ወደ ማጠቃለያው ተመለስ"},

    # Notes shown above the question
    "started_over": {"en": "OK, let's start again.", "am": "እሺ፣ እንደገና እንጀምር።"},
    "no_match": {
        "en": "Sorry, I couldn't find \"{query}\".",
        "am": "ይቅርታ፣ \"{query}\" አላገኘሁም።",
    },
    "size_unavailable": {
        "en": "Sorry, {opt2} {size} isn't available in that {opt1}.",
        "am": "ይቅርታ፣ {opt2} {size} በዚህ {opt1} የለም።",
    },
    "color_unavailable": {
        "en": "Sorry, {color} isn't available.",
        "am": "ይቅርታ፣ {color} የለም።",
    },
    # Delivery (D29): staff arrange the address by phone; the customer pays
    # when the items arrive.
    "summary_delivery_arranged": {
        "en": "Delivery: our team will call you to arrange the address. "
              "You pay when you receive your items.",
        "am": "ማድረስ፦ አድራሻውን ለማመቻቸት ቡድናችን ይደውልልዎታል። ክፍያው ዕቃዎቹን ሲረከቡ ነው።",
    },
    "delivery_handoff": {
        "en": "✅ Order #{number} is placed. Total: {total} (delivery fee not included).\n"
              "📞 Our team will call you on {phone} to arrange the delivery address.",
        "am": "✅ ትዕዛዝ #{number} ተመዝግቧል። ጠቅላላ ዋጋ: {total} (የማድረሻ ክፍያን አይጨምርም)።\n"
              "📞 የማድረሻ አድራሻውን ለማመቻቸት ቡድናችን በ{phone} ይደውልልዎታል።",
    },
    "delivery_fees": {"en": "🚚 Delivery areas and fees:", "am": "🚚 የማድረሻ ቦታዎችና ክፍያ:"},
    # Delivery orders are paid when the items arrive (D29), with the store's accounts.
    "pay_on_delivery": {
        "en": "💵 You pay when you receive your items.",
        "am": "💵 ክፍያው ዕቃዎቹን ሲረከቡ ነው።",
    },
    "pay_on_delivery_with": {
        "en": "💵 You pay when you receive your items. You can pay with:",
        "am": "💵 ክፍያው ዕቃዎቹን ሲረከቡ ነው። በእነዚህ መክፈል ይችላሉ:",
    },
    "too_many": {
        "en": "Sorry, we don't have that many. Please pick a smaller number.",
        "am": "ይቅርታ፣ ያን ያህል የለንም። እባክዎ ትንሽ ቁጥር ይምረጡ።",
    },
    "invalid_phone": {
        "en": "That doesn't look like a phone number. Please type it again, digits only, "
              "e.g. 0911 223 344.",
        "am": "ይህ ስልክ ቁጥር አይመስልም። እባክዎ እንደገና በቁጥሮች ብቻ ይጻፉት፣ ለምሳሌ 0911 223 344።",
    },
    "sold_out_now": {
        "en": "Sorry, {item} just sold out. Please pick another option.",
        "am": "ይቅርታ፣ {item} አሁን አልቋል። እባክዎ ሌላ ይምረጡ።",
    },
    "option_gone": {
        "en": "That option is no longer available.",
        "am": "ያ አማራጭ አሁን የለም።",
    },
    "photo_not_product": {
        "en": "I can't look at product photos. Please type the product name or pick a category.",
        "am": "የምርት ፎቶ ማየት አልችልም። እባክዎ የምርቱን ስም ይጻፉ ወይም ምድብ ይምረጡ።",
    },
    "please_type": {
        "en": "Please type your answer or use the buttons.",
        "am": "እባክዎ መልስዎን ይጻፉ ወይም ቁልፎቹን ይጠቀሙ።",
    },
    "handover_reply": {
        "en": "I'll get a team member to help you. They'll reply here soon.",
        "am": "የቡድናችን አባል እንዲረዳዎት አደርጋለሁ። በቅርቡ እዚህ ይመልሱልዎታል።",
    },
    "no_orders": {"en": "You don't have any orders yet.", "am": "እስካሁን ምንም ትዕዛዝ የለዎትም።"},
    "order_status_line": {"en": "Order #{number}: {status}", "am": "ትዕዛዝ #{number}: {status}"},
    "status_pending": {"en": "waiting for payment", "am": "ክፍያ በመጠባበቅ ላይ"},
    "status_confirmed": {"en": "paid, being prepared", "am": "ተከፍሏል፣ እየተዘጋጀ ነው"},
    "status_out_for_delivery": {"en": "on the way", "am": "በመንገድ ላይ ነው"},
    "status_delivered": {"en": "delivered", "am": "ደርሷል"},
    "status_cancelled": {"en": "cancelled", "am": "ተሰርዟል"},

    # --- Order summary ------------------------------------------------------
    "summary_title": {
        "en": "🧾 Order summary",
        "am": "🧾 የትዕዛዝ ማጠቃለያ",
    },
    "summary_total": {"en": "Total: {total}", "am": "ጠቅላላ ዋጋ: {total}"},
    "summary_name": {"en": "Name: {name}", "am": "ስም: {name}"},
    "summary_phone": {"en": "Phone: {phone}", "am": "ስልክ: {phone}"},
    "summary_delivery": {"en": "Delivery to: {address}", "am": "የሚደርስበት አድራሻ: {address}"},
    "summary_pickup": {"en": "Pickup at the store", "am": "ከሱቁ ይወሰዳል"},
    "summary_confirm": {
        "en": 'Reply "yes" (አዎ) to confirm, or tell me what to change.',
        "am": 'ለማረጋገጥ "አዎ" (yes) ብለው ይመልሱ፤ መቀየር የሚፈልጉት ነገር ካለ ይንገሩኝ።',
    },
    "size": {"en": "{opt2} {size}", "am": "{opt2} {size}"},

    # --- After the order is placed -------------------------------------------
    "order_placed": {
        "en": "✅ Order #{number} is placed. Total: {total}.",
        "am": "✅ ትዕዛዝ #{number} ተመዝግቧል። ጠቅላላ ዋጋ: {total}።",
    },
    "order_holding": {
        "en": "We're holding your items for {minutes} minutes.",
        "am": "ዕቃዎቹን ለ{minutes} ደቂቃ ይዘንልዎታል።",
    },
    "how_to_pay": {"en": "💵 How to pay:", "am": "💵 የክፍያ መንገድ:"},
    # Pickup orders: where and when, from the store's profile (if set).
    "pickup_where": {"en": "📍 Pick up at: {location}", "am": "📍 የሚወስዱበት ቦታ: {location}"},
    "pickup_hours": {"en": "🕒 Opening hours: {hours}", "am": "🕒 የሥራ ሰዓት: {hours}"},
    "payment_default": {
        "en": "Our team will send you the payment details shortly.",
        "am": "የክፍያ መረጃውን ቡድናችን በቅርቡ ይልክልዎታል።",
    },
    "after_paying": {
        "en": "After paying, please send a screenshot of the payment here.",
        "am": "ከከፈሉ በኋላ የክፍያውን ስክሪንሾት እዚህ ይላኩልን።",
    },

    # Sent only after a STAFF member confirmed the payment (Phase 9).
    "payment_confirmed": {
        "en": "✅ Payment confirmed for order #{number}. Thank you! We're preparing your order.",
        "am": "✅ ለትዕዛዝ #{number} ክፍያዎ ተረጋግጧል። እናመሰግናለን! ትዕዛዝዎን እያዘጋጀን ነው።",
    },

    # --- Fixed replies ------------------------------------------------------
    # Deliberately says nothing about the payment: only staff confirm it.
    "photo_reply": {
        "en": "We received your photo 🙏 A team member will check it and reply here soon.",
        "am": "ፎቶዎን ተቀብለናል 🙏 የቡድናችን አባል አይቶ በቅርቡ እዚህ ይመልስልዎታል።",
    },
    "stuck_reply": {
        "en": "Let me get a team member to help you with this. They'll reply here soon.",
        "am": "በዚህ ጉዳይ የቡድናችን አባል እንዲረዳዎት አደርጋለሁ። በቅርቡ እዚህ ይመልሱልዎታል።",
    },
    # Too many messages in a minute (Phase 10): the extra ones are ignored.
    "slow_down": {
        "en": "You're sending messages very quickly. Please wait a minute, then send your message again.",
        "am": "መልእክቶችን በጣም በፍጥነት እየላኩ ነው። እባክዎ አንድ ደቂቃ ቆይተው መልእክትዎን እንደገና ይላኩ።",
    },
    # A store waiting for approval (D14) or suspended: no orders yet.
    "store_not_open": {
        "en": "Sorry, this shop isn't taking orders yet. Please check back soon.",
        "am": "ይቅርታ፣ ይህ ሱቅ ገና ትዕዛዝ አይቀበልም። እባክዎ ቆይተው ይሞክሩ።",
    },
    "empty_reply": {
        "en": "Sorry, could you say that again?",
        "am": "ይቅርታ፣ እባክዎ እንደገና ይጻፉልኝ?",
    },
    "fallback_reply": {
        "en": "Sorry, something went wrong on our side. Our team has been notified "
              "and will reply to you soon.",
        "am": "ይቅርታ፣ በእኛ በኩል ችግር ተፈጥሯል። ቡድናችን ተነግሮታል፤ በቅርቡ ይመልስልዎታል።",
    },

    # --- How to use the bot -------------------------------------------------
    # /help in the chat, in the customer's language.
    "help": {
        "en": "ℹ️ How to order:\n"
              "1. Tap 🛒 Order on a post in our channel, or type a product name or code (e.g. P101).\n"
              "2. Choose the {opt1}, {opt2} and quantity with the buttons.\n"
              "3. Add more items or tap ➡️ Continue, then choose 🚚 Delivery or 🏪 Pickup.\n"
              "4. Check the summary and tap ✅ Confirm.\n\n"
              "💵 Pickup: pay with the details we send you, then send a screenshot here.\n"
              "💵 Delivery: you pay when you receive your items.\n\n"
              "🔄 Start over anytime. Questions? Just write, and our team will answer.",
        "am": "ℹ️ እንዴት ማዘዝ ይቻላል:\n"
              "1. በቻናላችን ላይ ያለውን 🛒 እዘዝ ይጫኑ፣ ወይም የምርቱን ስም ወይም ኮድ (ለምሳሌ P101) ይጻፉ።\n"
              "2. {opt1}፣ {opt2}ና ብዛት በቁልፎቹ ይምረጡ።\n"
              "3. ሌላ ዕቃ ይጨምሩ ወይም ➡️ ቀጥል ይጫኑ፤ ከዚያ 🚚 ይድረስልኝ ወይም 🏪 ከሱቁ እወስዳለሁ ይምረጡ።\n"
              "4. ማጠቃለያውን አይተው ✅ አረጋግጥ ይጫኑ።\n\n"
              "💵 ከሱቁ ሲወስዱ: በምንልክልዎት የክፍያ መረጃ ከፍለው ስክሪንሾቱን እዚህ ይላኩ።\n"
              "💵 ሲደርስልዎ: ክፍያው ዕቃዎቹን ሲረከቡ ነው።\n\n"
              "🔄 በማንኛውም ጊዜ እንደገና መጀመር ይችላሉ። ጥያቄ ካለዎት ይጻፉልን፤ ቡድናችን ይመልሳል።",
    },
    # The bot's Telegram profile (set by scripts/connect_store.py). Both
    # languages go into one text: Telegram picks a description by the app's
    # language, and few people use Telegram in Amharic.
    "bot_description": {
        "en": "👋 Welcome to {shop}!\n"
              "🛒 Tap Order on a post in our channel, or type a product name. "
              "Choose {opt1}, {opt2} and quantity, then confirm. Type /help anytime.",
        "am": "👋 እንኳን ወደ {shop} በደህና መጡ!\n"
              "🛒 በቻናላችን ላይ እዘዝ ይጫኑ፣ ወይም የምርቱን ስም ይጻፉ። "
              "{opt1}፣ {opt2}ና ብዛት መርጠው ያረጋግጡ። እርዳታ ከፈለጉ /help ይጻፉ።",
    },
    "bot_short_description": {"en": "Order from {shop} in a few taps.", "am": "ከ{shop} በቀላሉ ይዘዙ።"},
    "command_start": {"en": "Start an order", "am": "ትዕዛዝ ጀምር"},
    "command_help": {"en": "How to order", "am": "እንዴት ማዘዝ ይቻላል"},

    # --- Prices -------------------------------------------------------------
    "currency": {"en": "ETB", "am": "ብር"},
    "price_not_set": {"en": "price not set", "am": "ዋጋ አልተወሰነም"},
}


def t(key: str, language: Language, store: object | None = None, **values: object) -> str:
    """The text for `key` in `language`, with {placeholders} filled in.

    If the store has its own version of this text (a future
    store.text_overrides field: {key: {language: text}}), that one is used.
    """
    overrides = getattr(store, "text_overrides", None) or {}
    template = (overrides.get(key) or {}).get(language) or TEXTS[key][language]
    if "{opt" in template or "{Opt" in template:
        # The shop's names for the two product options (Phase 13, shop_types.py).
        values = {**text_values(store, language), **values}
    return template.format(**values)


def both(key: str, store: object | None = None, **values: object) -> str:
    """Amharic and English together, for when we don't know the language."""
    return f"{t(key, 'am', store, **values)}\n{t(key, 'en', store, **values)}"


def bot_profile(shop: str, store: object | None = None) -> tuple[str, str, list[tuple[str, str]]]:
    """The bot's Telegram profile, Amharic first: its description (shown
    before Start), short description, and command menu. `store` gives the
    shop's words for its options (Phase 13)."""
    shop = shop.strip()[:40]
    description = (f"{t('bot_description', 'am', store, shop=shop)}\n\n"
                   f"{t('bot_description', 'en', store, shop=shop)}")
    short = f"{t('bot_short_description', 'am', shop=shop)} {t('bot_short_description', 'en', shop=shop)}"
    commands = [(name, f"{t(f'command_{name}', 'am')} / {t(f'command_{name}', 'en')}")
                for name in ("start", "help")]
    return description, short, commands


def format_price(price: Decimal | None, language: Language = "en") -> str:
    """E.g. "4,500 ETB" or "4,500 ብር"."""
    if price is None:
        return t("price_not_set", language)
    amount = f"{price:,.0f}" if price == price.to_integral_value() else f"{price:,.2f}"
    return f"{amount} {t('currency', language)}"


# ---------------------------------------------------------------------------
# Which language is the customer writing in?
# ---------------------------------------------------------------------------

_ETHIOPIC = re.compile(r"[ሀ-፿]")  # Amharic (Ge'ez) script
_LATIN_WORD = re.compile(r"[a-z']+")

# Short replies that say nothing about the language: a customer chatting in
# Amharic who types "yes" or "ok" should keep getting Amharic.
_NEUTRAL_WORDS = {
    "yes", "yeah", "yep", "ok", "okay", "no", "sure", "hi", "hello", "hey",
    "thanks", "thank", "you", "thx", "please", "pls", "confirm", "cancel",
    "size", "cm", "etb", "birr", "br",
}
# Common Amharic words written in Latin letters ("Amharic in English letters").
# Words that are also English (new, ale, gin, min, ...) are left out on purpose.
_ROMANIZED_AMHARIC = {
    "awo", "awon", "ishi", "eshi", "selam", "salam", "endet", "nachu", "nesh",
    "alachu", "alachihu", "betam", "amesegnalehu", "ameseginalehu", "amesegnalew",
    "ebakih", "ebakish", "ebakachu", "dehna", "yistilign", "sint",
    "yelem", "yelelem", "hulet", "kutir", "chama", "shemiz",
}


# A Latin-letter message counts as English only if it's a real sentence:
# Amharic speakers often type single English words ("delivery", "pickup"),
# names ("abdi"), and places ("Addis Ababa") in the middle of an Amharic chat.
_MIN_ENGLISH_WORDS = 3


def message_language(text: str | None) -> Language | None:
    """The language of one message, or None if it doesn't tell (e.g. "yes",
    "0911223344", "👍", "delivery", "Addis Ababa")."""
    if not text:
        return None
    if _ETHIOPIC.search(text):
        return "am"
    words = _LATIN_WORD.findall(text.lower())
    if any(word in _ROMANIZED_AMHARIC for word in words):
        return "am"
    english_words = [word for word in words if word not in _NEUTRAL_WORDS]
    if len(english_words) < _MIN_ENGLISH_WORDS:
        return None
    return "en"


def detect_language(
    texts_newest_first: Iterable[str | None], telegram_language_code: str | None = None
) -> Language:
    """The language of the most recent message that tells; otherwise the
    customer's Telegram language setting; otherwise English."""
    for text in texts_newest_first:
        language = message_language(text)
        if language:
            return language
    if telegram_language_code and telegram_language_code.lower().startswith("am"):
        return "am"
    return "en"
