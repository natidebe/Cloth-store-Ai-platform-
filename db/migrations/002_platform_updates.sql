-- 002_platform_updates.sql
--
-- Gets the database into its final shape before the backend depends on it.
-- Run once in the Supabase SQL Editor, after 001. Then run
-- db/checks/002_platform_updates_check.sql to confirm it worked.
--
-- Decisions this relies on (see BUILD_PLAN.md, section 6):
--   D3  stock goes down when staff confirm payment, not when the order is placed
--   D4  delivery or pickup per order; address and phone saved on each order
--   D5  ETB only, no currency column
--   D6  payment methods: telebirr, bank_transfer, cash_on_delivery, cash_in_store
--   D7  order stages: pending -> confirmed -> ready_for_pickup / out_for_delivery
--       -> completed, or cancelled
--
-- The whole file runs in one transaction: if any part fails, nothing changes.

begin;


-- ---------------------------------------------------------------------------
-- 1. Fix the security-rule loop
--
-- The 001 rules all look up store_staff, and store_staff's own rule looks up
-- store_staff too, so Postgres fails with "infinite recursion detected in
-- policy". This helper runs the lookup with the rules switched off
-- (security definer), and every rule calls it instead.
-- ---------------------------------------------------------------------------

create or replace function public.is_store_staff(p_store_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1 from public.store_staff
    where store_id = p_store_id and user_id = auth.uid()
  );
$$;

revoke all on function public.is_store_staff(uuid) from public;
grant execute on function public.is_store_staff(uuid) to anon, authenticated, service_role;

drop policy "staff sees own stores" on stores;
create policy "staff sees own stores" on stores
  for select using (public.is_store_staff(id));

drop policy "staff sees own store_staff rows" on store_staff;
create policy "staff sees own store_staff rows" on store_staff
  for select using (public.is_store_staff(store_id));

drop policy "staff manages own products" on products;
create policy "staff manages own products" on products
  for all using (public.is_store_staff(store_id));

drop policy "staff manages own variants" on product_variants;
create policy "staff manages own variants" on product_variants
  for all using (public.is_store_staff(store_id));

drop policy "staff manages own customers" on customers;
create policy "staff manages own customers" on customers
  for all using (public.is_store_staff(store_id));

drop policy "staff manages own orders" on orders;
create policy "staff manages own orders" on orders
  for all using (public.is_store_staff(store_id));

drop policy "staff manages own order_items" on order_items;
create policy "staff manages own order_items" on order_items
  for all using (
    order_id in (select id from orders where public.is_store_staff(store_id))
  );

drop policy "staff manages own payments" on payments;
create policy "staff manages own payments" on payments
  for all using (
    order_id in (select id from orders where public.is_store_staff(store_id))
  );


-- ---------------------------------------------------------------------------
-- 2. New store settings
-- ---------------------------------------------------------------------------

alter table stores
  -- The staff's Telegram group, where escalations are sent.
  add column staff_chat_id bigint,
  -- Sent to Telegram's setWebhook; Telegram echoes it back on every request
  -- so we know the request is genuine. 64 random hex characters, created
  -- automatically for every store (existing ones included).
  add column webhook_secret text not null
    default replace(gen_random_uuid()::text || gen_random_uuid()::text, '-', ''),
  -- Turn a store's bot off without deleting anything.
  add column is_active boolean not null default true;


-- ---------------------------------------------------------------------------
-- 3. Hide secrets from the staff dashboard
--
-- The dashboard logs in as the `authenticated` role. It can read every
-- stores column except the bot token and webhook secret. `anon` (not logged
-- in) can't read stores at all. The backend uses service_role and is not
-- affected.
--
-- Note for the dashboard: `select *` on stores now fails with "permission
-- denied"; list the columns, or read from stores_public.
-- ---------------------------------------------------------------------------

revoke select on stores from anon, authenticated;
grant select (id, name, plan, staff_chat_id, is_active, created_at)
  on stores to authenticated;

create view stores_public
with (security_invoker = true)  -- the view still applies the stores rules
as
  select id, name, plan, staff_chat_id, is_active, created_at from stores;

revoke all on stores_public from anon;
grant select on stores_public to authenticated;


