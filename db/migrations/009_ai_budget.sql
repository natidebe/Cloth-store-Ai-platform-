-- 009_ai_budget.sql
--
-- Run in the Supabase SQL Editor, after 008_store_onboarding.sql.
-- Everything is in one transaction: if any part fails, nothing is applied.
-- Safe to run again.
--
-- Phase 10, decision D21: a daily AI budget per store, counted in AI calls
-- (works with any AI provider). The limit itself is in the backend's .env
-- (AI_DAILY_CALLS_PER_STORE). Only typed messages the scripted flow can't
-- read use the AI; buttons and product codes don't count.


begin;


-- One row per store per day (Addis Ababa date).
create table if not exists ai_usage (
  store_id uuid not null references stores(id) on delete cascade,
  day date not null,
  calls int not null default 0 check (calls >= 0),
  primary key (store_id, day)
);

-- Backend only: no rules for the dashboard, so logged-in users can't read
-- or change it.
alter table ai_usage enable row level security;


-- ---------------------------------------------------------------------------
-- use_ai_call: count one AI call for today and return today's total.
-- ---------------------------------------------------------------------------
-- The backend allows the call if the total is within the limit. One
-- statement (insert or add one), so two messages at the same moment can't
-- both take the last call. Calls over the limit are counted too: the first
-- one over (limit + 1) is when staff are told.

create or replace function public.use_ai_call(p_store_id uuid)
returns int
language sql
security definer
set search_path = public
as $$
  insert into ai_usage (store_id, day, calls)
  values (p_store_id, (now() at time zone 'Africa/Addis_Ababa')::date, 1)
  on conflict (store_id, day) do update set calls = ai_usage.calls + 1
  returning calls;
$$;

revoke all on function public.use_ai_call(uuid) from public, anon, authenticated;
grant execute on function public.use_ai_call(uuid) to service_role;


commit;
