"""Phase 9b database rules against a real Supabase project (migration 008).

Skipped unless SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are set in
backend/.env AND 008_store_onboarding.sql has been run. Use a test project.
Creates two temporary login users and stores named "__pytest_onboard_...",
and deletes them after.
"""
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.core.config import get_settings
from app.services.supabase_service import (
    DatabaseError,
    DatabaseUnavailableError,
    DuplicateError,
    SupabaseService,
)

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

PREFIX = "__pytest_onboard_"
BOT_ID = 990000000001
BOT_ID_2 = 990000000002


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
        await db._run(db._db.table("store_invites").select("id").limit(1))
    except DatabaseUnavailableError:
        await db.close()
        raise
    except DatabaseError:
        await db.close()
        pytest.skip("migration 008_store_onboarding.sql has not been run yet")

    await _cleanup(db)
    tag = uuid4().hex[:8]
    users = []
    for name in ("owner", "staff"):
        created = await db._db.auth.admin.create_user({
            "email": f"{PREFIX.strip('_')}{name}_{tag}@example.com",
            "password": uuid4().hex, "email_confirm": True,
        })
        users.append(created.user)
    owner, staff = UUID(str(users[0].id)), UUID(str(users[1].id))

    yield {"db": db, "owner": owner, "staff": staff, "staff_email": users[1].email}

    await _cleanup(db)
    for user in users:
        await db._db.auth.admin.delete_user(str(user.id))
    await db.close()


async def _new_store(db, owner, bot_id=BOT_ID, name="shop"):
    return await db.create_store(f"{PREFIX}{name}", f"{bot_id}:{uuid4().hex}{uuid4().hex[:4]}",
                                 bot_id, f"pytest_{bot_id}_bot", "secret-" + uuid4().hex, owner)


async def test_a_new_store_is_pending_with_its_owner(world):
    db, owner = world["db"], world["owner"]
    store_id = await _new_store(db, owner)
    try:
        store = await db.get_store_any_status(store_id)
        assert store.status == "pending" and not store.is_active and store.plan == "free"
        assert await db.get_store(store_id) is None  # not serving customers yet (D14)
        assert await db.staff_role(store_id, owner) == "owner"
        [mine] = [m for m in await db.list_user_stores(owner) if m.store_id == store_id]
        assert mine.role == "owner" and mine.status == "pending"
        assert (await db.find_store_by_bot(BOT_ID)).id == store_id

        # One store per bot: the same bot id (e.g. a regenerated token) is refused.
        with pytest.raises(DuplicateError):
            await _new_store(db, owner, name="copy")
    finally:
        await _cleanup(db)


async def test_status_drives_is_active(world):
    db = world["db"]
    store_id = await _new_store(db, world["owner"])
    try:
        await db.set_store_status(store_id, "active")
        assert (await db.get_store(store_id)).is_active
        await db.set_store_status(store_id, "suspended")
        assert await db.get_store(store_id) is None
        with pytest.raises(DatabaseError):
            await db.set_store_status(store_id, "closed")
        await db.set_store_plan(store_id, "pro")
        [summary] = [s for s in await db.list_all_stores() if s.id == store_id]
        assert summary.plan == "pro" and summary.status == "suspended" and summary.orders == 0
        with pytest.raises(DatabaseError):
            await db.set_store_plan(store_id, "gold")
    finally:
        await _cleanup(db)


async def test_a_link_code_works_once_and_expires(world):
    db = world["db"]
    store_id = await _new_store(db, world["owner"])
    try:
        later = datetime.now(timezone.utc) + timedelta(minutes=30)
        await db.set_link_code(store_id, "ABCD2345", later)
        assert not await db.use_link_code(store_id, "WRONG234", "staff_chat_id", -500)
        assert await db.use_link_code(store_id, "ABCD2345", "staff_chat_id", -500)
        assert not await db.use_link_code(store_id, "ABCD2345", "channel_id", -100500)  # used up
        store = await db.get_store_any_status(store_id)
        assert store.staff_chat_id == -500 and store.channel_id is None

        await db.set_link_code(store_id, "EXPIRED2", datetime.now(timezone.utc) - timedelta(seconds=1))
        assert not await db.use_link_code(store_id, "EXPIRED2", "channel_id", -100500)
    finally:
        await _cleanup(db)


async def test_invites_are_accepted_once_and_owners_stay_owners(world):
    db, owner, staff = world["db"], world["owner"], world["staff"]
    store_id = await _new_store(db, owner)
    try:
        await db.invite_staff(store_id, world["staff_email"].upper(), owner)
        await db.invite_staff(store_id, world["staff_email"], owner)  # again: still one invite
        assert await db.accept_invites(staff, world["staff_email"]) == 1
        assert await db.staff_role(store_id, staff) == "staff"
        assert await db.accept_invites(staff, world["staff_email"]) == 0

        # Inviting the owner can't turn them into staff.
        owner_email = (await db._db.auth.admin.get_user_by_id(str(owner))).user.email
        await db.invite_staff(store_id, owner_email, owner)
        assert await db.accept_invites(owner, owner_email) == 0
        assert await db.staff_role(store_id, owner) == "owner"

        assert not await db.remove_staff(store_id, owner)  # never the owner
        assert await db.remove_staff(store_id, staff)
        assert await db.staff_role(store_id, staff) is None
    finally:
        await _cleanup(db)


async def test_changing_the_bot_keeps_one_store_per_bot(world):
    db, owner = world["db"], world["owner"]
    first = await _new_store(db, owner, BOT_ID, "first")
    second = await _new_store(db, owner, BOT_ID_2, "second")
    try:
        with pytest.raises(DuplicateError):
            await db.set_bot(second, bot_id=BOT_ID, bot_username="taken_bot")
        await db.set_bot(first, bot_id=BOT_ID, bot_username="renamed_bot", bot_token=f"{BOT_ID}:{uuid4().hex}xyz",
                         webhook_secret="new-secret")
        store = await db.get_store_any_status(first)
        assert store.telegram_bot_username == "renamed_bot"
        assert store.webhook_secret.get_secret_value() == "new-secret"
    finally:
        await _cleanup(db)


async def test_platform_admins_and_logins(world):
    db = world["db"]
    assert not await db.is_platform_admin(world["owner"])
    assert await db.get_user("not-a-real-token") is None
