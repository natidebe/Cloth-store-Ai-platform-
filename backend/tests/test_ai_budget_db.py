"""Phase 10 (D21): the daily AI counter in a real Supabase project (migration 009).

Skipped unless SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are set in
backend/.env AND 009_ai_budget.sql has been run. Use a test project.
Creates two temporary stores named "__pytest_ai_..." and deletes them after.
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

PREFIX = "__pytest_ai_"


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
async def world(anyio_backend):
    db = await SupabaseService.connect(_settings.supabase_url,
                                       _settings.supabase_service_role_key.get_secret_value())
    try:
        await db._run(db._db.table("ai_usage").select("store_id").limit(1))
    except DatabaseUnavailableError:
        await db.close()
        raise
    except DatabaseError:
        await db.close()
        pytest.skip("migration 009_ai_budget.sql has not been run yet")

    await db._db.table("stores").delete().like("name", f"{PREFIX}%").execute()
    stores = []
    for name in ("a", "b"):
        row = (await db._db.table("stores").insert({"name": f"{PREFIX}{name}", "status": "active"})
               .execute()).data[0]
        stores.append(UUID(row["id"]))
    yield {"db": db, "a": stores[0], "b": stores[1]}
    await db._db.table("stores").delete().like("name", f"{PREFIX}%").execute()
    await db.close()


async def test_ai_calls_are_counted_per_store_per_day(world):
    db, a, b = world["db"], world["a"], world["b"]
    assert await db.use_ai_call(a) == 1
    assert await db.use_ai_call(a) == 2
    assert await db.use_ai_call(b) == 1  # each store has its own count
    assert await db.use_ai_call(a) == 3
