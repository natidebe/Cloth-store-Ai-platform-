"""Phase 15 against a real Supabase project (migration 015): a paid order
moves on and never back, only in its own store; the export reads one store's
month, page by page; each morning summary is remembered once; owners who
turned it off aren't asked. Skipped unless SUPABASE_URL and
SUPABASE_SERVICE_ROLE_KEY are set in backend/.env AND 015_quick_wins.sql has
been run. Use a test project. Creates temporary stores named
"__pytest_quick_..." and deletes them after.
"""
from datetime import datetime, timedelta, timezone
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

PREFIX = "__pytest_quick_"


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


async def _cleanup(db: SupabaseService) -> None:
    await db._db.table("stores").delete().like("name", f"{PREFIX}%").execute()


async def _store(db: SupabaseService, name: str, **fields) -> UUID:
    row = (await db._db.table("stores").insert({"name": f"{PREFIX}{name}", "status": "active", **fields})
           .execute()).data[0]
    return UUID(row["id"])


async def _order(db: SupabaseService, store_id: UUID, created_at: datetime, paid=True, status="confirmed") -> UUID:
    row = (await db._db.table("orders").insert({
        "store_id": str(store_id), "status": status, "payment_status": "paid" if paid else "unpaid",
        "fulfillment_method": "pickup", "total_price": 100, "created_at": created_at.isoformat(),
    }).execute()).data[0]
    return UUID(row["id"])


async def _status(db: SupabaseService, order_id: UUID) -> dict:
    return (await db._db.table("orders").select("status, status_changed_by_name")
            .eq("id", str(order_id)).execute()).data[0]


@pytest.fixture(scope="module")
async def db(anyio_backend):
    service = await SupabaseService.connect(_settings.supabase_url,
                                            _settings.supabase_service_role_key.get_secret_value())
    try:
        await service._run(service._db.table("daily_summaries").select("day").limit(1))
    except DatabaseUnavailableError:
        await service.close()
        raise
    except DatabaseError:
        await service.close()
        pytest.skip("migration 015_quick_wins.sql has not been run yet")
    await _cleanup(service)
    yield service
    await _cleanup(service)
    await service.close()


async def test_a_paid_order_moves_on_never_back_and_only_in_its_store(db):
    store, other = await _store(db, "a"), await _store(db, "b")
    now = datetime.now(timezone.utc)
    order = await _order(db, store, now)
    assert not await db.set_order_status(other, order, "out_for_delivery", ["confirmed"])  # another store
    assert await db.set_order_status(store, order, "out_for_delivery", ["confirmed"], 7, "Sara")
    assert await _status(db, order) == {"status": "out_for_delivery", "status_changed_by_name": "Sara"}
    assert not await db.set_order_status(store, order, "out_for_delivery", ["confirmed"])  # a second tap
    assert await db.set_order_status(store, order, "delivered", ["confirmed", "out_for_delivery"])
    assert not await db.set_order_status(store, order, "out_for_delivery", ["confirmed"])  # never back
    unpaid = await _order(db, store, now, paid=False, status="pending")
    assert not await db.set_order_status(store, unpaid, "delivered", ["pending", "confirmed"])


async def test_the_export_reads_one_stores_month_page_by_page(db):
    store, other = await _store(db, "export"), await _store(db, "export_other")
    start = datetime(2026, 9, 1, tzinfo=timezone(timedelta(hours=3)))
    end = datetime(2026, 10, 1, tzinfo=timezone(timedelta(hours=3)))
    inside = [await _order(db, store, start + timedelta(days=d)) for d in (0, 5, 29)]
    await _order(db, store, end)  # October: not in September
    await _order(db, store, start - timedelta(seconds=1))  # August 31
    await _order(db, other, start + timedelta(days=2))  # another store
    rows = await db.orders_for_export(store, start, end, page_size=2)
    assert [UUID(r["id"]) for r in rows] == inside  # oldest first, across pages
    assert {"order_items", "payments"} <= set(rows[0])


async def test_each_summary_is_remembered_once_and_off_means_off(db):
    wants = await _store(db, "summary", owner_telegram_id=111)
    off = await _store(db, "summary_off", owner_telegram_id=112, daily_summary="off")
    no_owner = await _store(db, "summary_no_owner")
    ids = {s.id for s in await db.stores_for_summary()}
    assert wants in ids and off not in ids and no_owner not in ids
    assert await db.add_daily_summary(wants, "2026-10-03")
    assert not await db.add_daily_summary(wants, "2026-10-03")
    assert await db.add_daily_summary(wants, "2026-10-04")
    with pytest.raises(DatabaseError):  # only 'am', 'en' or 'off'
        await db._run(db._db.table("stores").update({"daily_summary": "fr"}).eq("id", str(wants)))
