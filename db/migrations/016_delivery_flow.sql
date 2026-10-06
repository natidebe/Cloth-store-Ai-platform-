-- 016_delivery_flow.sql
--
-- Run in the Supabase SQL Editor, after 015_quick_wins.sql.
-- Everything is in one transaction: if any part fails, nothing is applied.
-- Safe to run again.
--
-- Phase 15b, delivery orders paid on arrival (decisions D76–D78):
--   D76  staff tap 🚚 On the way first, then ✅ Delivered & paid (or ❌ Not delivered)
--   D77  a delivery order takes its items out of stock when it leaves the shop
--        (On the way); Not delivered puts them back and cancels the order
--   D78  the bot asks clearly for the payment screenshot (backend texts only)
--
-- Three all-or-nothing steps, each locking the order so two staff tapping
-- at once can't both win:
--   dispatch_order   pending delivery order -> out_for_delivery, stock goes down
--                    (a paid one: stock was already taken at payment)
--   deliver_order    out_for_delivery -> delivered; unpaid: the payment is recorded
--   return_order     out_for_delivery and unpaid -> cancelled, stock goes back
-- And confirm_payment no longer takes stock for an order already on the way.


begin;


-- ---------------------------------------------------------------------------
-- 1. dispatch_order: 🚚 On the way
-- ---------------------------------------------------------------------------

create or replace function public.dispatch_order(
  p_store_id uuid,
  p_order_id uuid,
  p_by_telegram_id bigint,
  p_by_name text
) returns void
language plpgsql
set search_path = public
as $$
declare
  v_order orders%rowtype;
  v_line record;
  v_stock int;
begin
  select * into v_order from orders
  where id = p_order_id and store_id = p_store_id
  for update;

  if not found then
    raise exception 'order_not_found';
  end if;
  if v_order.status = 'cancelled' then
    raise exception 'order_cancelled';
  end if;
  if v_order.fulfillment_method is distinct from 'delivery' then
    raise exception 'not_delivery';
  end if;
  if v_order.status not in ('pending', 'confirmed') then
    raise exception 'already_dispatched';
  end if;

  -- Unpaid: the items leave the shop now, so the stock goes down now (D77).
  -- (Paid: confirm_payment already took them.) Other orders' holds count,
  -- like in confirm_payment; this order's own hold doesn't.
  if v_order.payment_status = 'unpaid' then
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
  end if;

  update orders
  set status = 'out_for_delivery',
      reserved_until = null,
      status_changed_at = now(),
      status_changed_by_telegram_id = p_by_telegram_id,
      status_changed_by_name = left(p_by_name, 200)
  where id = p_order_id;
end;
$$;


-- ---------------------------------------------------------------------------
-- 2. deliver_order: ✅ Delivered & paid
-- ---------------------------------------------------------------------------
-- Returns the new payment's id, or null if the order was already paid (or
-- has no total).

create or replace function public.deliver_order(
  p_store_id uuid,
  p_order_id uuid,
  p_amount numeric,
  p_method text,
  p_by_telegram_id bigint,
  p_by_name text
) returns uuid
language plpgsql
set search_path = public
as $$
declare
  v_order orders%rowtype;
  v_payment_id uuid;
begin
  select * into v_order from orders
  where id = p_order_id and store_id = p_store_id
  for update;

  if not found then
    raise exception 'order_not_found';
  end if;
  if v_order.status = 'cancelled' then
    raise exception 'order_cancelled';
  end if;
  if v_order.status = 'delivered' then
    raise exception 'already_delivered';
  end if;
  if v_order.status <> 'out_for_delivery' then
    raise exception 'not_on_the_way';
  end if;

  if v_order.payment_status = 'unpaid' and coalesce(p_amount, 0) > 0 then
    insert into payments (order_id, amount, method, confirmed_by_telegram_id, confirmed_by_name)
    values (p_order_id, p_amount, nullif(trim(p_method), ''), p_by_telegram_id, left(p_by_name, 200))
    returning id into v_payment_id;
  end if;

  update orders
  set status = 'delivered',
      payment_status = 'paid',
      status_changed_at = now(),
      status_changed_by_telegram_id = p_by_telegram_id,
      status_changed_by_name = left(p_by_name, 200)
  where id = p_order_id;

  return v_payment_id;
end;
$$;


-- ---------------------------------------------------------------------------
-- 3. return_order: ❌ Not delivered
-- ---------------------------------------------------------------------------

create or replace function public.return_order(
  p_store_id uuid,
  p_order_id uuid,
  p_by_telegram_id bigint,
  p_by_name text
) returns void
language plpgsql
set search_path = public
as $$
declare
  v_order orders%rowtype;
begin
  select * into v_order from orders
  where id = p_order_id and store_id = p_store_id
  for update;

  if not found then
    raise exception 'order_not_found';
  end if;
  if v_order.status = 'cancelled' then
    raise exception 'order_cancelled';
  end if;
  if v_order.status <> 'out_for_delivery' then
    raise exception 'not_on_the_way';
  end if;
  if v_order.payment_status <> 'unpaid' then
    raise exception 'already_paid';  -- money was taken: staff refund it by hand first
  end if;

  update product_variants v
  set stock_quantity = v.stock_quantity + l.quantity
  from (select variant_id, sum(quantity)::int as quantity
        from order_items where order_id = p_order_id and variant_id is not null
        group by variant_id) l
  where v.id = l.variant_id and v.store_id = p_store_id;

  update orders
  set status = 'cancelled',
      status_changed_at = now(),
      status_changed_by_telegram_id = p_by_telegram_id,
      status_changed_by_name = left(p_by_name, 200)
  where id = p_order_id;
end;
$$;


-- ---------------------------------------------------------------------------
-- 4. confirm_payment: an order already on the way has its stock taken
-- ---------------------------------------------------------------------------
-- Same as in 005, except an unpaid order that is out_for_delivery (sent out
-- before payment, D77) doesn't take its items out of stock a second time.

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

  if v_order.status not in ('out_for_delivery', 'delivered') then
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
  end if;

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
-- 5. Only the backend (service_role) may call these
-- ---------------------------------------------------------------------------

revoke all on function public.dispatch_order(uuid, uuid, bigint, text) from public, anon, authenticated;
revoke all on function public.deliver_order(uuid, uuid, numeric, text, bigint, text) from public, anon, authenticated;
revoke all on function public.return_order(uuid, uuid, bigint, text) from public, anon, authenticated;
revoke all on function public.confirm_payment(uuid, uuid, numeric, text, uuid) from public, anon, authenticated;
grant execute on function public.dispatch_order(uuid, uuid, bigint, text) to service_role;
grant execute on function public.deliver_order(uuid, uuid, numeric, text, bigint, text) to service_role;
grant execute on function public.return_order(uuid, uuid, bigint, text) to service_role;
grant execute on function public.confirm_payment(uuid, uuid, numeric, text, uuid) to service_role;


commit;
