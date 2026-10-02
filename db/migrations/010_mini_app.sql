-- 010_mini_app.sql
--
-- Run in the Supabase SQL Editor, after 009_ai_budget.sql.
-- Everything is in one transaction: if any part fails, nothing is applied.
-- Safe to run again (parts that already exist are skipped or replaced).
--
-- Phase 10b, the Telegram Mini App (decisions D41–D47). People sign in with
-- their Telegram account (no passwords): store access comes from the
-- store's staff group (D42), and sign-up happens in the platform bot (D44).
--
-- After running it, make yourself a platform admin by Telegram id (D45).
-- To find your id, open @userinfobot in Telegram and press Start. Then:
--   insert into platform_admin_telegram (telegram_id, name) values (123456789, 'Me');


begin;


-- ---------------------------------------------------------------------------
-- 1. Who created the store (Telegram), and which language they use
-- ---------------------------------------------------------------------------
-- The creator is always an owner in the Mini App, even before a staff group
-- is linked. Older stores (created by hand or with a Supabase login) have
-- none: their staff group's admins are their owners.

alter table stores add column if not exists owner_telegram_id bigint;
create index if not exists idx_stores_owner_telegram on stores (owner_telegram_id);

-- The dashboard reads the store row only through the backend; nothing to grant.


-- ---------------------------------------------------------------------------
-- 2. Platform admins by Telegram id (D45)
-- ---------------------------------------------------------------------------
-- The Mini App knows people by Telegram account; platform_admins (008) is by
-- Supabase login. Backend only (no rules for the dashboard).

create table if not exists platform_admin_telegram (
  telegram_id bigint primary key,
  name text,
  created_at timestamptz not null default now()
);

alter table platform_admin_telegram enable row level security;


-- ---------------------------------------------------------------------------
-- 3. create_store_for_telegram: sign-up from the platform bot (D44)
-- ---------------------------------------------------------------------------
-- Like create_store (008), but the owner is a Telegram account. Pending
-- (D14), free plan (D16). A bot another store uses fails with 23505.

create or replace function public.create_store_for_telegram(
  p_name text,
  p_bot_token text,
  p_bot_id bigint,
  p_bot_username text,
  p_webhook_secret text,
  p_owner_telegram_id bigint
) returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_store_id uuid;
begin
  insert into stores (name, telegram_bot_token, telegram_bot_id, telegram_bot_username,
                      webhook_secret, status, plan, owner_telegram_id)
  values (trim(p_name), p_bot_token, p_bot_id, p_bot_username, p_webhook_secret,
          'pending', 'free', p_owner_telegram_id)
  returning id into v_store_id;
  return v_store_id;
end;
$$;


-- ---------------------------------------------------------------------------
-- 4. store_analytics: the dashboard's numbers in one call
-- ---------------------------------------------------------------------------
-- For one store and a period [p_from, p_to). Days are Addis Ababa days.
-- - revenue: money confirmed by staff in the period (payments.paid_at)
-- - orders placed / paid: orders created in the period (cancelled excluded)
-- - unpaid: orders from the period still waiting for payment
-- - per day: placed, paid and revenue for every day of the period
-- - top products: best sellers by quantity in orders paid in the period
-- - delivery vs pickup, new customers
-- Returns JSON so the backend passes it through unchanged.

create or replace function public.store_analytics(
  p_store_id uuid,
  p_from timestamptz,
  p_to timestamptz
) returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  with period_orders as (
    select o.*
    from orders o
    where o.store_id = p_store_id
      and o.created_at >= p_from and o.created_at < p_to
      and o.status <> 'cancelled'
  ), period_payments as (
    select p.amount, p.paid_at, p.order_id
    from payments p
    join orders o on o.id = p.order_id
    where o.store_id = p_store_id
      and p.paid_at >= p_from and p.paid_at < p_to
  ), days as (
    select generate_series(
      (p_from at time zone 'Africa/Addis_Ababa')::date,
      ((p_to - interval '1 second') at time zone 'Africa/Addis_Ababa')::date,
      interval '1 day'
    )::date as day
  ), per_day as (
    select d.day,
      (select count(*) from period_orders o
        where (o.created_at at time zone 'Africa/Addis_Ababa')::date = d.day) as placed,
      (select count(*) from period_orders o
        where (o.created_at at time zone 'Africa/Addis_Ababa')::date = d.day
          and o.payment_status = 'paid') as paid,
      (select coalesce(sum(p.amount), 0) from period_payments p
        where (p.paid_at at time zone 'Africa/Addis_Ababa')::date = d.day) as revenue
    from days d
  ), top as (
    select pr.id as product_id, pr.name, pr.code,
           sum(oi.quantity) as quantity, sum(oi.quantity * oi.price) as revenue
    from period_payments pp
    join order_items oi on oi.order_id = pp.order_id
    join product_variants v on v.id = oi.variant_id
    join products pr on pr.id = v.product_id
    where pr.store_id = p_store_id
    group by pr.id, pr.name, pr.code
    order by sum(oi.quantity) desc, sum(oi.quantity * oi.price) desc
    limit 5
  )
  select jsonb_build_object(
    'revenue', (select coalesce(sum(amount), 0) from period_payments),
    'payments', (select count(*) from period_payments),
    'orders_placed', (select count(*) from period_orders),
    'orders_paid', (select count(*) from period_orders where payment_status = 'paid'),
    'unpaid_orders', (select count(*) from period_orders where payment_status = 'unpaid'),
    'delivery_orders', (select count(*) from period_orders where fulfillment_method = 'delivery'),
    'pickup_orders', (select count(*) from period_orders where fulfillment_method = 'pickup'),
    'new_customers', (select count(*) from customers c
                      where c.store_id = p_store_id
                        and c.created_at >= p_from and c.created_at < p_to),
    'per_day', coalesce((select jsonb_agg(jsonb_build_object(
                  'day', day, 'placed', placed, 'paid', paid, 'revenue', revenue) order by day)
                from per_day), '[]'::jsonb),
    'top_products', coalesce((select jsonb_agg(jsonb_build_object(
                  'product_id', product_id, 'name', name, 'code', code,
                  'quantity', quantity, 'revenue', revenue))
                from top), '[]'::jsonb)
  );
$$;


-- Only the backend (service_role) may call these.
revoke all on function public.create_store_for_telegram(text, text, bigint, text, text, bigint)
  from public, anon, authenticated;
revoke all on function public.store_analytics(uuid, timestamptz, timestamptz)
  from public, anon, authenticated;
grant execute on function public.create_store_for_telegram(text, text, bigint, text, text, bigint)
  to service_role;
grant execute on function public.store_analytics(uuid, timestamptz, timestamptz) to service_role;


commit;
