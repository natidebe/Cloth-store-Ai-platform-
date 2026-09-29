"""Every fixed message our code sends to customers, in Amharic and English.

The AI writes its own replies in the customer's language. These are the
messages written by our code instead (the order summary, the payment
message, fixed replies), so they need both languages here.

To correct a translation, change the text in TEXTS below. Keep the
{placeholders} exactly as they are.

The store's own texts (payment instructions, return policy) are sent as the
store wrote them. Staff alerts stay in English.
"""
import re
from collections.abc import Iterable
from decimal import Decimal
from typing import Literal

Language = Literal["am", "en"]

TEXTS: dict[str, dict[Language, str]] = {
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
    "size": {"en": "size {size}", "am": "ቁጥር {size}"},

    # --- After the order is placed -------------------------------------------
    "order_placed": {
        "en": "✅ Order #{number} is placed. Total: {total}.",
        "am": "✅ ትዕዛዝ #{number} ተመዝግቧል። ጠቅላላ ዋጋ: {total}።",
    },
    "order_holding": {
        "en": "We're holding your items for {minutes} minutes.",
        "am": "ዕቃዎቹን ለ{minutes} ደቂቃ ይዘንልዎታል።",
    },
    "how_to_pay": {"en": "How to pay:", "am": "የክፍያ መንገድ:"},
    "payment_default": {
        "en": "Our team will send you the payment details shortly.",
        "am": "የክፍያ መረጃውን ቡድናችን በቅርቡ ይልክልዎታል።",
    },
    "after_paying": {
        "en": "After paying, please send a screenshot of the payment here.",
        "am": "ከከፈሉ በኋላ የክፍያውን ስክሪንሾት እዚህ ይላኩልን።",
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
    "empty_reply": {
        "en": "Sorry, could you say that again?",
        "am": "ይቅርታ፣ እባክዎ እንደገና ይጻፉልኝ?",
    },
    "fallback_reply": {
        "en": "Sorry, something went wrong on our side. Our team has been notified "
              "and will reply to you soon.",
        "am": "ይቅርታ፣ በእኛ በኩል ችግር ተፈጥሯል። ቡድናችን ተነግሮታል፤ በቅርቡ ይመልስልዎታል።",
    },

    # --- Prices -------------------------------------------------------------
    "currency": {"en": "ETB", "am": "ብር"},
    "price_not_set": {"en": "price not set", "am": "ዋጋ አልተወሰነም"},
}


def t(key: str, language: Language, **values: object) -> str:
    """The text for `key` in `language`, with {placeholders} filled in."""
    return TEXTS[key][language].format(**values)


def both(key: str, **values: object) -> str:
    """Amharic and English together, for when we don't know the language."""
    return f"{t(key, 'am', **values)}\n{t(key, 'en', **values)}"


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
