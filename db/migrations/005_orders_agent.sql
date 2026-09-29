-- 005_orders_agent.sql
--
-- Run once in the Supabase SQL Editor, after 004_store_profile.sql.
-- Everything is in one transaction: if any part fails, nothing is applied.
--
-- Decisions used (see BUILD_PLAN.md):
--   D3   stock goes down when staff confirm payment (unchanged)
--   D19  placing an order HOLDS its items for a short time (5 minutes, set
--        by the backend), so two customers can't both be told to pay for
--        the last pair
--   Payment instructions: each store's own text, stores.payment_instructions
--        (added by 004_store_profile.sql)
--
-- How the hold works: an order that is pending, unpaid, and whose
-- reserved_until is still in the future "holds" its items. The stock
-- other customers can order is stock_quantity minus what is held.
-- After the hold ends the order stays pending; if the items are sold to
-- someone else meanwhile, confirming its payment is refused (as before).

begin;


-- ---------------------------------------------------------------------------
-- 1. New columns
-- ---------------------------------------------------------------------------

-- Until when this order holds its items.
alter table orders add column if not exists reserved_until timestamptz;

create index if not exists idx_order_items_variant on order_items(variant_id);
create index if not exists idx_orders_holding on orders(store_id, reserved_until)
  where status = 'pending' and payment_status = 'unpaid';


-- ---------------------------------------------------------------------------
-- 2. How much of a variant is held
-- ---------------------------------------------------------------------------

-- Held quantity of one variant, not counting p_except_order (the order
-- that is itself being placed or paid).
create or replace function public.held_quantity(
  p_store_id uuid,
  p_variant_id uuid,
  p_except_order uuid default null
)
returns int
language sql
stable
set search_path = public
as $$
  select coalesce(sum(oi.quantity), 0)::int
  from order_items oi
  join orders o on o.id = oi.order_id
  where o.store_id = p_store_id
    and oi.variant_id = p_variant_id
    and o.status = 'pending'
    and o.payment_status = 'unpaid'
    and o.reserved_until > now()
    and (p_except_order is null or o.id <> p_except_order);
$$;

-- Held quantities of several variants at once (for search results).
-- Variants with nothing held are left out.
create or replace function public.held_quantities(p_store_id uuid, p_variant_ids uuid[])
returns table (variant_id uuid, held int)
language sql
stable
set search_path = public
as $$
  select oi.variant_id, sum(oi.quantity)::int
  from order_items oi
  join orders o on o.id = oi.order_id
  where o.store_id = p_store_id
    and oi.variant_id = any(p_variant_ids)
    and o.status = 'pending'
    and o.payment_status = 'unpaid'
    and o.reserved_until > now()
  group by oi.variant_id;
$$;


-- ---------------------------------------------------------------------------
-- 3. place_order: now holds the items
-- ---------------------------------------------------------------------------
-- Same as in 002, plus:
--   - p_hold_minutes: how long the order holds its items
--   - stock is checked against what is NOT held by other orders
--   - each variant row is locked while checking, so two orders for the
--     last pair can't both pass the check
--   - lines for the same variant are added together
-- The function gets a new parameter, so the old version is removed first.

drop function if exists public.place_order(uuid, uuid, jsonb, text, text, text, text, text);

create or replace function public.place_order(
  p_store_id uuid,
  p_customer_id uuid,
  p_items jsonb,
  p_fulfillment_method text,
  p_contact_name text,
  p_contact_phone text,
  p_delivery_address text,
  p_idempotency_key text,
  p_hold_minutes int
)
returns uuid
language plpgsql
set search_path = public
as $$
declare
  v_order_id uuid;
  v_item record;
  v_price numeric;
  v_stock int;
  v_total numeric := 0;
