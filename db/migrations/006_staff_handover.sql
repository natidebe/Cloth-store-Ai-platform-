-- 006_staff_handover.sql
--
-- Run once in the Supabase SQL Editor, after 005_orders_agent.sql.
-- Everything is in one transaction: if any part fails, nothing is applied.
--
-- Decisions used (see BUILD_PLAN.md, Phase 9):
--   D9   the bot resumes when staff press "Hand back to bot", or automatically
--        2 hours after the last staff activity
--   Staff reply to a customer by using Telegram's "Reply" on the bot's
--        message about that customer in the staff group
--   Anyone in the staff group may act; we record who did


begin;


-- ---------------------------------------------------------------------------
-- 1. staff_messages: which customer a staff-group message is about
-- ---------------------------------------------------------------------------
-- Every alert or forwarded customer message the bot posts in the staff group
-- is recorded here, so when a staff member replies to it (or presses one of
-- its buttons) we know which customer, and which order, it's about.
-- The message id is only unique inside one Telegram chat, hence the
-- staff_chat_id in the unique key.

create table staff_messages (
  id bigint generated always as identity primary key,
  store_id uuid not null references stores(id) on delete cascade,
  staff_chat_id bigint not null,          -- the staff group
  message_id bigint not null,             -- the bot's message in that group
  telegram_id bigint not null,            -- the customer it's about
  order_id uuid references orders(id) on delete set null,
  created_at timestamptz not null default now(),
  constraint staff_messages_unique unique (store_id, staff_chat_id, message_id)
);

-- Only the backend uses this table.
alter table staff_messages enable row level security;


-- ---------------------------------------------------------------------------
-- 2. When staff last acted in a handed-over chat (for the 2-hour resume)
-- ---------------------------------------------------------------------------

alter table conversations add column staff_active_at timestamptz;

-- For the auto-resume sweep: only paused chats are indexed.
create index idx_conversations_paused on conversations(paused_at)
  where bot_paused;


-- ---------------------------------------------------------------------------
-- 3. Who confirmed a payment from the staff Telegram group
-- ---------------------------------------------------------------------------
-- payments.confirmed_by is a dashboard login. A confirmation from the staff
-- group has no login, so we record the Telegram user instead.

alter table payments
  add column confirmed_by_telegram_id bigint,
  add column confirmed_by_name text;


commit;
