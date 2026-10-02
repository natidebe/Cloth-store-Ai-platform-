-- 002_platform_updates.sql
--
-- Run once in the Supabase SQL Editor, after 001_init_schema.sql.
-- Everything is in one transaction: if any part fails, nothing is applied.
--
-- Decisions used (see BUILD_PLAN.md):
--   D3  stock goes down when staff confirm payment, not when the order is placed
--   D4  delivery or pickup; each order keeps its own contact details
--   D5  currency ETB, saved on each order
--   D6  stores handle payments themselves; payment method is free text
--   D7  order: pending, confirmed, out_for_delivery, delivered, cancelled
--       payment: unpaid, paid, refunded
--   If an item sold out before payment is confirmed, confirmation is refused.
--   Staff roles: owner, staff

begin;


-- ---------------------------------------------------------------------------
-- 1. Fix the recursive security rules
-- ---------------------------------------------------------------------------
-- The 001 rules read store_staff from inside store_staff's own rule, which
-- makes Postgres fail with "infinite recursion detected in policy". This
-- helper runs with its owner's rights (security definer), so it reads
-- store_staff without triggering the rules again.

create or replace function public.is_store_member(p_store_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1 from public.store_staff
    where store_id = p_store_id and user_id = auth.uid()
  );
$$;

drop policy if exists "staff sees own stores" on stores;
drop policy if exists "staff sees own store_staff rows" on store_staff;
drop policy if exists "staff manages own products" on products;
drop policy if exists "staff manages own variants" on product_variants;
drop policy if exists "staff manages own customers" on customers;
drop policy if exists "staff manages own orders" on orders;
drop policy if exists "staff manages own order_items" on order_items;
drop policy if exists "staff manages own payments" on payments;

create policy "staff sees own stores" on stores
  for select using (is_store_member(id));

create policy "staff sees own store_staff rows" on store_staff
  for select using (is_store_member(store_id));

create policy "staff manages own products" on products
  for all using (is_store_member(store_id)) with check (is_store_member(store_id));

create policy "staff manages own variants" on product_variants
  for all using (is_store_member(store_id)) with check (is_store_member(store_id));

create policy "staff manages own customers" on customers
  for all using (is_store_member(store_id)) with check (is_store_member(store_id));

create policy "staff manages own orders" on orders
  for all using (is_store_member(store_id)) with check (is_store_member(store_id));

create policy "staff manages own order_items" on order_items
  for all
  using (exists (select 1 from orders o where o.id = order_id and is_store_member(o.store_id)))
  with check (exists (select 1 from orders o where o.id = order_id and is_store_member(o.store_id)));

create policy "staff manages own payments" on payments
  for all
  using (exists (select 1 from orders o where o.id = order_id and is_store_member(o.store_id)))
  with check (exists (select 1 from orders o where o.id = order_id and is_store_member(o.store_id)));


-- ---------------------------------------------------------------------------
-- 2. New store settings
-- ---------------------------------------------------------------------------

alter table stores
  add column staff_chat_id bigint,                        -- Telegram group ids are large negative numbers
  add column webhook_secret text,
  add column is_active boolean not null default true;


-- ---------------------------------------------------------------------------
-- 3. Hide secrets from the staff dashboard
-- ---------------------------------------------------------------------------
-- The dashboard uses the anon key (roles `anon` and `authenticated`).
-- Column privileges: logged-in staff can read only the safe columns of
-- `stores`; telegram_bot_token and webhook_secret are readable only by the
-- backend (service_role). New columns added later are hidden by default
-- until they're granted here.
-- Note: the dashboard must select named columns; `select *` on stores will
-- be refused.

revoke all on table stores from anon, authenticated;
grant select (id, name, plan, created_at, staff_chat_id, is_active)
  on table stores to authenticated;


-- ---------------------------------------------------------------------------
-- 4. Product variant fixes
-- ---------------------------------------------------------------------------
-- store_id is always copied from the product: on insert, when the variant
-- moves to another product, and if someone tries to set store_id directly.

create or replace function set_variant_store_id()
returns trigger
language plpgsql
as $$
begin
  select store_id into new.store_id from public.products where id = new.product_id;
  if new.store_id is null then
    raise exception 'product % not found', new.product_id;
  end if;
  return new;
end;
$$;