begin
  -- Same key as an earlier call: return that order.
  if p_idempotency_key is not null then
    select id into v_order_id from orders
    where store_id = p_store_id and idempotency_key = p_idempotency_key;
    if found then
      return v_order_id;
    end if;
  end if;

  if not exists (select 1 from stores where id = p_store_id and is_active) then
    raise exception 'store_not_found';
  end if;

  if not exists (select 1 from customers where id = p_customer_id and store_id = p_store_id) then
    raise exception 'customer_not_found';
  end if;

  if p_items is null or jsonb_typeof(p_items) <> 'array' or jsonb_array_length(p_items) = 0 then
    raise exception 'empty_order';
  end if;

  if exists (
    select 1 from jsonb_to_recordset(p_items) as x(variant_id uuid, quantity int)
    where x.quantity is null or x.quantity < 1
  ) then
    raise exception 'invalid_quantity';
  end if;

  if p_fulfillment_method is null or p_fulfillment_method not in ('delivery', 'pickup') then
    raise exception 'invalid_fulfillment';
  end if;

  if p_fulfillment_method = 'delivery' and coalesce(trim(p_delivery_address), '') = '' then
    raise exception 'address_required';
  end if;

  begin
    insert into orders (
      store_id, customer_id, status, payment_status, fulfillment_method,
      contact_name, contact_phone, delivery_address, currency, idempotency_key,
      reserved_until
    )
    values (
      p_store_id, p_customer_id, 'pending', 'unpaid', p_fulfillment_method,
      p_contact_name, p_contact_phone, p_delivery_address, 'ETB', p_idempotency_key,
      now() + make_interval(mins => greatest(coalesce(p_hold_minutes, 0), 0))
    )
    returning id into v_order_id;
  exception when unique_violation then
    -- Another call with the same key finished first.
    select id into v_order_id from orders
    where store_id = p_store_id and idempotency_key = p_idempotency_key;
    return v_order_id;
  end;

  -- One line per variant; variants in a fixed order so two orders can't
  -- block each other while locking.
  for v_item in
    select x.variant_id, sum(x.quantity)::int as quantity
    from jsonb_to_recordset(p_items) as x(variant_id uuid, quantity int)
    group by x.variant_id
    order by x.variant_id
  loop
    select v.stock_quantity, coalesce(v.price_override, p.base_price)
    into v_stock, v_price
    from product_variants v
    join products p on p.id = v.product_id
    where v.id = v_item.variant_id and v.store_id = p_store_id
    for update of v;

    if not found then
      raise exception 'variant_not_found' using detail = coalesce(v_item.variant_id::text, 'null');
    end if;
    if v_price is null then
      raise exception 'price_missing' using detail = v_item.variant_id::text;
    end if;
    if v_stock - held_quantity(p_store_id, v_item.variant_id, v_order_id) < v_item.quantity then
      raise exception 'out_of_stock' using detail = v_item.variant_id::text;
    end if;

    -- order_items.price is the unit price at the time of the order
    insert into order_items (order_id, variant_id, quantity, price)
    values (v_order_id, v_item.variant_id, v_item.quantity, v_price);

    v_total := v_total + v_price * v_item.quantity;
  end loop;

  update orders set total_price = v_total where id = v_order_id;
  return v_order_id;
end;
$$;


-- ---------------------------------------------------------------------------
-- 4. confirm_payment: respects other orders' holds
-- ---------------------------------------------------------------------------
-- Same as in 002, except stock held by OTHER orders can't be used. (This
-- order's own hold is counted as available, whether or not it has ended.)
-- Once paid, the order no longer holds anything: the stock itself is reduced.

create or replace function public.confirm_payment(
  p_store_id uuid,
  p_order_id uuid,
  p_amount numeric,
  p_method text,
  p_staff_id uuid
)
returns uuid
language plpgsql
set search_path = public
as $$
declare
  v_order orders%rowtype;
  v_line record;
  v_stock int;
  v_payment_id uuid;
begin
  -- Lock the order so two staff can't confirm it at the same time.
  select * into v_order from orders
  where id = p_order_id and store_id = p_store_id
  for update;

  if not found then
    raise exception 'order_not_found';
  end if;
  if v_order.status = 'cancelled' then
    raise exception 'order_cancelled';
  end if;
  if v_order.payment_status = 'paid' then
    raise exception 'already_paid';
  end if;

  for v_line in
    select variant_id, sum(quantity)::int as quantity
    from order_items
    where order_id = p_order_id
    group by variant_id
    order by variant_id
  loop
    select stock_quantity into v_stock
    from product_variants
    where id = v_line.variant_id and store_id = p_store_id
    for update;

    if not found
       or v_stock - held_quantity(p_store_id, v_line.variant_id, p_order_id) < v_line.quantity then
      raise exception 'out_of_stock' using detail = coalesce(v_line.variant_id::text, 'null');
    end if;

    update product_variants
    set stock_quantity = stock_quantity - v_line.quantity
    where id = v_line.variant_id and store_id = p_store_id;
  end loop;

  insert into payments (order_id, amount, method, confirmed_by)
  values (p_order_id, p_amount, p_method, p_staff_id)
  returning id into v_payment_id;

  update orders
  set payment_status = 'paid',
      status = case when status = 'pending' then 'confirmed' else status end,
      reserved_until = null
  where id = p_order_id;

  return v_payment_id;
end;
$$;


-- ---------------------------------------------------------------------------
-- 5. Only the backend may call these functions
-- ---------------------------------------------------------------------------

revoke all on function public.place_order(uuid, uuid, jsonb, text, text, text, text, text, int) from public, anon, authenticated;
revoke all on function public.held_quantity(uuid, uuid, uuid) from public, anon, authenticated;
revoke all on function public.held_quantities(uuid, uuid[]) from public, anon, authenticated;

grant execute on function public.place_order(uuid, uuid, jsonb, text, text, text, text, text, int) to service_role;
grant execute on function public.held_quantity(uuid, uuid, uuid) to service_role;
grant execute on function public.held_quantities(uuid, uuid[]) to service_role;


commit;
