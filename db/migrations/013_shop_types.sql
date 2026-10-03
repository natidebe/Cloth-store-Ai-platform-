-- 013_shop_types.sql
--
-- Run in the Supabase SQL Editor, after 012_counter_sales.sql.
-- Everything is in one transaction: if any part fails, nothing is applied.
-- Safe to run again.
--
-- Phase 13, shop types (decisions D58–D63):
--   D58  a shop is clothing, electronics, cosmetics or general; existing shops: clothing
--   D59  the owner may rename the two product options (English and Amharic)
--   D60  electronics products have a condition (new / used) and a warranty
--   D61  the type can change later: only the words change, data stays
--   D62  condition and warranty belong to the product, not the variant
--
-- Products keep their color and size columns: they are simply "option 1" and
-- "option 2", named by the shop's type. Nothing is moved or rewritten.


begin;


-- ---------------------------------------------------------------------------
-- 1. The shop's type and its own words for the two options
-- ---------------------------------------------------------------------------

alter table stores
  add column if not exists shop_type text not null default 'clothing',
  -- The owner's renames, e.g. {"option2": {"en": "Model", "am": "ሞዴል"}}; null = the type's words.
  add column if not exists option_labels jsonb;

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'stores_shop_type_check') then
    alter table stores add constraint stores_shop_type_check
      check (shop_type in ('clothing', 'electronics', 'cosmetics', 'general'));
  end if;
  if not exists (select 1 from pg_constraint where conname = 'stores_option_labels_check') then
    alter table stores add constraint stores_option_labels_check check (
      option_labels is null
      or (jsonb_typeof(option_labels) = 'object' and length(option_labels::text) <= 1000));
  end if;
end;
$$;


-- ---------------------------------------------------------------------------
-- 2. Electronics: condition and warranty, per product
-- ---------------------------------------------------------------------------

alter table products
  add column if not exists condition text,          -- 'new' or 'used'; null = not said
  add column if not exists warranty_months int;     -- null or 0 = no warranty

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'products_condition_check') then
    alter table products add constraint products_condition_check
      check (condition is null or condition in ('new', 'used'));
  end if;
  if not exists (select 1 from pg_constraint where conname = 'products_warranty_months_check') then
    alter table products add constraint products_warranty_months_check
      check (warranty_months is null or warranty_months between 0 and 120);
  end if;
end;
$$;


commit;


-- Check (optional): every existing shop is 'clothing', nothing else changed.
-- select shop_type, count(*) from stores group by shop_type;
