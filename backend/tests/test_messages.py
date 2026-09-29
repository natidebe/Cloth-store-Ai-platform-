"""Phase 8b: fixed messages in Amharic and English, and language detection."""
import string
from decimal import Decimal

import pytest

from app.agents.messages import TEXTS, both, detect_language, format_price, message_language, t


def _placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


@pytest.mark.parametrize("key", sorted(TEXTS))
def test_every_text_has_both_languages_with_the_same_placeholders(key):
    versions = TEXTS[key]
    assert set(versions) == {"am", "en"}
    assert all(v.strip() for v in versions.values())
    # A translation must keep the same {placeholders}, or the message breaks.
    assert _placeholders(versions["am"]) == _placeholders(versions["en"])


def test_amharic_texts_are_really_amharic():
    for key, versions in TEXTS.items():
        if key in ("currency",):
            continue
        assert message_language(versions["am"]) == "am", key


@pytest.mark.parametrize("text, expected", [
    ("ጫማ አላችሁ?", "am"),
    ("white AF1 ቁጥር 43 ስንት ነው?", "am"),      # mixed: Amharic wins
    ("selam, chama alachu?", "am"),             # Amharic in Latin letters
    ("Do you have white Air Force 1?", "en"),
    ("I want new shoes", "en"),                 # "new" is English here
    ("yes", None), ("ok 👍", None), ("0911223344", None), ("", None), (None, None),
])
def test_message_language(text, expected):
    assert message_language(text) == expected


def test_short_replies_keep_the_earlier_language():
    # Newest first: "yes" doesn't tell, the Amharic message before it does.
    assert detect_language(["yes", "ok", "ነጩን ቁጥር 42 እፈልጋለሁ"]) == "am"
    assert detect_language(["yes", "I'll take the white one"]) == "en"


def test_telegram_setting_then_english_as_last_resort():
    assert detect_language(["yes"], "am") == "am"
    assert detect_language(["yes"], "en-US") == "en"
    assert detect_language([], None) == "en"


def test_prices():
    assert format_price(Decimal("4500"), "en") == "4,500 ETB"
    assert format_price(Decimal("4500"), "am") == "4,500 ብር"
    assert format_price(Decimal("99.5"), "am") == "99.50 ብር"
    assert format_price(None, "am") == t("price_not_set", "am")


def test_both_languages_amharic_first():
    assert both("empty_reply") == f"{t('empty_reply', 'am')}\n{t('empty_reply', 'en')}"
