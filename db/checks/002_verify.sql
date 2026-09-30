-- 002_verify.sql
--
-- NOTE: only valid right after 002. Migration 005 changed place_order (it
-- now takes a hold time), so after 005 this script fails with "function
-- place_order(...) does not exist". Use the later check files instead.
--
-- Run in the Supabase SQL Editor AFTER 002_platform_updates.sql.
-- It creates a temporary test store, checks every new rule and function,
-- then deletes the test store again. Nothing is left behind.
--
-- Success: a table where every row's "ok" column is true.
-- Failure: the editor shows an error starting with "CHECK FAILED: ...".

do $$
declare
  v_store uuid;
  v_other_store uuid;
  v_product uuid;
  v_variant uuid;
  v_other_variant uuid;
  v_customer uuid;
  v_order uuid;
  v_order2 uuid;
  v_again uuid;
  v_total numeric;
  v_stock int;
  v_status text;
  v_payment_status text;
begin
  -- Test data -------------------------------------------------------------
  insert into stores (name) values ('__002_check_store__') returning id into v_store;
  insert into stores (name) values ('__002_check_other__') returning id into v_other_store;

  insert into products (store_id, name, base_price)
  values (v_store, 'Air Force 1', 4500) returning id into v_product;

  -- Variant with 1 in stock; price_override should win over base_price.
  insert into product_variants (product_id, color, size, sku, stock_quantity, price_override)
  values (v_product, 'white', '42', '__chk_af1_w42__', 1, 5000) returning id into v_variant;

  insert into products (store_id, name, base_price)
  values (v_other_store, 'Other store shoe', 100) returning id into v_other_variant;
  insert into product_variants (product_id, size, stock_quantity)
  values (v_other_variant, '40', 10) returning id into v_other_variant;

  insert into customers (store_id, telegram_id, name)
  values (v_store, 999000111, 'Test Customer') returning id into v_customer;

  -- 1. Variant store_id is filled in automatically -------------------------
  if (select store_id from product_variants where id = v_variant) <> v_store then
    raise exception 'CHECK FAILED: variant store_id was not copied from product';
  end if;

  -- 2. place_order creates an order with database prices -------------------
  v_order := place_order(
    v_store, v_customer,
    jsonb_build_array(jsonb_build_object('variant_id', v_variant, 'quantity', 1)),
    'delivery', 'Test Customer', '0911000000', 'Bole, Addis Ababa', '__chk_key_1__'
  );
  select total_price, status, payment_status into v_total, v_status, v_payment_status
  from orders where id = v_order;
  if v_total <> 5000 then
    raise exception 'CHECK FAILED: total should be 5000 (price_override), got %', v_total;
  end if;
  if v_status <> 'pending' or v_payment_status <> 'unpaid' then
    raise exception 'CHECK FAILED: new order should be pending/unpaid, got %/%', v_status, v_payment_status;
  end if;

  -- 3. Same idempotency key returns the same order ------------------------
  v_again := place_order(
    v_store, v_customer,
    jsonb_build_array(jsonb_build_object('variant_id', v_variant, 'quantity', 1)),
    'delivery', 'Test Customer', '0911000000', 'Bole, Addis Ababa', '__chk_key_1__'
  );
  if v_again <> v_order or (select count(*) from orders where store_id = v_store) <> 1 then
    raise exception 'CHECK FAILED: same idempotency key created a second order';
  end if;

  -- 4. Stock is NOT reduced when the order is placed (D3) -----------------
  if (select stock_quantity from product_variants where id = v_variant) <> 1 then
    raise exception 'CHECK FAILED: stock changed when the order was placed';
  end if;

  -- 5. place_order refusals -----------------------------------------------
  begin
    perform place_order(v_store, v_customer,
      jsonb_build_array(jsonb_build_object('variant_id', v_variant, 'quantity', 5)),
      'pickup', null, '0911000000', null, null);
    raise exception 'CHECK FAILED: ordering 5 of 1 in stock was allowed';
  exception when others then
    if sqlerrm <> 'out_of_stock' then raise; end if;
  end;

  begin
    perform place_order(v_store, v_customer,
      jsonb_build_array(jsonb_build_object('variant_id', v_other_variant, 'quantity', 1)),
      'pickup', null, '0911000000', null, null);
    raise exception 'CHECK FAILED: ordered another store''s product';
  exception when others then
    if sqlerrm <> 'variant_not_found' then raise; end if;
  end;

  begin
    perform place_order(v_store, v_customer,
      jsonb_build_array(jsonb_build_object('variant_id', v_variant, 'quantity', 1)),
      'delivery', null, '0911000000', null, null);
    raise exception 'CHECK FAILED: delivery order without address was allowed';
  exception when others then
    if sqlerrm <> 'address_required' then raise; end if;
  end;

  begin
    perform place_order(v_store, v_customer, '[]'::jsonb, 'pickup', null, null, null, null);
    raise exception 'CHECK FAILED: empty order was allowed';
  exception when others then
    if sqlerrm <> 'empty_order' then raise; end if;
  end;

  -- 6. adjust_stock never goes below zero ---------------------------------
  begin
    perform adjust_stock(v_store, v_variant, -5);
    raise exception 'CHECK FAILED: stock went below zero';
  exception when others then
    if sqlerrm <> 'insufficient_stock' then raise; end if;
  end;

  begin
    perform adjust_stock(v_store, v_other_variant, 1);
    raise exception 'CHECK FAILED: changed another store''s stock';
  exception when others then
    if sqlerrm <> 'variant_not_found' then raise; end if;
  end;

  v_stock := adjust_stock(v_store, v_variant, 2);   -- 1 -> 3
  v_stock := adjust_stock(v_store, v_variant, -2);  -- 3 -> 1
  if v_stock <> 1 then
    raise exception 'CHECK FAILED: adjust_stock returned %, expected 1', v_stock;
  end if;

  -- 7. A second customer order for the same last pair (allowed: stock is
  --    only reduced at payment) --------------------------------------------
  v_order2 := place_order(v_store, v_customer,
    jsonb_build_array(jsonb_build_object('variant_id', v_variant, 'quantity', 1)),
    'pickup', 'Test Customer', '0911000000', null, '__chk_key_2__');

  -- 8. confirm_payment reduces stock and marks the order paid -------------
  perform confirm_payment(v_store, v_order, 5000, 'Telebirr', null);
  select status, payment_status into v_status, v_payment_status from orders where id = v_order;
  if v_status <> 'confirmed' or v_payment_status <> 'paid' then
    raise exception 'CHECK FAILED: paid order should be confirmed/paid, got %/%', v_status, v_payment_status;
  end if;
  if (select stock_quantity from product_variants where id = v_variant) <> 0 then
    raise exception 'CHECK FAILED: stock was not reduced at payment';
  end if;

  begin
    perform confirm_payment(v_store, v_order, 5000, 'Telebirr', null);
    raise exception 'CHECK FAILED: the same order was paid twice';
  exception when others then
    if sqlerrm <> 'already_paid' then raise; end if;
  end;

  -- 9. Sold out before payment: refused, nothing changes -------------------
  begin
    perform confirm_payment(v_store, v_order2, 5000, 'Bank transfer', null);
    raise exception 'CHECK FAILED: payment confirmed for a sold-out item';
  exception when others then
    if sqlerrm <> 'out_of_stock' then raise; end if;
  end;
  if (select payment_status from orders where id = v_order2) <> 'unpaid'
     or exists (select 1 from payments where order_id = v_order2) then
    raise exception 'CHECK FAILED: refused payment still changed the order';
  end if;

  -- 10. Another store can't confirm this store's order ---------------------
  begin
    perform confirm_payment(v_other_store, v_order2, 5000, 'Telebirr', null);
    raise exception 'CHECK FAILED: another store confirmed this order';
  exception when others then
    if sqlerrm <> 'order_not_found' then raise; end if;
  end;

  -- 11. Allowed values are enforced ----------------------------------------
  begin
    update orders set status = 'shipped' where id = v_order;
    raise exception 'CHECK FAILED: invalid order status was accepted';
  exception when check_violation then null;
  end;

  -- Clean up ---------------------------------------------------------------
  delete from stores where id in (v_store, v_other_store);
end;
$$;

-- Structure checks: every row should say true.
select 'stores.staff_chat_id exists' as check, exists (
  select 1 from information_schema.columns
  where table_name = 'stores' and column_name = 'staff_chat_id') as ok
union all
select 'stores.webhook_secret exists', exists (
  select 1 from information_schema.columns
  where table_name = 'stores' and column_name = 'webhook_secret')
union all
select 'stores.is_active exists', exists (
  select 1 from information_schema.columns
  where table_name = 'stores' and column_name = 'is_active')
union all
select 'staff cannot read telegram_bot_token',
  not has_column_privilege('authenticated', 'public.stores', 'telegram_bot_token', 'select')
union all
select 'staff cannot read webhook_secret',
  not has_column_privilege('authenticated', 'public.stores', 'webhook_secret', 'select')
union all
select 'staff cannot call place_order',
  not has_function_privilege('authenticated',
    'public.place_order(uuid, uuid, jsonb, text, text, text, text, text)', 'execute')
union all
select 'staff cannot call confirm_payment',
  not has_function_privilege('authenticated',
    'public.confirm_payment(uuid, uuid, numeric, text, uuid)', 'execute')
union all
select 'all 002 function checks passed', true;
