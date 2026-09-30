"""Phase 7b: store profile and product nicknames, against a real Supabase project.

Skipped unless SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are set in
backend/.env AND 004_store_profile.sql has been run. Use a test project.

Creates temporary stores named "__pytest_profile_..." and deletes them after.
"""
from uuid import UUID

import pytest

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

PREFIX = "__pytest_profile_"


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


async def _insert(service: SupabaseService, table: str, row: dict) -> dict:
    return (await service._db.table(table).insert(row).execute()).data[0]


async def _cleanup(service: SupabaseService) -> None:
    await service._db.table("stores").delete().like("name", f"{PREFIX}%").execute()


@pytest.fixture(scope="module")
async def world(anyio_backend):
    service = await SupabaseService.connect(
        _settings.supabase_url, _settings.supabase_service_role_key.get_secret_value()
    )
    try:
        await service._run(service._db.table("products").select("search_keywords").limit(1))
    except DatabaseUnavailableError:
        await service.close()
        raise  # Supabase unreachable: a real failure, not "not migrated"
    except DatabaseError:
        await service.close()
        pytest.skip("migration 004_store_profile.sql has not been run yet")

    await _cleanup(service)
    store_a = await _insert(service, "stores", {
        "name": f"{PREFIX}a",
        "opening_hours": "Mon–Sat 8:30–19:00, Sun closed",
        "payment_instructions": "Telebirr 0911 000 000 (Selam Shoes)",
    })
    store_b = await _insert(service, "stores", {"name": f"{PREFIX}b"})

    af1 = await _insert(service, "products", {
        "store_id": store_a["id"], "name": "Air Force 1", "brand": "Nike",
        "base_price": 4500, "search_keywords": "AF1, air force, ኤር ፎርስ",
    })
    await _insert(service, "product_variants", {
        "product_id": af1["id"], "color": "White", "size": "43", "stock_quantity": 2,
    })
    # Store B uses the same nickname for a different product.
    other = await _insert(service, "products", {
        "store_id": store_b["id"], "name": "Other shoe", "base_price": 100, "search_keywords": "AF1",
    })
    await _insert(service, "product_variants", {"product_id": other["id"], "size": "43", "stock_quantity": 5})

    yield {"service": service, "a": UUID(store_a["id"]), "b": UUID(store_b["id"])}

    await _cleanup(service)
    await service.close()


async def test_get_store_includes_profile(world):
    store = await world["service"].get_store(world["a"])
    assert store.profile.filled() == {
        "opening_hours": "Mon–Sat 8:30–19:00, Sun closed",
        "payment_instructions": "Telebirr 0911 000 000 (Selam Shoes)",
    }
    assert "location" in store.profile.missing()


@pytest.mark.parametrize("query", ["AF1", "af1", "air force", "ኤር ፎርስ", "Air Force 1", "nike"])
async def test_nicknames_find_the_product(world, query):
    results = await world["service"].search_variants(world["a"], query, color="white", size="43")
    assert [r.product_name for r in results] == ["Air Force 1"]


async def test_nicknames_stay_in_their_store(world):
    results = await world["service"].search_variants(world["b"], "AF1")
    assert [r.product_name for r in results] == ["Other shoe"]


async def test_profile_text_is_limited(world):
    with pytest.raises(DatabaseError):
        await world["service"]._run(
            world["service"]._db.table("stores").update({"return_policy": "x" * 1001})
            .eq("id", str(world["a"]))
        )
