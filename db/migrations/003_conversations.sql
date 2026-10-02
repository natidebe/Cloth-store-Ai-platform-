-- 003_conversations.sql
--
-- Run once in the Supabase SQL Editor, after 002_platform_updates.sql.
-- Everything is in one transaction: if any part fails, nothing is applied.
--
-- Decisions used (see BUILD_PLAN.md):
--   D8   tables: conversations (with version number), messages, inbox
--   D20  quick bursts: wait 2 seconds (handled in the backend, not here)
--
-- Three tables:
--   inbox          every customer message from Telegram, saved BEFORE we
--                  answer Telegram, so nothing is lost if the server crashes
--   conversations  one per customer per store: the order in progress, a
--                  version number, and whether the bot is paused for staff
--   messages       the chat history (customer, assistant, and staff)

begin;


-- ---------------------------------------------------------------------------
-- 1. conversations
-- ---------------------------------------------------------------------------
-- One row per customer per store. The customer must exist in the SAME store:
-- the foreign key is on (store_id, telegram_id), which customers already has
-- as a unique pair.
--
-- version goes up by one on every save. The backend saves only if the
-- version is still the one it loaded, so two runs can't overwrite each other.

create table conversations (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  telegram_id bigint not null,
  order_draft jsonb not null default '{}'::jsonb,
  version int not null default 0,
  bot_paused boolean not null default false,    -- true while staff handle the chat
  paused_at timestamptz,
  paused_by uuid references auth.users(id) on delete set null,
  last_message_at timestamptz,                  -- last customer message; used for expiry
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (store_id, telegram_id),
  unique (id, store_id),                        -- lets messages check their store matches
  foreign key (store_id, telegram_id)
    references customers(store_id, telegram_id) on delete cascade,
  constraint conversations_version_not_negative check (version >= 0)
);


-- ---------------------------------------------------------------------------
-- 2. messages
-- ---------------------------------------------------------------------------
-- The foreign key on (conversation_id, store_id) makes it impossible to put
-- a message in another store's conversation.
-- update_id is the Telegram update a customer message came from. It's
-- unique per store, so saving the same message twice (after a retry) does
-- nothing the second time. Bot and staff messages have no update_id.

create table messages (
  id bigint generated always as identity primary key,  -- also gives the order
  store_id uuid not null references stores(id) on delete cascade,
  conversation_id uuid not null,
  role text not null check (role in ('customer', 'assistant', 'staff')),
  kind text not null default 'text'
    check (kind in ('text', 'photo', 'sticker', 'voice', 'document', 'other')),
  content text,
  update_id bigint,
  telegram_message_id bigint,
  created_at timestamptz not null default now(),
  foreign key (conversation_id, store_id)
    references conversations(id, store_id) on delete cascade,
  constraint messages_update_unique unique (store_id, update_id)
);

create index idx_messages_conversation on messages(conversation_id, id desc);


-- ---------------------------------------------------------------------------
-- 3. inbox
-- ---------------------------------------------------------------------------
-- Status: received -> processing -> done
--                              \-> received again (retry) -> ... -> failed
-- (store_id, update_id) is unique: Telegram sometimes resends an update, and
-- the second copy is recognised and ignored. Each bot numbers its own
-- updates, so update_id alone isn't unique across stores.

create table inbox (
  id bigint generated always as identity primary key,
  store_id uuid not null references stores(id) on delete cascade,
  update_id bigint not null,
  telegram_id bigint not null,                  -- the customer who sent it
  payload jsonb not null,                       -- the Telegram update, as received
  status text not null default 'received'
    check (status in ('received', 'processing', 'done', 'failed')),
  attempts int not null default 0,
  last_error text,
  received_at timestamptz not null default now(),
  claimed_at timestamptz,                       -- when processing last started
  finished_at timestamptz,
  constraint inbox_update_unique unique (store_id, update_id)
);

create index idx_inbox_customer on inbox(store_id, telegram_id, status);
-- For the recovery sweep: only unfinished rows are indexed.
create index idx_inbox_unfinished on inbox(status, received_at)
  where status in ('received', 'processing');


-- ---------------------------------------------------------------------------
-- 4. claim_inbox: take a customer's waiting messages
-- ---------------------------------------------------------------------------
-- In one UPDATE: every 'received' message of this customer becomes
-- 'processing' and its attempt count goes up. Returns the claimed rows,
-- oldest first. Two workers can't claim the same row.

create or replace function public.claim_inbox(p_store_id uuid, p_telegram_id bigint)
returns setof inbox
language sql
set search_path = public
as $$
  update inbox
  set status = 'processing', attempts = attempts + 1, claimed_at = now()
  where store_id = p_store_id
    and telegram_id = p_telegram_id
    and status = 'received'
  returning *;
$$;


-- ---------------------------------------------------------------------------
-- 5. release_inbox: handling failed, try again later or give up
-- ---------------------------------------------------------------------------
-- Rows that have been tried p_max_attempts times become 'failed'; the others
-- go back to 'received' so the recovery sweep retries them.
-- Returns the rows with their new status.

create or replace function public.release_inbox(
  p_store_id uuid,
  p_ids bigint[],
  p_error text,
  p_max_attempts int
)
returns setof inbox
language sql
set search_path = public
as $$
  update inbox
  set status = case when attempts >= p_max_attempts then 'failed' else 'received' end,
      last_error = left(p_error, 1000),
      finished_at = case when attempts >= p_max_attempts then now() else null end
  where store_id = p_store_id
    and id = any(p_ids)
    and status = 'processing'
  returning *;
$$;


-- ---------------------------------------------------------------------------
-- 6. Security
-- ---------------------------------------------------------------------------
-- Staff (dashboard) may read their own store's conversations and messages.
-- Changes (pausing the bot, sending as staff) go through the backend.
-- inbox has no rules at all: only the backend (service_role) can see it.

alter table conversations enable row level security;
alter table messages enable row level security;
alter table inbox enable row level security;

create policy "staff sees own conversations" on conversations
  for select using (is_store_member(store_id));

create policy "staff sees own messages" on messages
  for select using (is_store_member(store_id));

revoke all on function public.claim_inbox(uuid, bigint) from public, anon, authenticated;
revoke all on function public.release_inbox(uuid, bigint[], text, int) from public, anon, authenticated;
grant execute on function public.claim_inbox(uuid, bigint) to service_role;
grant execute on function public.release_inbox(uuid, bigint[], text, int) to service_role;


commit;