-- ---------------------------------------------------------------------------
-- 4. Product variant fixes
--
-- product_variants.store_id is copied from the product, but 001 only did it
-- on insert. Now it's also updated when a variant moves to another product,
-- and when a product moves to another store. It can no longer be empty.
-- ---------------------------------------------------------------------------

create or replace function set_variant_store_id()
returns trigger
language plpgsql
set search_path = public
as $$
begin
  select store_id into new.store_id from products where id = new.product_id;
  if new.store_id is null then
    raise exception 'product % not found', new.product_id;
  end if;
  return new;
end;
$$;

drop trigger trg_set_variant_store_id on product_variants;
create trigger trg_set_variant_store_id
before insert or update of product_id on product_variants
for each row execute function set_variant_store_id();

create or replace function sync_variant_store_id()
returns trigger
language plpgsql
set search_path = public
as $$
begin
  update product_variants set store_id = new.store_id where product_id = new.id;
  return null;
end;
$$;

create trigger trg_sync_variant_store_id
after update of store_id on products
for each row
when (old.store_id is distinct from new.store_id)
execute function sync_variant_store_id();

-- Repair any rows that are already out of step, then require the column.
update product_variants v
set store_id = p.store_id
from products p
where p.id = v.product_id and v.store_id is distinct from p.store_id;

alter table product_variants alter column store_id set not null;


-- ---------------------------------------------------------------------------
-- 5. Allowed values
--
-- Check constraints rather than enum types: adding or removing a value later
-- is just dropping and re-adding the constraint in a new migration.
-- ---------------------------------------------------------------------------

alter table orders alter column status set not null;
alter table orders add constraint orders_status_check check (
  status in ('pending', 'confirmed', 'ready_for_pickup', 'out_for_delivery',
             'completed', 'cancelled')
);

alter table orders alter column payment_status set not null;
alter table orders add constraint orders_payment_status_check check (
  payment_status in ('unpaid', 'pending_verification', 'paid', 'refunded')
);

alter table payments alter column method set not null;
alter table payments add constraint payments_method_check check (
  method in ('telebirr', 'bank_transfer', 'cash_on_delivery', 'cash_in_store')
);
alter table payments add constraint payments_amount_positive check (amount > 0);

alter table store_staff alter column role set not null;
alter table store_staff add constraint store_staff_role_check check (
  role in ('owner', 'staff')
);

alter table product_variants alter column stock_quantity set not null;
alter table product_variants add constraint product_variants_stock_not_negative
  check (stock_quantity >= 0);

alter table order_items add constraint order_items_quantity_positive
  check (quantity > 0);


-- ---------------------------------------------------------------------------
-- 6. Missing indexes
--
-- Customer by (store_id, telegram_id) and variants by store_id are already
-- covered by the unique constraints from 001.
-- ---------------------------------------------------------------------------

create index idx_order_items_variant on order_items(variant_id);
create index idx_payments_order on payments(order_id);
create index idx_orders_customer on orders(customer_id);
create index idx_store_staff_user on store_staff(user_id);


-- ---------------------------------------------------------------------------
-- 7. Order delivery details
--
-- Saved on the order itself, so later changes to the customer's profile
-- don't rewrite old orders. A delivery order must have an address.
-- ---------------------------------------------------------------------------

alter table orders
  -- The temporary default only fills in existing rows; new orders must say.
  add column fulfillment text not null default 'pickup'
    check (fulfillment in ('delivery', 'pickup')),
  add column phone text,
  add column delivery_address text;

alter table orders alter column fulfillment drop default;

alter table orders add constraint orders_delivery_needs_address check (
  fulfillment <> 'delivery' or nullif(btrim(delivery_address), '') is not null
);


-- ---------------------------------------------------------------------------
-- Functions 8 and 9 are for the backend only. They fail with a short
-- machine-readable message (e.g. 'out_of_stock') and a human-readable
-- DETAIL, and if they fail nothing they did is saved.
-- ---------------------------------------------------------------------------


-- ---------------------------------------------------------------------------
-- 8. place_order
--
-- p_items is a JSON array: [{"variant_id": "<uuid>", "quantity": 2}, ...]
-- Prices come from the database (price_override, otherwise base_price),
-- never from the caller. Stock is checked but not reduced (D3): that
-- happens in confirm_payment.
--
-- Errors: store_not_found, customer_not_found, invalid_fulfillment,
-- phone_required, address_required, no_items, invalid_item,
-- variant_not_found, price_missing, out_of_stock
-- ---------------------------------------------------------------------------

