-- Checks for 002_platform_updates.sql.
--
-- Run in the Supabase SQL Editor after the migration. It creates test data,
-- tries each rule, then rolls everything back, so nothing is left behind.
--
-- Passed:  the result is one row saying "002 checks passed".
-- Failed:  an error naming the check that failed.

begin;

do $$
declare
  v_store uuid;
  v_other_store uuid;
  v_product uuid;
  v_other_product uuid;
  v_variant uuid;
  v_other_variant uuid;
  v_customer uuid;
  v_order uuid;
  v_order2 uuid;
  v_row orders%rowtype;
begin
  -- Test data ---------------------------------------------------------------

  insert into stores (name, telegram_bot_token) values ('Check Store', '000:test')
    returning id into v_store;
  insert into stores (name) values ('Other Store') returning id into v_other_store;

  insert into products (store_id, name, base_price) values (v_store, 'Air Max', 4500)
    returning id into v_product;
  insert into products (store_id, name, base_price) values (v_other_store, 'Other Shoe', 100)
    returning id into v_other_product;

  insert into product_variants (product_id, color, size, sku, stock_quantity)
    values (v_product, 'black', '42', 'AM-42', 1) returning id into v_variant;
  insert into product_variants (product_id, sku, stock_quantity)
    values (v_other_product, 'OS-1', 5) returning id into v_other_variant;

  insert into customers (store_id, telegram_id, name) values (v_store, 555, 'Abebe')
    returning id into v_customer;

  -- 2. Store settings -------------------------------------------------------

  assert (select length(webhook_secret) from stores where id = v_store) = 64,
    'webhook_secret is generated';
  assert (select webhook_secret from stores where id = v_store)
      <> (select webhook_secret from stores where id = v_other_store),
    'each store gets its own webhook_secret';
  assert (select is_active from stores where id = v_store), 'is_active defaults to true';

  -- 4. Variant store_id -----------------------------------------------------

  assert (select store_id from product_variants where id = v_variant) = v_store,
    'variant store_id is set on insert';

  -- 5. Allowed values -------------------------------------------------------

  begin
    insert into orders (store_id, fulfillment, status) values (v_store, 'pickup', 'shipped');
    raise exception 'CHECK FAILED: order status "shipped" was accepted';
  exception when check_violation then null;
  end;

  begin
    insert into orders (store_id, fulfillment, payment_status) values (v_store, 'pickup', 'partial');
    raise exception 'CHECK FAILED: payment status "partial" was accepted';
  exception when check_violation then null;
  end;

  begin
    insert into store_staff (store_id, user_id, role) values (v_store, gen_random_uuid(), 'admin');
    raise exception 'CHECK FAILED: staff role "admin" was accepted';
  exception when check_violation then null;  -- runs before the user_id foreign key
  end;

  begin
    update product_variants set stock_quantity = -1 where id = v_variant;
    raise exception 'CHECK FAILED: negative stock was accepted';
  exception when check_violation then null;
  end;

  -- 7. Delivery details -----------------------------------------------------

  begin
    insert into orders (store_id, fulfillment) values (v_store, 'delivery');
    raise exception 'CHECK FAILED: delivery order without address was accepted';
  exception when check_violation then null;
  end;

  begin
    insert into orders (store_id) values (v_store);
    raise exception 'CHECK FAILED: order without fulfillment was accepted';
  exception when not_null_violation then null;
  end;

  -- 8. place_order ----------------------------------------------------------

  v_order := place_order(
    v_store, v_customer,
    jsonb_build_array(jsonb_build_object('variant_id', v_variant, 'quantity', 1)),
    'delivery', ' 0911000000 ', 'Bole, Addis Ababa'
  );
  select * into v_row from orders where id = v_order;
  assert v_row.total_price = 4500, 'total comes from base_price';
  assert v_row.status = 'pending' and v_row.payment_status = 'unpaid', 'new order is pending and unpaid';
  assert v_row.phone = '0911000000' and v_row.delivery_address = 'Bole, Addis Ababa', 'phone and address saved';
  assert (select count(*) from order_items where order_id = v_order) = 1, 'order item created';
  assert (select stock_quantity from product_variants where id = v_variant) = 1,
    'placing an order does not reduce stock (D3)';

  -- price_override wins over base_price; same variant twice is merged
  update product_variants set price_override = 4200, stock_quantity = 2 where id = v_variant;
  v_order2 := place_order(
    v_store, v_customer,
    jsonb_build_array(jsonb_build_object('variant_id', v_variant, 'quantity', 1),
                      jsonb_build_object('variant_id', v_variant, 'quantity', 1)),
    'pickup', '0911000000', 'ignored for pickup'
  );
  select * into v_row from orders where id = v_order2;
  assert v_row.total_price = 8400, 'total uses price_override';
  assert v_row.delivery_address is null, 'pickup orders store no address';
  assert (select quantity from order_items where order_id = v_order2) = 2, 'duplicate lines merged';
  update product_variants set stock_quantity = 1 where id = v_variant;

  begin
    perform place_order(v_store, v_customer,
      jsonb_build_array(jsonb_build_object('variant_id', v_other_variant, 'quantity', 1)),
      'pickup', '0911000000');
    raise exception 'CHECK FAILED: another store''s variant was accepted';
  exception when others then
    assert sqlerrm = 'variant_not_found', 'other store''s variant: expected variant_not_found, got ' || sqlerrm;
  end;

  begin
    perform place_order(v_store, v_customer,
      jsonb_build_array(jsonb_build_object('variant_id', v_variant, 'quantity', 5)),
      'pickup', '0911000000');
    raise exception 'CHECK FAILED: more than the stock was accepted';
  exception when others then
    assert sqlerrm = 'out_of_stock', 'too many: expected out_of_stock, got ' || sqlerrm;
  end;

  begin
    perform place_order(v_other_store, v_customer,
      jsonb_build_array(jsonb_build_object('variant_id', v_other_variant, 'quantity', 1)),
      'pickup', '0911000000');
    raise exception 'CHECK FAILED: another store''s customer was accepted';
  exception when others then
    assert sqlerrm = 'customer_not_found', 'other store''s customer: expected customer_not_found, got ' || sqlerrm;
  end;

  begin
    perform place_order(v_store, v_customer,
      jsonb_build_array(jsonb_build_object('variant_id', v_variant, 'quantity', 1)),
      'delivery', '0911000000', '  ');
    raise exception 'CHECK FAILED: delivery without address was accepted';
  exception when others then
    assert sqlerrm = 'address_required', 'no address: expected address_required, got ' || sqlerrm;
  end;

  begin
    perform place_order(v_store, v_customer,
      jsonb_build_array(jsonb_build_object('variant_id', v_variant, 'quantity', 1)),
      'pickup', '');
    raise exception 'CHECK FAILED: order without phone was accepted';
  exception when others then
    assert sqlerrm = 'phone_required', 'no phone: expected phone_required, got ' || sqlerrm;
  end;

  begin
    perform place_order(v_store, v_customer, '[]'::jsonb, 'pickup', '0911000000');
    raise exception 'CHECK FAILED: empty order was accepted';
  exception when others then
    assert sqlerrm = 'no_items', 'empty order: expected no_items, got ' || sqlerrm;
  end;

  begin
    perform place_order(v_store, v_customer,
      jsonb_build_array(jsonb_build_object('variant_id', v_variant, 'quantity', 'two')),
      'pickup', '0911000000');
    raise exception 'CHECK FAILED: text quantity was accepted';
  exception when others then
    assert sqlerrm = 'invalid_item', 'text quantity: expected invalid_item, got ' || sqlerrm;
  end;

  begin
    perform place_order(v_store, v_customer,
      jsonb_build_array(jsonb_build_object('variant_id', v_variant, 'quantity', 0)),
      'pickup', '0911000000');
    raise exception 'CHECK FAILED: quantity 0 was accepted';
  exception when others then
    assert sqlerrm = 'invalid_item', 'quantity 0: expected invalid_item, got ' || sqlerrm;
  end;

  -- 9. confirm_payment ------------------------------------------------------

  begin
    perform confirm_payment(v_store, v_order, 1000, 'telebirr');
    raise exception 'CHECK FAILED: payment below the total was accepted';
  exception when others then
    assert sqlerrm = 'amount_too_low', 'low amount: expected amount_too_low, got ' || sqlerrm;
  end;

  begin
    perform confirm_payment(v_store, v_order, 4500, 'paypal');
    raise exception 'CHECK FAILED: payment method "paypal" was accepted';
  exception when check_violation then null;
  end;

  begin
    perform confirm_payment(v_other_store, v_order, 4500, 'telebirr');
    raise exception 'CHECK FAILED: confirmed an order from another store';
  exception when others then
    assert sqlerrm = 'order_not_found', 'other store: expected order_not_found, got ' || sqlerrm;
  end;

  perform confirm_payment(v_store, v_order, 4500, 'bank_transfer');
  select * into v_row from orders where id = v_order;
  assert v_row.payment_status = 'paid' and v_row.status = 'confirmed', 'order paid and confirmed';
  assert (select count(*) from payments where order_id = v_order) = 1, 'payment recorded';
  assert (select stock_quantity from product_variants where id = v_variant) = 0,
    'stock reduced on payment (D3)';

  begin
    perform confirm_payment(v_store, v_order, 4500, 'bank_transfer');
    raise exception 'CHECK FAILED: paid the same order twice';
  exception when others then
    assert sqlerrm = 'already_paid', 'second payment: expected already_paid, got ' || sqlerrm;
  end;

  -- The second order wanted 2 of a variant that now has 0: nothing is saved.
  begin
    perform confirm_payment(v_store, v_order2, 8400, 'cash_in_store');
    raise exception 'CHECK FAILED: confirmed payment with no stock left';
  exception when others then
    assert sqlerrm = 'out_of_stock', 'no stock at payment: expected out_of_stock, got ' || sqlerrm;
  end;
  assert (select payment_status from orders where id = v_order2) = 'unpaid', 'failed confirmation left order unpaid';
  assert (select count(*) from payments where order_id = v_order2) = 0, 'failed confirmation saved no payment';

  update orders set status = 'cancelled' where id = v_order2;
  begin
    perform confirm_payment(v_store, v_order2, 8400, 'cash_in_store');
    raise exception 'CHECK FAILED: paid a cancelled order';
  exception when others then
    assert sqlerrm = 'order_cancelled', 'cancelled: expected order_cancelled, got ' || sqlerrm;
  end;

  -- adjust_stock
  assert adjust_stock(v_store, v_variant, 3) = 3, 'adjust_stock adds stock';
  assert adjust_stock(v_store, v_variant, -1) = 2, 'adjust_stock removes stock';

  begin
    perform adjust_stock(v_store, v_variant, -5);
    raise exception 'CHECK FAILED: adjust_stock went below zero';
  exception when others then
    assert sqlerrm = 'insufficient_stock', 'below zero: expected insufficient_stock, got ' || sqlerrm;
  end;

  begin
    perform adjust_stock(v_other_store, v_variant, 1);
    raise exception 'CHECK FAILED: adjust_stock changed another store''s variant';
  exception when others then
    assert sqlerrm = 'variant_not_found', 'other store: expected variant_not_found, got ' || sqlerrm;
  end;

  -- 4. Variant store_id follows moves ---------------------------------------

  update product_variants set product_id = v_product where id = v_other_variant;
  assert (select store_id from product_variants where id = v_other_variant) = v_store,
    'variant moved to another product gets that product''s store';

  update products set store_id = v_other_store where id = v_product;
  assert (select count(*) from product_variants where product_id = v_product and store_id <> v_other_store) = 0,
    'variants follow their product to another store';

  -- 1 and 3. Dashboard role --------------------------------------------------

  set local role authenticated;

  -- Before 002 this failed with "infinite recursion detected in policy".
  perform count(*) from store_staff;
  perform count(*) from orders;
  perform count(*) from stores_public;
  perform id, name, is_active from stores;

  begin
    perform telegram_bot_token from stores;
    raise exception 'CHECK FAILED: dashboard can read the bot token';
  exception when insufficient_privilege then null;
  end;

  begin
    perform webhook_secret from stores;
    raise exception 'CHECK FAILED: dashboard can read the webhook secret';
  exception when insufficient_privilege then null;
  end;

  begin
    perform place_order(v_store, v_customer, '[]'::jsonb, 'pickup', '0911000000');
    raise exception 'CHECK FAILED: dashboard can call place_order';
  exception when others then
    assert sqlstate = '42501', 'dashboard can call place_order (got ' || sqlerrm || ')';
  end;

  reset role;
end;
$$;

select '002 checks passed' as result;

rollback;