drop trigger if exists trg_set_variant_store_id on product_variants;
create trigger trg_set_variant_store_id
before insert or update of product_id, store_id on product_variants
for each row execute function set_variant_store_id();

-- Repair any existing rows, then make the column required.
update product_variants v
set store_id = p.store_id
from products p
where p.id = v.product_id and v.store_id is distinct from p.store_id;

alter table product_variants alter column store_id set not null;

-- In 001 this foreign key had no "on delete cascade", so a store with
-- variants could never be deleted. Recreate it to match the other tables.
alter table product_variants
  drop constraint product_variants_store_id_fkey,
  add constraint product_variants_store_id_fkey
    foreign key (store_id) references stores(id) on delete cascade;

update product_variants set stock_quantity = 0 where stock_quantity is null;
alter table product_variants
  alter column stock_quantity set not null,
  add constraint stock_not_negative check (stock_quantity >= 0);


-- ---------------------------------------------------------------------------
-- 5. Allowed values
-- ---------------------------------------------------------------------------

alter table orders
  alter column status set not null,
  alter column payment_status set not null,
  add constraint orders_status_check
    check (status in ('pending', 'confirmed', 'out_for_delivery', 'delivered', 'cancelled')),
  add constraint orders_payment_status_check
    check (payment_status in ('unpaid', 'paid', 'refunded'));

alter table store_staff
  alter column role set not null,
  add constraint store_staff_role_check check (role in ('owner', 'staff'));

alter table order_items
  add constraint order_items_quantity_positive check (quantity > 0),
  add constraint order_items_price_not_negative check (price >= 0);

alter table payments
  add constraint payments_amount_positive check (amount > 0);


-- ---------------------------------------------------------------------------
-- 6. Missing indexes
-- ---------------------------------------------------------------------------

create index if not exists idx_variants_store on product_variants(store_id);
create index if not exists idx_orders_customer on orders(customer_id);
create index if not exists idx_payments_order on payments(order_id);


-- ---------------------------------------------------------------------------
-- 7. Order details: fulfilment, contact snapshot, currency, idempotency
-- ---------------------------------------------------------------------------
-- Contact details are copied onto the order, so a customer changing their
-- profile later doesn't change old orders.
-- idempotency_key lets the backend safely retry "place this order" without
-- creating a second order.

-- fulfillment_method is empty on orders created before this migration;
-- place_order always sets it.
alter table orders
  add column fulfillment_method text
    check (fulfillment_method in ('delivery', 'pickup')),
  add column contact_name text,
  add column contact_phone text,
  add column delivery_address text,
  add column currency text not null default 'ETB',
  add column idempotency_key text,
  add constraint orders_idempotency_unique unique (store_id, idempotency_key),
  add constraint orders_delivery_needs_address
    check (fulfillment_method is distinct from 'delivery' or delivery_address is not null);


-- ---------------------------------------------------------------------------
-- 8. place_order: create an order in one step
-- ---------------------------------------------------------------------------
-- p_items is a JSON array: [{"variant_id": "...", "quantity": 2}, ...]
-- Prices always come from the database: price_override, else base_price.
-- Stock is checked but NOT reduced (D3: that happens in confirm_payment).
-- Returns the order id. Calling it again with the same idempotency key
-- returns the same order instead of creating a new one.
--
-- Errors (the message is a short code the backend can read):
--   store_not_found, customer_not_found, empty_order, invalid_quantity,
--   variant_not_found, price_missing, out_of_stock, invalid_fulfillment,
--   address_required

