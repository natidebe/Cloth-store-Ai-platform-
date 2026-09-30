-- 008_store_onboarding.sql
--
-- Run in the Supabase SQL Editor, after 007_channel_catalog.sql.
-- Everything is in one transaction: if any part fails, nothing is applied.
-- Safe to run again (parts that already exist are skipped or replaced).
--
-- Phase 9b, decisions used (see BUILD_PLAN.md):
--   D14  new stores wait for a platform admin's approval (status 'pending')
--   D15  platform admins are listed in the platform_admins table
--   D16  stores have a plan (free / basic / pro); it limits nothing yet
--   D17  the owner can change the bot token later (the backend re-checks it)
--
-- After running it, make yourself a platform admin (your user id is in
-- Authentication -> Users):
--   insert into platform_admins (user_id) values ('<your user id>');


begin;


-- ---------------------------------------------------------------------------
-- 1. Store status: pending -> active, or suspended (D14)
-- ---------------------------------------------------------------------------
-- is_active stays: the bot, place_order and the security rules already use
-- it. A trigger keeps it equal to (status = 'active'), so only status is
-- ever changed.

alter table stores add column if not exists status text;

update stores set status = case when is_active then 'active' else 'suspended' end
where status is null;

alter table stores
  alter column status set default 'pending',
  alter column status set not null;

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'stores_status_check') then
    alter table stores add constraint stores_status_check
      check (status in ('pending', 'active', 'suspended'));
  end if;
end;
$$;

create or replace function sync_store_is_active()
returns trigger
language plpgsql
as $$
begin
  new.is_active := (new.status = 'active');
  return new;
end;
$$;

drop trigger if exists trg_sync_store_is_active on stores;
create trigger trg_sync_store_is_active
  before insert or update of status, is_active on stores
  for each row execute function sync_store_is_active();


-- ---------------------------------------------------------------------------
-- 2. Plans (D16): a name only for now; nothing is limited yet
-- ---------------------------------------------------------------------------

update stores set plan = 'basic' where plan is null or plan not in ('free', 'basic', 'pro');

alter table stores
  alter column plan set default 'free',
  alter column plan set not null;

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'stores_plan_check') then
    alter table stores add constraint stores_plan_check check (plan in ('free', 'basic', 'pro'));
  end if;
end;
$$;


-- ---------------------------------------------------------------------------
-- 3. One store per bot
-- ---------------------------------------------------------------------------
-- A bot keeps its Telegram id when its token is regenerated in @BotFather,
-- so the id (from getMe) is what must be unique, not only the token.
-- The backend fills these in when it checks a token (existing stores: when
-- scripts/connect_store.py is run again).

alter table stores
  add column if not exists telegram_bot_id bigint,
  add column if not exists telegram_bot_username text;

create unique index if not exists stores_telegram_bot_id_unique
  on stores (telegram_bot_id) where telegram_bot_id is not null;
create unique index if not exists stores_telegram_bot_token_unique
  on stores (telegram_bot_token) where telegram_bot_token is not null;


-- ---------------------------------------------------------------------------
-- 4. Link codes: connect the staff group or the channel with /link <code>
-- ---------------------------------------------------------------------------
-- The owner gets a code in the dashboard and sends "/link <code>" in the
-- staff group (or the channel); the bot saves that chat's id on the store.
-- One code at a time per store, used once, valid for 30 minutes. Only the
-- backend reads it (not granted to the dashboard below).

alter table stores
  add column if not exists link_code text,
  add column if not exists link_code_expires_at timestamptz;


-- ---------------------------------------------------------------------------
-- 5. What the dashboard can read on stores
-- ---------------------------------------------------------------------------
-- 002 grants column by column: add the new safe columns. The bot token,
-- webhook secret and link code stay backend-only.

grant select (status, telegram_bot_username) on table stores to authenticated;


-- ---------------------------------------------------------------------------
-- 6. Platform admins (D15)
-- ---------------------------------------------------------------------------
-- Only the backend reads this table (it has no rules for the dashboard, so
-- logged-in users can't read or change it).

create table if not exists platform_admins (
  user_id uuid primary key references auth.users(id) on delete cascade,
  created_at timestamptz not null default now()
);

alter table platform_admins enable row level security;


-- ---------------------------------------------------------------------------
-- 7. Staff invitations
-- ---------------------------------------------------------------------------
-- The owner invites someone by email. When that person logs in to the
-- dashboard (new or existing account, with that email confirmed), the
-- backend adds them to store_staff and deletes the invitation.

create table if not exists store_invites (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  email text not null,                 -- stored lowercase
  role text not null default 'staff' check (role in ('staff')),
  invited_by uuid references auth.users(id) on delete set null,
  created_at timestamptz not null default now(),
  constraint store_invites_unique unique (store_id, email),
  constraint store_invites_email_lowercase check (email = lower(email))
);

alter table store_invites enable row level security;

-- Staff can see their own store's pending invitations in the dashboard.
-- Creating and deleting them goes through the backend (owner only).
drop policy if exists "staff sees own store invites" on store_invites;
create policy "staff sees own store invites" on store_invites
  for select using (is_store_member(store_id));

grant select on table store_invites to authenticated;


-- ---------------------------------------------------------------------------
-- 8. create_store: the store and its owner in one step
-- ---------------------------------------------------------------------------
-- Called by the backend after it checked the bot token with Telegram.
-- Returns the new store's id. A bot that another store already uses fails
-- with a unique violation (23505).

create or replace function public.create_store(
  p_name text,
  p_bot_token text,
  p_bot_id bigint,
  p_bot_username text,
  p_webhook_secret text,
  p_owner uuid
) returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_store_id uuid;
begin
  insert into stores (name, telegram_bot_token, telegram_bot_id, telegram_bot_username,
                      webhook_secret, status, plan)
  values (trim(p_name), p_bot_token, p_bot_id, p_bot_username, p_webhook_secret, 'pending', 'free')
  returning id into v_store_id;

  insert into store_staff (store_id, user_id, role) values (v_store_id, p_owner, 'owner');
  return v_store_id;
end;
$$;


-- ---------------------------------------------------------------------------
-- 9. accept_store_invites: a logged-in user joins the stores that invited them
-- ---------------------------------------------------------------------------
-- Returns how many stores they joined. Someone already on the staff keeps
-- their role (an owner is never turned into staff).

create or replace function public.accept_store_invites(p_user uuid, p_email text)
returns int
language plpgsql
security definer
set search_path = public
as $$
declare
  v_count int;
begin
  with invites as (
    delete from store_invites where email = lower(trim(p_email))
    returning store_id, role
  ), joined as (
    insert into store_staff (store_id, user_id, role)
    select store_id, p_user, role from invites
    on conflict (store_id, user_id) do nothing
    returning 1
  )
  select count(*) into v_count from joined;
  return v_count;
end;
$$;


-- Only the backend (service_role) may call these.
revoke all on function public.create_store(text, text, bigint, text, text, uuid)
  from public, anon, authenticated;
revoke all on function public.accept_store_invites(uuid, text) from public, anon, authenticated;
grant execute on function public.create_store(text, text, bigint, text, text, uuid) to service_role;
grant execute on function public.accept_store_invites(uuid, text) to service_role;


commit;