create or replace function public.place_order(
  p_store_id uuid,
  p_customer_id uuid,
  p_items jsonb,
  p_fulfillment text,
  p_phone text,
  p_delivery_address text default null
)
returns uuid
language plpgsql
set search_path = public
as $$
declare
  v_order_id uuid;
  v_line record;
  v_price numeric;
  v_stock int;
  v_total numeric := 0;
begin
  if not exists (select 1 from stores where id = p_store_id and is_active) then
    raise exception 'store_not_found'
      using detail = format('store %s does not exist or is not active', p_store_id);
  end if;

  if not exists (select 1 from customers where id = p_customer_id and store_id = p_store_id) then
    raise exception 'customer_not_found'
      using detail = format('customer %s does not belong to store %s', p_customer_id, p_store_id);
  end if;

  if p_fulfillment is null or p_fulfillment not in ('delivery', 'pickup') then
    raise exception 'invalid_fulfillment'
      using detail = format('fulfillment must be delivery or pickup, got %s', p_fulfillment);
  end if;

  if nullif(btrim(p_phone), '') is null then
    raise exception 'phone_required' using detail = 'a phone number is required';
  end if;

  if p_fulfillment = 'delivery' and nullif(btrim(p_delivery_address), '') is null then
    raise exception 'address_required' using detail = 'delivery orders need an address';
  end if;

  if p_items is null or jsonb_typeof(p_items) <> 'array' or jsonb_array_length(p_items) = 0 then
    raise exception 'no_items' using detail = 'the order has no items';
  end if;

  -- CASE so the casts only run on values already known to be the right
  -- shape (OR doesn't guarantee evaluation order).
  if exists (
    select 1 from jsonb_array_elements(p_items) e
    where case
      when jsonb_typeof(e) <> 'object' then true
      when jsonb_typeof(e->'variant_id') is distinct from 'string'
        or e->>'variant_id' !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        then true
      when jsonb_typeof(e->'quantity') is distinct from 'number' then true
      else (e->>'quantity')::numeric < 1
        or (e->>'quantity')::numeric <> trunc((e->>'quantity')::numeric)
    end
  ) then
    raise exception 'invalid_item'
      using detail = 'every item needs a variant_id and a whole-number quantity of at least 1';
  end if;

  insert into orders (store_id, customer_id, fulfillment, phone, delivery_address)
  values (
    p_store_id,
    p_customer_id,
    p_fulfillment,
    btrim(p_phone),
    case when p_fulfillment = 'delivery' then btrim(p_delivery_address) end
  )
  returning id into v_order_id;

  -- The same variant listed twice is merged into one line.
  for v_line in
    select (e->>'variant_id')::uuid as variant_id, sum((e->>'quantity')::int) as quantity
    from jsonb_array_elements(p_items) e
    group by 1
  loop
    select coalesce(v.price_override, p.base_price), v.stock_quantity
    into v_price, v_stock
    from product_variants v
    join products p on p.id = v.product_id
    where v.id = v_line.variant_id and v.store_id = p_store_id;

    if not found then
      raise exception 'variant_not_found'
        using detail = format('variant %s does not belong to store %s', v_line.variant_id, p_store_id);
    end if;

    if v_price is null then
      raise exception 'price_missing'
        using detail = format('variant %s has no price set', v_line.variant_id);
    end if;

    if v_stock < v_line.quantity then
      raise exception 'out_of_stock'
        using detail = format('variant %s: asked for %s, %s in stock',
                              v_line.variant_id, v_line.quantity, v_stock);
    end if;

    insert into order_items (order_id, variant_id, quantity, price)
    values (v_order_id, v_line.variant_id, v_line.quantity, v_price);

    v_total := v_total + v_price * v_line.quantity;
  end loop;

  update orders set total_price = v_total where id = v_order_id;

  return v_order_id;
end;
$$;


-- ---------------------------------------------------------------------------
-- 9. Stock functions
-- ---------------------------------------------------------------------------

-- adjust_stock: add (positive p_delta) or remove (negative) stock. Never lets
-- stock go below zero. Returns the new stock level.
--
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
  v_stock int;
begin
  select stock_quantity into v_stock
  from product_variants
  where id = p_variant_id and store_id = p_store_id
  for update;

  if not found then
    raise exception 'variant_not_found'
      using detail = format('variant %s does not belong to store %s', p_variant_id, p_store_id);
  end if;

  if v_stock + p_delta < 0 then
    raise exception 'insufficient_stock'
      using detail = format('variant %s: %s in stock, cannot remove %s', p_variant_id, v_stock, -p_delta);
  end if;

  update product_variants
  set stock_quantity = stock_quantity + p_delta
  where id = p_variant_id
  returning stock_quantity into v_stock;

  return v_stock;
end;
$$;


-- confirm_payment: staff confirmed the money arrived. In one step it records
-- the payment, reduces stock for every item (D3), marks the order paid, and
-- moves a pending order to confirmed. If any item is out of stock by now,
-- nothing is saved and the error says which one.
--
-- p_confirmed_by is the staff member's dashboard user id, or null when
-- confirmed another way (e.g. from the staff Telegram group).
--
-- Errors: order_not_found, order_cancelled, already_paid, order_refunded,
-- amount_too_low, variant_not_found, out_of_stock
-- (an unknown p_method fails the payments_method_check constraint)

create or replace function public.confirm_payment(
  p_store_id uuid,
  p_order_id uuid,
  p_amount numeric,
  p_method text,
  p_confirmed_by uuid default null
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
  -- Lock the order so two confirmations can't run at the same time.
  select * into v_order
  from orders
  where id = p_order_id and store_id = p_store_id
  for update;

  if not found then
    raise exception 'order_not_found'
      using detail = format('order %s does not belong to store %s', p_order_id, p_store_id);
  end if;

  if v_order.status = 'cancelled' then
    raise exception 'order_cancelled' using detail = format('order %s is cancelled', p_order_id);
  end if;
  if v_order.payment_status = 'paid' then
    raise exception 'already_paid' using detail = format('order %s is already paid', p_order_id);
  end if;
  if v_order.payment_status = 'refunded' then
    raise exception 'order_refunded' using detail = format('order %s was refunded', p_order_id);
  end if;

  if p_amount is null or p_amount < v_order.total_price then
    raise exception 'amount_too_low'
      using detail = format('order %s total is %s, payment is %s', p_order_id, v_order.total_price, p_amount);
  end if;

  -- Variants are updated in a fixed order so two confirmations touching the
  -- same variants can't deadlock.
  for v_line in
    select variant_id, sum(quantity) as quantity
    from order_items
    where order_id = p_order_id
    group by variant_id
    order by variant_id
  loop
    if v_line.variant_id is null then
      raise exception 'variant_not_found'
        using detail = format('order %s has an item with no variant', p_order_id);
    end if;

    update product_variants
    set stock_quantity = stock_quantity - v_line.quantity
    where id = v_line.variant_id
      and store_id = p_store_id
      and stock_quantity >= v_line.quantity;

    if not found then
      raise exception 'out_of_stock'
        using detail = format('variant %s: not enough stock left for %s', v_line.variant_id, v_line.quantity);
    end if;
  end loop;

  insert into payments (order_id, amount, method, confirmed_by)
  values (p_order_id, p_amount, p_method, p_confirmed_by)
  returning id into v_payment_id;

  update orders
  set payment_status = 'paid',
      status = case when status = 'pending' then 'confirmed' else status end
  where id = p_order_id;

  return v_payment_id;
end;
$$;


-- Only the backend (service_role) may call these. The dashboard can't, since
-- they take store_id as a plain argument.
revoke all on function public.place_order(uuid, uuid, jsonb, text, text, text) from public, anon, authenticated;
revoke all on function public.adjust_stock(uuid, uuid, int) from public, anon, authenticated;
revoke all on function public.confirm_payment(uuid, uuid, numeric, text, uuid) from public, anon, authenticated;
grant execute on function public.place_order(uuid, uuid, jsonb, text, text, text) to service_role;
grant execute on function public.adjust_stock(uuid, uuid, int) to service_role;
grant execute on function public.confirm_payment(uuid, uuid, numeric, text, uuid) to service_role;


commit;
