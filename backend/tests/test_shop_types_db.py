"""Phase 13 against a real Supabase project (migration 013): a shop's type
and renamed words, and a product's condition and warranty, saved and read
back; the database refuses what isn't allowed.
Skipped unless SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are set in
backend/.env AND 013_shop_types.sql has been run. Use a test project.
Creates temporary stores named "__pytest_shoptype_..." and deletes them after.
"""
from decimal import Decimal
from uuid import UUID

import pytest

from app.agents.shop_types import labels
from app.core.config import get_settings
from app.services.supabase_service import DatabaseError, DatabaseUnavailableError, SupabaseService

_settings = get_settings()
_configured = bool(
    _settings.supabase_url
    and _settings.supabase_service_role_key.get_secret_value()
    and "your-project-ref" not in _settings.supabase_url
)

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(not _configured, reason="Supabase not configured in backend/.env"),
]

PREFIX = "__pytest_shoptype_"


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


async def _cleanup(db: SupabaseService) -> None:
    await db._db.table("stores").delete().like("name", f"{PREFIX}%").execute()


@pytest.fixture(scope="module")
async def world(anyio_backend):
    db = await SupabaseService.connect(_settings.supabase_url,
                                       _settings.supabase_service_role_key.get_secret_value())
    try:
        await db._run(db._db.table("stores").select("shop_type, option_labels").limit(1))
    except DatabaseUnavailableError:
        await db.close()
        raise
    except DatabaseError:
        await db.close()
        pytest.skip("migration 013_shop_types.sql has not been run yet")
    await _cleanup(db)
    row = (await db._db.table("stores").insert({"name": f"{PREFIX}a", "status": "active"}).execute()).data[0]
    yield {"db": db, "store": UUID(row["id"])}
    await _cleanup(db)
    await db.close()


async def test_a_new_shop_is_clothing_until_the_owner_says_otherwise(world):
    db, store_id = world["db"], world["store"]
    assert (await db.get_store_any_status(store_id)).shop_type == "clothing"
    await db.update_store_profile(store_id, {"shop_type": "electronics",
                                             "option_labels": {"option2": {"en": "Model", "am": "ሞዴል"}}})
    shop = await db.get_store_any_status(store_id)
    assert shop.shop_type == "electronics" and labels(shop)[1].en == "Model"


async def test_the_database_refuses_an_unknown_type(world):
    with pytest.raises(DatabaseError):
        await world["db"].update_store_profile(world["store"], {"shop_type": "spaceships"})


async def test_condition_and_warranty_reach_every_reader(world):
    db, store_id = world["db"], world["store"]
    product_id = await db.create_product(store_id, {"name": "iPhone 13", "base_price": Decimal("60000"),
                                                    "condition": "used", "warranty_months": 6})
    await db.add_variant(store_id, product_id, "Black", "128GB", 2, None)
    product = await db.get_product(store_id, product_id)
    assert product.condition == "used" and product.warranty_months == 6
    variants = await db.get_product_variants(store_id, product_id)
    assert variants[0].condition == "used" and variants[0].warranty_months == 6


async def test_the_database_refuses_a_bad_condition_or_warranty(world):
    db, store_id = world["db"], world["store"]
    for fields in ({"condition": "broken"}, {"warranty_months": 500}):
        with pytest.raises(DatabaseError):
            await db.create_product(store_id, {"name": "Bad", **fields})
