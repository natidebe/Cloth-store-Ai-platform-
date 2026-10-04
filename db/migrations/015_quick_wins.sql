-- 015_quick_wins.sql
--
-- Run in the Supabase SQL Editor, after 014_subscriptions.sql.
-- Everything is in one transaction: if any part fails, nothing is applied.
-- Safe to run again.
--
-- Phase 15, quick wins (decisions D70–D75):
--   D70/D71  a morning summary of yesterday for the owner (in Amharic or English, or off)
--   D72/D73  export a month for the accountant (no database change: read only)
--   D74/D75  staff tap On the way / Delivered (pickup: Ready / Picked up); we keep who and when


begin;


-- ---------------------------------------------------------------------------
-- 1. The morning summary: the owner's choice, and the days already sent
-- ---------------------------------------------------------------------------

alter table stores
  add column if not exists daily_summary text not null default 'am';   -- 'am', 'en' or 'off'

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'stores_daily_summary_check') then
    alter table stores add constraint stores_daily_summary_check
      check (daily_summary in ('am', 'en', 'off'));
  end if;
end;
$$;

-- One row per shop per day it was sent: the check runs every 15 minutes in
-- the morning, and a restart mustn't send it twice.
create table if not exists daily_summaries (
  store_id uuid not null references stores(id) on delete cascade,
  day date not null,              -- the day it was about (Addis Ababa), i.e. yesterday
  sent_at timestamptz not null default now(),
  primary key (store_id, day)
);
alter table daily_summaries enable row level security;


-- ---------------------------------------------------------------------------
-- 2. Order updates (D74): who moved the order on, and when
-- ---------------------------------------------------------------------------
-- The stages themselves exist since 002 (out_for_delivery, delivered).

alter table orders
  add column if not exists status_changed_at timestamptz,
  add column if not exists status_changed_by_telegram_id bigint,
  add column if not exists status_changed_by_name text;

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'orders_status_changed_by_name_length') then
    alter table orders add constraint orders_status_changed_by_name_length
      check (length(status_changed_by_name) <= 200);
  end if;
end;
$$;

-- The export reads a month of orders at a time.
create index if not exists orders_store_created_idx on orders (store_id, created_at);


commit;
