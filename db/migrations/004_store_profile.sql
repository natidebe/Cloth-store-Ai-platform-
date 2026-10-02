-- 004_store_profile.sql
--
-- Run once in the Supabase SQL Editor, after 003_conversations.sql.
-- Everything is in one transaction: if any part fails, nothing is applied.
--
-- Decisions used (see BUILD_PLAN.md):
--   D22  store profile: opening hours, location, delivery areas and fees,
--        pickup instructions, payment instructions, return policy
--   D23  product nicknames ("search keywords") now
--
-- Why: in testing the AI invented opening hours, and searched for "AF1"
-- (a nickname) which found nothing because the product is "Air Force 1".


begin;


-- ---------------------------------------------------------------------------
-- 1. Store profile
-- ---------------------------------------------------------------------------
-- Free text, written by the store owner, read by the AI. Empty means "not
-- set": the AI must then say it will check with the team, never guess.
-- Limited to 1000 characters each, to keep the AI's instructions short.

alter table stores
  add column opening_hours text,         -- e.g. "Mon–Sat 8:30–19:00, Sun closed"
  add column location text,              -- e.g. "Bole, next to Edna Mall, 2nd floor"
  add column delivery_info text,         -- areas and fees, e.g. "Bole & CMC 150 ETB, other areas 250 ETB"
  add column pickup_instructions text,   -- e.g. "Pick up at the shop, bring your order number"
  add column payment_instructions text,  -- sent after ordering, e.g. "Telebirr 09.. (Selam Shoes)"
  add column return_policy text,         -- e.g. "Exchange within 3 days with receipt"
  add constraint stores_profile_length check (
    coalesce(length(opening_hours), 0) <= 1000
    and coalesce(length(location), 0) <= 1000
    and coalesce(length(delivery_info), 0) <= 1000
    and coalesce(length(pickup_instructions), 0) <= 1000
    and coalesce(length(payment_instructions), 0) <= 1000
    and coalesce(length(return_policy), 0) <= 1000
  );


-- ---------------------------------------------------------------------------
-- 2. Staff can read and edit their own store's profile (dashboard)
-- ---------------------------------------------------------------------------
-- 002 made `stores` column-by-column: staff may read only safe columns and
-- change nothing. Here they get read AND update on the profile columns only.
-- They still can't change the name, plan, is_active, staff_chat_id, or see
-- or change the bot token and webhook secret.

grant select (opening_hours, location, delivery_info, pickup_instructions,
              payment_instructions, return_policy)
  on table stores to authenticated;

grant update (opening_hours, location, delivery_info, pickup_instructions,
              payment_instructions, return_policy)
  on table stores to authenticated;

-- ...and only on their own store's row.
create policy "staff edits own store profile" on stores
  for update
  using (is_store_member(id))
  with check (is_store_member(id));


-- ---------------------------------------------------------------------------
-- 3. Product nicknames
-- ---------------------------------------------------------------------------
-- Comma-separated words customers use for a product, in any language,
-- e.g. "AF1, air force, ኤር ፎርስ". The product search also looks here.
-- Staff already manage their own products (002), so they can edit this.

alter table products
  add column search_keywords text,
  add constraint products_search_keywords_length
    check (coalesce(length(search_keywords), 0) <= 500);


commit;
