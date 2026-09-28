-- sample_store_profile.sql
--
-- Fills in YOUR test store's profile and product nicknames, so the bot has
-- real information to give (instead of guessing). Run in the Supabase SQL
-- Editor AFTER 004_store_profile.sql. Safe to run again: it just overwrites.
--
-- 1. Change the store name on the line marked "CHANGE THIS".
-- 2. Change the example texts to your store's real details.
--    Leave a field as null if you don't know it yet: the bot will then say
--    it will check with the team, instead of inventing an answer.

do $$
declare
  v_store_name text := 'Selam Shoes';  -- CHANGE THIS to your test store's name
  v_store uuid;
begin
  select id into v_store from stores where name = v_store_name;
  if v_store is null then
    raise exception 'No store named "%". Check the name in the stores table.', v_store_name;
  end if;

  update stores set
    opening_hours        = 'Mon–Sat 8:30–19:00, Sunday closed',
    location             = 'Bole, next to Edna Mall, 2nd floor',
    delivery_info        = 'Bole and CMC: 150 ETB. Other areas in Addis Ababa: 250 ETB. Delivery in 1–2 days.',
    pickup_instructions  = 'Pick up at the shop during opening hours. Bring your order number.',
    payment_instructions = 'Pay by Telebirr to 0911 000 000 (Selam Shoes) or CBE account 1000 0000 0000, then send a screenshot here.',
    return_policy        = 'Exchange within 3 days with the receipt, if unworn. No cash refunds.'
  where id = v_store;

  -- Product nicknames (comma-separated), for the products in sample_products.sql.
  update products set search_keywords = 'AF1, air force, ኤር ፎርስ, ናይኪ'
    where store_id = v_store and name = 'Air Force 1';
  update products set search_keywords = 'ሳምባ, adidas samba'
    where store_id = v_store and name = 'Samba';
  update products set search_keywords = 'tshirt, tee, ቲሸርት, ካናቴራ'
    where store_id = v_store and name = 'Basic T-Shirt';
end;
$$;

-- Show the result.
select name, opening_hours, location, delivery_info, payment_instructions
from stores
where name = 'Selam Shoes';  -- CHANGE THIS too
