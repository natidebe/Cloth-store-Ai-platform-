"""Phase 13, shop types (D58–D62): the shop's words for a product's two
options everywhere (bot, channel posts, AI, staff group, Mini App), the
owner's renames, and electronics' condition and warranty."""
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.agents.catalog import caption
from app.agents.interpreter import _PROMPT, _options
from app.agents.messages import bot_profile, t
from app.agents.shop_types import clean_labels, labels, lower_word, text_values
from app.agents.tools import _alert_line, describe
from app.models.schemas import OrderItemDetail, Product, Store, VariantMatch
from tests.test_miniapp import (  # noqa: F401  (world is a pytest fixture)
    MEMBER,
    OWNER,
    STORE_A,
    TOKEN_B,
    headers,
    platform,
    url,
    world,
)


def store(shop_type="clothing", option_labels=None) -> Store:
    return Store(id=uuid4(), name="Shop", shop_type=shop_type, option_labels=option_labels)


PHONES = store("electronics")


def phone_variant(**changes) -> VariantMatch:
    fields = dict(variant_id=uuid4(), product_id=uuid4(), product_name="iPhone 13", brand="Apple",
                  color="Black", size="128GB", stock_quantity=2, price=Decimal("60000"))
    return VariantMatch(**{**fields, **changes})


# --- The words ------------------------------------------------------------------------

@pytest.mark.parametrize("shop_type, first, second", [
    ("clothing", "Color", "Size"), ("electronics", "Color", "Storage"),
    ("cosmetics", "Shade", "Volume"), ("general", "Type", "Size"),
])
def test_each_type_names_the_two_options(shop_type, first, second):
    one, two = labels(store(shop_type))
    assert (one.en, two.en) == (first, second)


def test_no_store_or_an_unknown_type_means_clothing_as_before():
    assert labels(None)[1].en == labels(store("spaceships"))[1].en == "Size"


def test_the_owner_renames_an_option_in_either_language():
    shop = store("electronics", {"option2": {"en": "Model", "am": "ሞዴል"}})
    assert labels(shop)[1].en == "Model" and labels(shop)[1].am == "ሞዴል"
    assert labels(shop)[0].en == "Color"  # not renamed: the type's word
    only_amharic = store("electronics", {"option2": {"am": "ሞዴል"}})
    assert labels(only_amharic)[1].en == "Storage" and labels(only_amharic)[1].am == "ሞዴል"


def test_renames_are_cleaned_and_empty_ones_dropped():
    assert clean_labels({"option1": {"en": "  Big   word ", "am": ""}, "option9": {"en": "x"}}) == \
        {"option1": {"en": "Big word"}}
    assert clean_labels({"option1": {"en": " "}}) is None and clean_labels("nope") is None


def test_english_words_are_lowercased_in_a_sentence_but_not_abbreviations():
    assert lower_word("Storage") == "storage" and lower_word("RAM") == "RAM"
    assert text_values(PHONES, "en")["opt2"] == "storage" and text_values(PHONES, "en")["Opt2"] == "Storage"


# --- The bot ----------------------------------------------------------------------------

def test_the_bot_asks_in_the_shops_words():
    assert t("ask_size", "en", PHONES) == "Which storage?"
    assert t("ask_size", "am", PHONES) == "የትኛው ማከማቻ?"
    assert t("ask_color", "en", store("cosmetics"), product="Lipstick", price="900 ETB") == \
        "Lipstick — 900 ETB. Which shade?"
    assert t("size_unavailable", "en", PHONES, size="1TB") == "Sorry, storage 1TB isn't available in that color."


def test_a_clothing_shop_reads_as_before():
    assert t("ask_size", "en", store()) == "Which size?" and t("size", "en", store(), size="42") == "size 42"
    assert t("ask_size", "en") == "Which size?"  # no store at all


def test_the_bots_description_names_the_options():
    description, _, _ = bot_profile("Phone World", PHONES)
    assert "Choose color, storage and quantity" in description
    assert "ቀለም፣ ማከማቻና ብዛት" in description


def test_descriptions_carry_condition_and_warranty():
    variant = phone_variant(condition="used", warranty_months=6)
    assert describe(variant, "en", PHONES) == "iPhone 13 (Apple), Used, Black, storage 128GB, 6 months warranty"
    assert describe(variant, "am", PHONES) == "iPhone 13 (Apple), ያገለገለ, Black, ማከማቻ 128GB, የ6 ወር ዋስትና"


def test_staff_alerts_use_the_shops_word():
    item = OrderItemDetail(id=uuid4(), order_id=uuid4(), variant_id=uuid4(), quantity=1, price=Decimal(1),
                           product_name="iPhone 13", color="Black", size="128GB", condition="new",
                           warranty_months=12)
    assert _alert_line(item, PHONES) == "• iPhone 13 (New, 12 months warranty), Black, storage 128GB × 1"