create or replace function public.place_order(
  p_store_id uuid,
  p_customer_id uuid,
  p_items jsonb,
  p_fulfillment_method text,
  p_contact_name text,
  p_contact_phone text,
  p_delivery_address text,
  p_idempotency_key text
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

  if p_fulfillment_method is null or p_fulfillment_method not in ('delivery', 'pickup') then
    raise exception 'invalid_fulfillment';
  end if;

  if p_fulfillment_method = 'delivery' and coalesce(trim(p_delivery_address), '') = '' then
    raise exception 'address_required';
  end if;

  begin
    insert into orders (
      store_id, customer_id, status, payment_status, fulfillment_method,
      contact_name, contact_phone, delivery_address, currency, idempotency_key
    )
    values (
      p_store_id, p_customer_id, 'pending', 'unpaid', p_fulfillment_method,
      p_contact_name, p_contact_phone, p_delivery_address, 'ETB', p_idempotency_key
    )
    returning id into v_order_id;
  exception when unique_violation then
    -- Another call with the same key finished first.
    select id into v_order_id from orders
    where store_id = p_store_id and idempotency_key = p_idempotency_key;
    return v_order_id;
  end;

  for v_item in
    select * from jsonb_to_recordset(p_items) as x(variant_id uuid, quantity int)
  loop
    if v_item.quantity is null or v_item.quantity < 1 then
      raise exception 'invalid_quantity';
    end if;

    select v.stock_quantity, coalesce(v.price_override, p.base_price)
    into v_stock, v_price
    from product_variants v
    join products p on p.id = v.product_id
    where v.id = v_item.variant_id and v.store_id = p_store_id;

    if not found then
      raise exception 'variant_not_found' using detail = coalesce(v_item.variant_id::text, 'null');
    end if;
    if v_price is null then
      raise exception 'price_missing' using detail = v_item.variant_id::text;
    end if;
    if v_stock < v_item.quantity then
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
-- 9. adjust_stock: change stock safely
-- ---------------------------------------------------------------------------
-- One UPDATE, so two simultaneous calls can't both use the same stock.
-- Returns the new stock quantity.
-- Errors: variant_not_found, insufficient_stock

create or replace function public.adjust_stock(
  p_store_id uuid,
  p_variant_id uuid,
  p_delta int
)
returns int
language plpgsql
set search_path = public
as $$
declare
  v_new int;
begin
  update product_variants
  set stock_quantity = stock_quantity + p_delta
  where id = p_variant_id
    and store_id = p_store_id
    and stock_quantity + p_delta >= 0
  returning stock_quantity into v_new;

  if not found then
    if exists (select 1 from product_variants where id = p_variant_id and store_id = p_store_id) then
      raise exception 'insufficient_stock' using detail = p_variant_id::text;
    end if;
    raise exception 'variant_not_found' using detail = coalesce(p_variant_id::text, 'null');
  end if;

  return v_new;
end;
$$;


-- ---------------------------------------------------------------------------
-- 10. confirm_payment: staff confirm a payment (D3)
-- ---------------------------------------------------------------------------
-- In one step: reduce stock for every item, save the payment, mark the
-- order paid (and confirmed if it was pending). If any item doesn't have
-- enough stock, nothing changes and the error says which variant is short.
-- p_staff_id is the auth user id of the staff member (checked by the
-- backend before calling).
-- Returns the payment id.
-- Errors: order_not_found, order_cancelled, already_paid, out_of_stock

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

  -- Same variant on several lines is added up. Variants are handled in a
  -- fixed order so two confirmations can't block each other.
  for v_line in
    select variant_id, sum(quantity)::int as quantity
    from order_items
    where order_id = p_order_id
    group by variant_id
    order by variant_id
  loop
    update product_variants
    set stock_quantity = stock_quantity - v_line.quantity
    where id = v_line.variant_id
      and store_id = p_store_id
      and stock_quantity >= v_line.quantity;

    if not found then
      raise exception 'out_of_stock' using detail = coalesce(v_line.variant_id::text, 'null');
    end if;
  end loop;

  insert into payments (order_id, amount, method, confirmed_by)
  values (p_order_id, p_amount, p_method, p_staff_id)
  returning id into v_payment_id;

  update orders
  set payment_status = 'paid',
      status = case when status = 'pending' then 'confirmed' else status end
  where id = p_order_id;

  return v_payment_id;
end;
$$;


-- ---------------------------------------------------------------------------
-- 11. Only the backend may call the order and stock functions
-- ---------------------------------------------------------------------------
-- By default anyone can call a function. These change orders and stock, so
-- only the backend (service_role) may call them. is_store_member stays
-- callable because the security rules use it.

revoke all on function public.place_order(uuid, uuid, jsonb, text, text, text, text, text) from public, anon, authenticated;
revoke all on function public.adjust_stock(uuid, uuid, int) from public, anon, authenticated;
revoke all on function public.confirm_payment(uuid, uuid, numeric, text, uuid) from public, anon, authenticated;

grant execute on function public.place_order(uuid, uuid, jsonb, text, text, text, text, text) to service_role;
grant execute on function public.adjust_stock(uuid, uuid, int) to service_role;
grant execute on function public.confirm_payment(uuid, uuid, numeric, text, uuid) to service_role;


commit;
