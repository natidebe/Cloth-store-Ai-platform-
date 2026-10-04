"""Phase 14 against a real Supabase project (migration 014): a payment adds
months from the current end (or now), resumes an unpaid pause but never an
admin suspension, and is kept; reminders are remembered once per end date.
Skipped unless SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are set in
backend/.env AND 014_subscriptions.sql has been run. Use a test project.
Creates temporary stores named "__pytest_subs_..." and deletes them after.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
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

PREFIX = "__pytest_subs_"


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


async def _cleanup(db: SupabaseService) -> None:
    await db._db.table("stores").delete().like("name", f"{PREFIX}%").execute()


async def _store(db: SupabaseService, name: str, **fields) -> UUID:
    row = (await db._db.table("stores").insert({"name": f"{PREFIX}{name}", "status": "active", **fields})
           .execute()).data[0]
    return UUID(row["id"])


@pytest.fixture(scope="module")
async def db(anyio_backend):
    service = await SupabaseService.connect(_settings.supabase_url,
                                            _settings.supabase_service_role_key.get_secret_value())
    try:
        await service._run(service._db.table("subscription_payments").select("id").limit(1))
    except DatabaseUnavailableError:
        await service.close()
        raise
    except DatabaseError:
        await service.close()
        pytest.skip("migration 014_subscriptions.sql has not been run yet")
    await _cleanup(service)
    yield service
    await _cleanup(service)
    await service.close()


def _when(value) -> datetime:
    return datetime.fromisoformat(str(value))


async def test_a_payment_adds_three_months_to_the_current_end(db):
    ends = datetime.now(timezone.utc) + timedelta(days=10)
    store_id = await _store(db, "early", plan="free", plan_ends_at=ends.isoformat())
    result = await db.record_subscription_payment(store_id, "basic", 3, Decimal("4500"), "Telebirr", "TX1", 7)
    assert abs(_when(result["period_start"]) - ends) < timedelta(seconds=1)  # paying early loses nothing
    shop = await db.get_store_any_status(store_id)
    assert shop.plan == "basic" and abs(shop.plan_ends_at - _when(result["period_end"])) < timedelta(seconds=1)
    assert 89 <= (shop.plan_ends_at - ends).days <= 92
    [payment] = await db.subscription_payments(store_id)
    assert payment["reference"] == "TX1" and Decimal(str(payment["amount"])) == Decimal("4500")


async def test_a_payment_after_the_end_starts_today_and_resumes_an_unpaid_pause(db):
    store_id = await _store(db, "late", plan="basic",
                            plan_ends_at=(datetime.now(timezone.utc) - timedelta(days=5)).isoformat())
    await db.set_store_status(store_id, "suspended", reason="unpaid")
    result = await db.record_subscription_payment(store_id, "pro", 3, Decimal("9000"), None, None, None)
    assert result["resumed"] is True
    assert abs(_when(result["period_start"]) - datetime.now(timezone.utc)) < timedelta(minutes=1)
    shop = await db.get_store_any_status(store_id)
    assert shop.status == "active" and shop.suspended_reason is None and shop.is_active


async def test_an_admin_suspension_is_not_undone_by_a_payment(db):
    store_id = await _store(db, "admin", plan="basic",
                            plan_ends_at=datetime.now(timezone.utc).isoformat())
    await db.set_store_status(store_id, "suspended", reason="admin")
    result = await db.record_subscription_payment(store_id, "basic", 3, Decimal("4500"), None, None, None)
    assert result["resumed"] is False
    assert (await db.get_store_any_status(store_id)).status == "suspended"


async def test_a_reminder_is_remembered_once_per_end_date(db):
    ends = datetime.now(timezone.utc) + timedelta(days=3)
    store_id = await _store(db, "notice", plan="basic", plan_ends_at=ends.isoformat())
    assert await db.add_subscription_notice(store_id, ends, "before_3") is True
    assert await db.add_subscription_notice(store_id, ends, "before_3") is False
    assert await db.add_subscription_notice(store_id, ends + timedelta(days=90), "before_3") is True
    assert store_id in {s.id for s in await db.stores_with_plan_end()}