def test_the_ai_is_told_what_the_options_are_called():
    assert "`size` is the storage (Amharic: ማከማቻ)" in _options(PHONES)
    assert "{shop_kind}" in _PROMPT and "clothing and shoe store" not in _PROMPT


# --- Channel posts --------------------------------------------------------------------------

def test_a_phone_post_says_storage_condition_and_warranty():
    product = Product(id=uuid4(), store_id=uuid4(), name="iPhone 13", brand="Apple", code="P120",
                      condition="new", warranty_months=12)
    text = caption(product, [phone_variant(), phone_variant(color="White", size="256GB")], PHONES)
    assert "🎨 ቀለም እና ማከማቻ / Colors & storage:" in text
    assert "• Black: 128GB" in text and "• White: 256GB" in text
    assert "✨ አዲስ / New · የ12 ወር ዋስትና / 12 months warranty" in text


def test_a_clothing_post_is_unchanged():
    product = Product(id=uuid4(), store_id=uuid4(), name="Samba", code="P101")
    variant = phone_variant(product_name="Samba", brand=None, color="White", size="42")
    assert "🎨 ቀለም እና ቁጥር / Colors & sizes:" in caption(product, [variant], store())


# --- The Mini App and sign-up -----------------------------------------------------------------

def test_me_tells_the_screens_the_shops_words(world):
    db, _, client, _, _ = world
    db.stores[STORE_A.id] = db.stores[STORE_A.id].model_copy(update={"shop_type": "electronics"})
    shop = client.get(url("/me"), headers=headers(MEMBER)).json()["store"]
    assert shop["shop_type"] == "electronics" and shop["option2"]["en"] == "Storage"
    assert shop["condition_and_warranty"] is True and "Phones" in shop["categories"]


def test_the_owner_changes_the_type_and_renames_and_the_bot_description_follows(world):
    db, telegram, client, _, _ = world
    saved = client.put(url("/settings"), headers=headers(OWNER), json={
        "shop_type": "electronics", "option_labels": {"option2": {"en": " Model ", "am": "ሞዴል"}}})
    assert saved.status_code == 200
    body = saved.json()
    assert body["shop_type"] == "electronics" and body["option_labels"] == {"option2": {"en": "Model", "am": "ሞዴል"}}
    assert {t["type"] for t in body["shop_types"]} == {"clothing", "electronics", "cosmetics", "general"}
    assert db.stores[STORE_A.id].shop_type == "electronics"
    descriptions = [b for _, m, b in telegram.calls if m == "setMyDescription"]
    assert descriptions and "Choose color, model and quantity" in descriptions[-1]["description"]
    # Staff can't change it.
    assert client.put(url("/settings"), headers=headers(MEMBER), json={"shop_type": "general"}).status_code == 403
    assert client.put(url("/settings"), headers=headers(OWNER), json={"shop_type": "cars"}).status_code == 422


def test_products_keep_condition_and_warranty(world):
    db, _, client, _, _ = world
    created = client.post(url("/products"), headers=headers(OWNER), json={
        "product": {"name": "iPhone 13", "base_price": 60000, "condition": "used", "warranty_months": 3}})
    assert created.status_code == 201, created.text
    product = created.json()["product"] if "product" in created.json() else created.json()
    assert product["condition"] == "used" and product["warranty_months"] == 3
    changed = client.patch(url(f"/products/{product['id']}"), headers=headers(OWNER),
                           json={"condition": None, "warranty_months": 12})
    assert changed.status_code == 200
    assert db.products[UUID(product["id"])]["condition"] is None
    assert db.products[UUID(product["id"])]["warranty_months"] == 12
    bad = client.post(url("/products"), headers=headers(OWNER),
                      json={"product": {"name": "X", "condition": "broken"}})
    assert bad.status_code == 422


def test_sign_up_asks_the_kind_of_shop_first(world):
    db, telegram, client, _, _ = world
    me = client.get("/api/v1/platform-app/me", headers=platform(MEMBER)).json()
    assert [t["type"] for t in me["shop_types"]] == ["clothing", "electronics", "cosmetics", "general"]
    created = client.post("/api/v1/platform-app/stores", headers=platform(MEMBER), json={
        "name": "Phone World", "bot_token": TOKEN_B[:10] + "Z" * 35, "shop_type": "electronics"})
    assert created.status_code == 201
    assert db.stores[UUID(created.json()["id"])].shop_type == "electronics"
    # The new bot's description already speaks the shop's words.
    description = [b for _, m, b in telegram.calls if m == "setMyDescription"][-1]["description"]
    assert "Choose color, storage and quantity" in description
