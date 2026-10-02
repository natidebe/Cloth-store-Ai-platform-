-- 012_counter_sales.sql
--
-- Run in the Supabase SQL Editor, after 011_store_profile_structured.sql.
-- Everything is in one transaction: if any part fails, nothing is applied.
-- Safe to run again.
--
-- Phase 12, sales in the shop (decisions D53–D57):
--   D53  the owner may agree any price; staff down to a limit (staff_discount_percent)
--   D54  the listed price never changes: each line keeps the listed AND the paid price
--   D55  the last piece held by an online order: warn, staff decide
--   D56  any payment method (free text, e.g. "Cash", "Telebirr")
--   D57  counter sales count in the analytics, with Telegram vs in shop


begin;


-- ---------------------------------------------------------------------------
-- 1. Where a sale happened, how it was paid, who sold it
-- ---------------------------------------------------------------------------

alter table orders
  add column if not exists channel text not null default 'telegram',
  add column if not exists payment_method text,      -- counter sales: "Cash", "Telebirr", ...
  add column if not exists payment_note text,
  add column if not exists sold_by_telegram_id bigint, -- counter sales: the staff member
  add column if not exists sold_by_name text,
  add column if not exists note text;

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'orders_channel_check') then
    alter table orders add constraint orders_channel_check check (channel in ('telegram', 'in_shop'));
  end if;
  if not exists (select 1 from pg_constraint where conname = 'orders_counter_texts_length') then
    alter table orders add constraint orders_counter_texts_length check (
      coalesce(length(payment_method), 0) <= 60 and coalesce(length(payment_note), 0) <= 300
      and coalesce(length(sold_by_name), 0) <= 100 and coalesce(length(note), 0) <= 500);
  end if;
end;
$$;

-- The listed price at the time of the sale, next to the price paid (D54).
-- Older lines have none: their listed price was the price paid.
alter table order_items add column if not exists list_price numeric;

-- The staff discount limit, in percent of the listed price (D53).
alter table stores add column if not exists staff_discount_percent numeric not null default 10;

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'stores_staff_discount_check') then
    alter table stores add constraint stores_staff_discount_check
      check (staff_discount_percent >= 0 and staff_discount_percent <= 100);
  end if;
end;
$$;


-- ---------------------------------------------------------------------------
-- 2. record_counter_sale: a sale in the shop, all or nothing
-- ---------------------------------------------------------------------------
-- p_items: [{"variant_id": "...", "quantity": 1, "price": 9000}, ...], each
-- variant once. p_max_discount_percent: null for the owner (any price),
-- the store's limit for staff. p_allow_held: sell even what an online
-- order is holding (D55); the held orders are returned so staff can call
-- those customers. p_idempotency_key: the same sale sent twice is saved once.
--
-- Returns {"order_id", "total", "list_total", "already_saved", "held_orders": [order ids]}.
-- Errors (the message is the code): store_not_found, empty_order,
-- duplicate_item, invalid_quantity, variant_not_found, price_missing,
-- price_above_list, discount_too_large, insufficient_stock, held_by_online_order.

