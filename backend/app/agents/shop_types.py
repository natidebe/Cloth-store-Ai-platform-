"""Shop types (Phase 13, D58–D62): the shop's words for a product's options.

Every product has up to two options, stored in the `color` and `size`
columns of product_variants. Their *names* depend on the kind of shop:

    clothing     Color / ቀለም   Size / ቁጥር       (every shop before Phase 13)
    electronics  Color / ቀለም   Storage / ማከማቻ   (+ condition and warranty)
    cosmetics    Shade / ቀለም   Volume / መጠን
    general      Type / አይነት   Size / መጠን

The owner may rename either option (stores.option_labels, D59), e.g.
{"option2": {"en": "Model", "am": "ሞዴል"}}; anything not renamed keeps the
type's word. Changing the type (D61) changes only the words.
"""
from dataclasses import dataclass, field, replace
from typing import Literal

ShopType = Literal["clothing", "electronics", "cosmetics", "general"]
SHOP_TYPES: tuple[ShopType, ...] = ("clothing", "electronics", "cosmetics", "general")
DEFAULT_TYPE: ShopType = "clothing"
MAX_LABEL = 20


@dataclass(frozen=True)
class Label:
    """One option's name: English (as a heading, e.g. "Storage"), its plural
    for lists ("Colors"), Amharic, and the emoji used in channel posts."""
    en: str
    am: str
    plural: str
    icon: str


@dataclass(frozen=True)
class Preset:
    option1: Label
    option2: Label
    categories: tuple[str, ...] = ()
    condition_and_warranty: bool = False  # D60: electronics
    names: dict[str, str] = field(default_factory=dict)  # the type itself, by language


PRESETS: dict[ShopType, Preset] = {
    "clothing": Preset(
        Label("Color", "ቀለም", "Colors", "🎨"), Label("Size", "ቁጥር", "Sizes", "📏"),
        ("Clothing", "Shoes", "Bags", "Accessories"),
        names={"en": "Clothing & shoes", "am": "አልባሳትና ጫማ"},
    ),
    "electronics": Preset(
        Label("Color", "ቀለም", "Colors", "🎨"), Label("Storage", "ማከማቻ", "Storage", "💾"),
        ("Phones", "Laptops", "Tablets", "Accessories"),
        condition_and_warranty=True,
        names={"en": "Electronics & phones", "am": "ኤሌክትሮኒክስና ስልክ"},
    ),
    "cosmetics": Preset(
        Label("Shade", "ቀለም", "Shades", "🎨"), Label("Volume", "መጠን", "Volumes", "🧴"),
        ("Makeup", "Perfume", "Skincare", "Hair"),
        names={"en": "Cosmetics & perfume", "am": "መዋቢያና ሽቶ"},
    ),
    "general": Preset(
        Label("Type", "አይነት", "Types", "🏷️"), Label("Size", "መጠን", "Sizes", "📏"),
        (),
        names={"en": "General", "am": "ሌላ"},
    ),
}


def shop_type_of(store: object | None) -> ShopType:
    value = getattr(store, "shop_type", None)
    return value if value in PRESETS else DEFAULT_TYPE


def preset_of(store: object | None) -> Preset:
    return PRESETS[shop_type_of(store)]


def labels(store: object | None) -> tuple[Label, Label]:
    """The shop's two option names: the type's, with the owner's renames.
    No store (or an older one): clothing's, as before Phase 13."""
    preset = preset_of(store)
    renames = getattr(store, "option_labels", None) or {}
    result = []
    for key, label in (("option1", preset.option1), ("option2", preset.option2)):
        own = renames.get(key) if isinstance(renames, dict) else None
        if isinstance(own, dict):
            en = _clean(own.get("en"))
            am = _clean(own.get("am"))
            if en or am:
                # A rename is used as written; the English one also as its plural.
                label = replace(label, en=en or label.en, am=am or label.am,
                                plural=en or label.plural)
        result.append(label)
    return result[0], result[1]


def has_condition_and_warranty(store: object | None) -> bool:
    return preset_of(store).condition_and_warranty


def lower_word(text: str) -> str:
    """For English in a sentence: "Storage" -> "storage", but "RAM" stays."""
    if len(text) > 1 and text[0].isupper() and text[1:] == text[1:].lower():
        return text[0].lower() + text[1:]
    return text


def text_values(store: object | None, language: str) -> dict[str, str]:
    """The placeholders texts use for the option names (messages.t fills them):
    {opt1}/{opt2} inside a sentence, {Opt1}/{Opt2} at its start or on a button."""
    first, second = labels(store)
    if language == "am":
        return {"opt1": first.am, "opt2": second.am, "Opt1": first.am, "Opt2": second.am}
    return {"opt1": lower_word(first.en), "opt2": lower_word(second.en),
            "Opt1": first.en, "Opt2": second.en}


def clean_labels(value: object) -> dict | None:
    """The owner's renames as saved: {"option1"|"option2": {"en", "am"}},
    trimmed, empty parts dropped; None when nothing is renamed."""
    if not isinstance(value, dict):
        return None
    result = {}
    for key in ("option1", "option2"):
        own = value.get(key)
        if not isinstance(own, dict):
            continue
        words = {lang: _clean(own.get(lang)) for lang in ("en", "am")}
        words = {lang: word for lang, word in words.items() if word}
        if words:
            result[key] = words
    return result or None


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())[:MAX_LABEL]


def condition_text(condition: str | None, language: str) -> str | None:
    if condition == "new":
        return "አዲስ" if language == "am" else "New"
    if condition == "used":
        return "ያገለገለ" if language == "am" else "Used"
    return None


def warranty_text(months: int | None, language: str) -> str | None:
    if not months:
        return None
    if language == "am":
        return f"የ{months} ወር ዋስትና"
    return f"{months} month{'s' if months != 1 else ''} warranty"


# --- For the Mini App ------------------------------------------------------------------

def _label_json(label: Label) -> dict[str, str]:
    return {"en": label.en, "am": label.am, "plural": label.plural, "icon": label.icon}


def shop_types_json() -> list[dict]:
    """Every type with its own words, for "What kind of shop?" and Settings."""
    return [{"type": name, "names": preset.names, "option1": _label_json(preset.option1),
             "option2": _label_json(preset.option2), "categories": list(preset.categories),
             "condition_and_warranty": preset.condition_and_warranty}
            for name, preset in PRESETS.items()]


def shop_json(store: object | None) -> dict:
    """This shop's type and words (the owner's renames applied), for /me."""
    first, second = labels(store)
    preset = preset_of(store)
    return {"shop_type": shop_type_of(store), "option1": _label_json(first), "option2": _label_json(second),
            "categories": list(preset.categories), "condition_and_warranty": preset.condition_and_warranty}
