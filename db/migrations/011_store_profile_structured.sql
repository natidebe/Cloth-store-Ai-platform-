-- 011_store_profile_structured.sql
--
-- Run in the Supabase SQL Editor, after 010_mini_app.sql.
-- Everything is in one transaction: if any part fails, nothing is applied.
-- Safe to run again.
--
-- Phase 10b, the Mini App's Store profile screen: payment accounts, delivery
-- areas with fees, and opening hours per day are edited as lists, not free
-- text. The backend keeps writing the matching texts (payment_instructions,
-- delivery_info, opening_hours) that the bot already sends customers, so
-- the bot works unchanged.


begin;


alter table stores
  -- [{"name": "Telebirr", "number": "0911 000 000"}, ...]
  add column if not exists payment_accounts jsonb not null default '[]'::jsonb,
  -- [{"area": "Bole", "fee": 150}, ...]   (fee in ETB)
  add column if not exists delivery_areas jsonb not null default '[]'::jsonb,
  -- {"mon": {"open": true, "from": "08:30", "to": "19:00"}, ..., "sun": {"open": false}}
  add column if not exists opening_week jsonb;

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'stores_profile_lists_check') then
    alter table stores add constraint stores_profile_lists_check check (
      jsonb_typeof(payment_accounts) = 'array' and jsonb_array_length(payment_accounts) <= 10
      and jsonb_typeof(delivery_areas) = 'array' and jsonb_array_length(delivery_areas) <= 30
      and (opening_week is null or jsonb_typeof(opening_week) = 'object')
    );
  end if;
end;
$$;

-- Only the backend reads and writes these (the Mini App goes through it);
-- nothing is granted to the dashboard role.


commit;