create or replace function public.record_counter_sale(
  p_store_id uuid,
  p_items jsonb,
  p_payment_method text,
  p_payment_note text,
  p_sold_by_telegram_id bigint,
  p_sold_by_name text,
  p_contact_name text,
  p_contact_phone text,
  p_note text,
  p_max_discount_percent numeric,
  p_allow_held boolean,
  p_idempotency_key text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_order_id uuid;
  v_item jsonb;
  v_variant record;
  v_quantity int;
  v_price numeric;
  v_listed numeric;
  v_held int;
  v_total numeric := 0;
  v_list_total numeric := 0;
  v_held_orders uuid[] := '{}';
  v_seen uuid[] := '{}';
  v_lines jsonb := '[]';
begin
  if not exists (select 1 from stores where id = p_store_id and is_active) then
    raise exception 'store_not_found';
  end if;

  -- The same sale again (e.g. the button pressed twice): the first one.
  select id into v_order_id from orders
    where store_id = p_store_id and idempotency_key = p_idempotency_key;
  if v_order_id is not null then
    return jsonb_build_object('order_id', v_order_id, 'already_saved', true,
      'total', (select total_price from orders where id = v_order_id),
      'list_total', null, 'held_orders', '[]'::jsonb);
  end if;

  if p_items is null or jsonb_array_length(p_items) = 0 then
    raise exception 'empty_order';
  end if;

  -- Check every line first (lock the variants: no one else changes them meanwhile).
  for v_item in select * from jsonb_array_elements(p_items) loop
    if (v_item->>'variant_id')::uuid = any(v_seen) then
      raise exception 'duplicate_item';
    end if;
    v_seen := v_seen || (v_item->>'variant_id')::uuid;

    v_quantity := (v_item->>'quantity')::int;
    if v_quantity is null or v_quantity < 1 or v_quantity > 1000 then
      raise exception 'invalid_quantity';
    end if;

    select v.id, v.stock_quantity, coalesce(v.price_override, p.base_price) as listed
      into v_variant
      from product_variants v join products p on p.id = v.product_id
      where v.id = (v_item->>'variant_id')::uuid and v.store_id = p_store_id
      for update of v;
    if not found then
      raise exception 'variant_not_found';
    end if;
    v_listed := v_variant.listed;
    if v_listed is null then
      raise exception 'price_missing';
    end if;

    v_price := round((v_item->>'price')::numeric, 2);
    if v_price is null or v_price < 0 then
      raise exception 'invalid_price';
    end if;
    if v_price > v_listed then
      raise exception 'price_above_list';
    end if;
    if p_max_discount_percent is not null
       and v_price < round(v_listed * (100 - p_max_discount_percent) / 100, 2) then
      raise exception 'discount_too_large';
    end if;

    if v_variant.stock_quantity < v_quantity then
      raise exception 'insufficient_stock' using detail = v_variant.id::text;
    end if;
    v_held := held_quantity(p_store_id, v_variant.id);
    if v_variant.stock_quantity - v_held < v_quantity then
      if not p_allow_held then
        raise exception 'held_by_online_order' using detail = v_variant.id::text;
      end if;
      v_held_orders := v_held_orders || array(
        select distinct o.id from orders o join order_items oi on oi.order_id = o.id
        where o.store_id = p_store_id and oi.variant_id = v_variant.id
          and o.status = 'pending' and o.payment_status = 'unpaid' and o.reserved_until > now());
    end if;

    v_total := v_total + v_price * v_quantity;
    v_list_total := v_list_total + v_listed * v_quantity;
    v_lines := v_lines || jsonb_build_object('variant_id', v_variant.id, 'quantity', v_quantity,
                                             'price', v_price, 'list_price', v_listed);
  end loop;

  -- Save it: the order (paid, handed over), its lines, the stock, the payment.
  insert into orders (store_id, customer_id, status, payment_status, fulfillment_method,
                      total_price, contact_name, contact_phone, idempotency_key, channel,
                      payment_method, payment_note, sold_by_telegram_id, sold_by_name, note)
  values (p_store_id, null, 'delivered', 'paid', 'pickup', v_total,
          nullif(trim(p_contact_name), ''), nullif(trim(p_contact_phone), ''), p_idempotency_key,
          'in_shop', nullif(trim(p_payment_method), ''), nullif(trim(p_payment_note), ''),
          p_sold_by_telegram_id, p_sold_by_name, nullif(trim(p_note), ''))
  returning id into v_order_id;

  insert into order_items (order_id, variant_id, quantity, price, list_price)
  select v_order_id, (l->>'variant_id')::uuid, (l->>'quantity')::int,
         (l->>'price')::numeric, (l->>'list_price')::numeric
  from jsonb_array_elements(v_lines) l;

  update product_variants v set stock_quantity = v.stock_quantity - (l->>'quantity')::int
  from jsonb_array_elements(v_lines) l
  where v.id = (l->>'variant_id')::uuid and v.store_id = p_store_id;

  if v_total > 0 then  -- payments must be above 0: a gift has none
    insert into payments (order_id, amount, method, confirmed_by_telegram_id, confirmed_by_name)
    values (v_order_id, v_total, nullif(trim(p_payment_method), ''), p_sold_by_telegram_id, p_sold_by_name);
  end if;

  return jsonb_build_object(
    'order_id', v_order_id, 'already_saved', false, 'total', v_total, 'list_total', v_list_total,
    'held_orders', (select coalesce(jsonb_agg(distinct x), '[]'::jsonb) from unnest(v_held_orders) x));
end;
$$;


-- ---------------------------------------------------------------------------
-- 3. store_analytics: also Telegram vs in shop, discounts, per seller (D57)
-- ---------------------------------------------------------------------------
-- Same as 010, plus: telegram_orders, in_shop_sales, in_shop_revenue,
-- discount_total, discounted_items, sellers.

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
  ), shop_lines as (
    select oi.quantity, oi.price, oi.list_price
    from period_orders o join order_items oi on oi.order_id = o.id
    where o.channel = 'in_shop'
  ), sellers as (
    select o.sold_by_telegram_id as telegram_id, max(o.sold_by_name) as name,
           count(*) as sales, coalesce(sum(o.total_price), 0) as revenue
    from period_orders o
    where o.channel = 'in_shop'
    group by o.sold_by_telegram_id
    order by count(*) desc
  )
  select jsonb_build_object(
    'revenue', (select coalesce(sum(amount), 0) from period_payments),
    'payments', (select count(*) from period_payments),
    'orders_placed', (select count(*) from period_orders),
    'orders_paid', (select count(*) from period_orders where payment_status = 'paid'),
    'unpaid_orders', (select count(*) from period_orders where payment_status = 'unpaid'),
    'delivery_orders', (select count(*) from period_orders where fulfillment_method = 'delivery'),
    'pickup_orders', (select count(*) from period_orders
                      where fulfillment_method = 'pickup' and channel = 'telegram'),
    'telegram_orders', (select count(*) from period_orders where channel = 'telegram'),
    'in_shop_sales', (select count(*) from period_orders where channel = 'in_shop'),
    'in_shop_revenue', (select coalesce(sum(total_price), 0) from period_orders where channel = 'in_shop'),
    'discount_total', (select coalesce(sum((list_price - price) * quantity), 0) from shop_lines
                       where list_price is not null and list_price > price),
    'discounted_items', (select coalesce(sum(quantity), 0) from shop_lines
                         where list_price is not null and list_price > price),
    'new_customers', (select count(*) from customers c
                      where c.store_id = p_store_id
                        and c.created_at >= p_from and c.created_at < p_to),
    'per_day', coalesce((select jsonb_agg(jsonb_build_object(
                  'day', day, 'placed', placed, 'paid', paid, 'revenue', revenue) order by day)
                from per_day), '[]'::jsonb),
    'top_products', coalesce((select jsonb_agg(jsonb_build_object(
                  'product_id', product_id, 'name', name, 'code', code,
                  'quantity', quantity, 'revenue', revenue))
                from top), '[]'::jsonb),
    'sellers', coalesce((select jsonb_agg(jsonb_build_object(
                  'telegram_id', telegram_id, 'name', name, 'sales', sales, 'revenue', revenue))
                from sellers), '[]'::jsonb)
  );
$$;


-- Only the backend (service_role) may call these.
revoke all on function public.record_counter_sale(uuid, jsonb, text, text, bigint, text, text, text,
                                                  text, numeric, boolean, text)
  from public, anon, authenticated;
grant execute on function public.record_counter_sale(uuid, jsonb, text, text, bigint, text, text, text,
                                                     text, numeric, boolean, text)
  to service_role;
revoke all on function public.store_analytics(uuid, timestamptz, timestamptz)
  from public, anon, authenticated;
grant execute on function public.store_analytics(uuid, timestamptz, timestamptz) to service_role;


commit;
