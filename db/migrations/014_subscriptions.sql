-- 014_subscriptions.sql
--
-- Run in the Supabase SQL Editor, after 013_shop_types.sql.
-- Everything is in one transaction: if any part fails, nothing is applied.
-- Safe to run again.
--

-- Phase 14, subscriptions (decisions D64–D69):
--   D64  a 1-week free trial (plan 'free'); shops already active get one from today
--   D65  Basic / Pro: 3 months per payment, recorded by the platform admin
--   D66  reminders before the end, on the day, and in the grace days (sent once each)
--   D67  3 days of grace, then paused ('suspended', reason 'unpaid'); a payment resumes it
--   D68  AI replies a day per plan (in the app: free 100, basic 100, pro 300)
--   D69  the trial starts when the shop is approved


begin;


-- ---------------------------------------------------------------------------
-- 1. When the shop's trial or paid period ends, and why it's suspended
-- ---------------------------------------------------------------------------

alter table stores
  add column if not exists plan_ends_at timestamptz,   -- null: not started yet (waiting for approval)
  add column if not exists suspended_reason text;      -- 'unpaid' (D67) or 'admin'

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'stores_suspended_reason_check') then
    alter table stores add constraint stores_suspended_reason_check
      check (suspended_reason is null or suspended_reason in ('unpaid', 'admin'));
  end if;
end;
$$;

-- D64: shops already selling get a 1-week trial from today. Suspended ones
-- were suspended by the admin; pending ones start at approval (D69).
update stores set plan_ends_at = now() + interval '7 days'
where plan_ends_at is null and status = 'active';
update stores set suspended_reason = 'admin'
where status = 'suspended' and suspended_reason is null;


-- ---------------------------------------------------------------------------
-- 2. Payments (D65): one row per payment the platform admin records
-- ---------------------------------------------------------------------------

create table if not exists subscription_payments (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  plan text not null check (plan in ('basic', 'pro')),
  months int not null default 3 check (months between 1 and 24),
  amount numeric not null check (amount >= 0),
  method text check (length(method) <= 60),           -- "Telebirr", "CBE", ...
  reference text check (length(reference) <= 120),    -- the transaction number
  recorded_by_telegram_id bigint,
  period_start timestamptz not null,
  period_end timestamptz not null,
  created_at timestamptz not null default now()
);
create index if not exists subscription_payments_store on subscription_payments (store_id, created_at desc);
alter table subscription_payments enable row level security;  -- only the backend (service role)


-- ---------------------------------------------------------------------------
-- 3. Reminders already sent (D66): each one once per end date
-- ---------------------------------------------------------------------------

create table if not exists subscription_notices (
  store_id uuid not null references stores(id) on delete cascade,
  ends_at timestamptz not null,   -- the end date it was about (a payment makes a new one)
  kind text not null,             -- 'before_7', 'before_3', 'before_1', 'ended', 'grace_1', 'grace_2', 'paused'
  sent_at timestamptz not null default now(),
  primary key (store_id, ends_at, kind)
);
alter table subscription_notices enable row level security;


-- ---------------------------------------------------------------------------
-- 4. Record a payment: extend the period and turn an unpaid pause back on,
--    all at once. From the current end, or from now if it has ended.
-- ---------------------------------------------------------------------------

create or replace function record_subscription_payment(
  p_store_id uuid, p_plan text, p_months int, p_amount numeric,
  p_method text, p_reference text, p_recorded_by bigint
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_store stores%rowtype;
  v_start timestamptz;
  v_end timestamptz;
  v_payment uuid;
begin
  select * into v_store from stores where id = p_store_id for update;
  if not found then
    raise exception 'store_not_found' using errcode = 'P0002';
  end if;
  v_start := greatest(now(), coalesce(v_store.plan_ends_at, now()));
  v_end := v_start + make_interval(months => p_months);

  insert into subscription_payments (store_id, plan, months, amount, method, reference,
                                     recorded_by_telegram_id, period_start, period_end)
  values (p_store_id, p_plan, p_months, p_amount, nullif(trim(p_method), ''),
          nullif(trim(p_reference), ''), p_recorded_by, v_start, v_end)
  returning id into v_payment;

  update stores set
    plan = p_plan,
    plan_ends_at = v_end,
    status = case when status = 'suspended' and suspended_reason = 'unpaid' then 'active' else status end,
    suspended_reason = case when status = 'suspended' and suspended_reason = 'unpaid' then null
                            else suspended_reason end
  where id = p_store_id;

  return jsonb_build_object(
    'payment_id', v_payment, 'plan', p_plan, 'period_start', v_start, 'period_end', v_end,
    'resumed', v_store.status = 'suspended' and v_store.suspended_reason = 'unpaid');
end;
$$;

revoke all on function record_subscription_payment(uuid, text, int, numeric, text, text, bigint) from public;
revoke all on function record_subscription_payment(uuid, text, int, numeric, text, text, bigint) from anon, authenticated;


commit;


-- Check (optional): every active shop has an end date a week from now.
-- select name, status, plan, plan_ends_at from stores order by plan_ends_at;
