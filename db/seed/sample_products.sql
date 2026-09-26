-- sample_products.sql
--
-- Adds sample products to YOUR test store, so you can try searches and
-- orders by hand. Run in the Supabase SQL Editor.
--
-- 1. Change the store name on the line marked "CHANGE THIS".
-- 2. Run it. It adds:
--      Air Force 1 (Nike)   white sizes 40-44, black 42-43      4,500 ETB
--                           white 44 costs 4,800 (price_override)
--      Samba (Adidas)       white/black sizes 39-43             5,200 ETB
--      Basic T-Shirt        black/white S-XL                      800 ETB
--   Some variants have 0 stock, so you can test "sold out".
--
-- Running it twice fails on the duplicate SKUs and adds nothing the second time.

do $$
declare
  v_store_name text := 'Selam Shoes';  -- CHANGE THIS to your test store's name
  v_store uuid;
  v_product uuid;
begin
  select id into v_store from stores where name = v_store_name;
  if v_store is null then
    raise exception 'No store named "%". Check the name in the stores table.', v_store_name;
  end if;

  -- Air Force 1 ------------------------------------------------------------
  insert into products (store_id, name, brand, category, base_price)
  values (v_store, 'Air Force 1', 'Nike', 'sneakers', 4500)
  returning id into v_product;

  insert into product_variants (product_id, color, size, sku, stock_quantity, cost_price, price_override) values
    (v_product, 'White', '40', 'AF1-WHT-40', 3, 3000, null),
    (v_product, 'White', '41', 'AF1-WHT-41', 2, 3000, null),
    (v_product, 'White', '42', 'AF1-WHT-42', 5, 3000, null),
    (v_product, 'White', '43', 'AF1-WHT-43', 0, 3000, null),   -- sold out
    (v_product, 'White', '44', 'AF1-WHT-44', 1, 3000, 4800),   -- special price
    (v_product, 'Black', '42', 'AF1-BLK-42', 2, 3000, null),
    (v_product, 'Black', '43', 'AF1-BLK-43', 4, 3000, null);

  -- Samba ------------------------------------------------------------------
  insert into products (store_id, name, brand, category, base_price)
  values (v_store, 'Samba', 'Adidas', 'sneakers', 5200)
  returning id into v_product;

  insert into product_variants (product_id, color, size, sku, stock_quantity, cost_price) values
    (v_product, 'White', '39', 'SMB-WHT-39', 1, 3500),
    (v_product, 'White', '40', 'SMB-WHT-40', 0, 3500),         -- sold out
    (v_product, 'White', '41', 'SMB-WHT-41', 2, 3500),
    (v_product, 'Black', '42', 'SMB-BLK-42', 3, 3500),
    (v_product, 'Black', '43', 'SMB-BLK-43', 1, 3500);

  -- Basic T-Shirt ----------------------------------------------------------
  insert into products (store_id, name, brand, category, base_price)
  values (v_store, 'Basic T-Shirt', null, 'clothing', 800)
  returning id into v_product;

  insert into product_variants (product_id, color, size, sku, stock_quantity, cost_price) values
    (v_product, 'Black', 'S',  'TSH-BLK-S',  6, 400),
    (v_product, 'Black', 'M',  'TSH-BLK-M',  8, 400),
    (v_product, 'Black', 'L',  'TSH-BLK-L',  0, 400),          -- sold out
    (v_product, 'White', 'M',  'TSH-WHT-M',  5, 400),
    (v_product, 'White', 'XL', 'TSH-WHT-XL', 2, 400);
end;
$$;

-- Show what the store now has.
select p.name, v.color, v.size, v.stock_quantity,
       coalesce(v.price_override, p.base_price) as price
from product_variants v
join products p on p.id = v.product_id
join stores s on s.id = v.store_id
where s.name = 'Selam Shoes'  -- CHANGE THIS too
order by p.name, v.color, v.size;
