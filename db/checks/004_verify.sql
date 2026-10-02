-- 004_verify.sql
--
-- Run in the Supabase SQL Editor AFTER 004_store_profile.sql.
-- Success: a table where every row's "ok" column is true.

select 'stores has all 6 profile columns' as check,
  (select count(*) from information_schema.columns
   where table_schema = 'public' and table_name = 'stores'
     and column_name in ('opening_hours', 'location', 'delivery_info',
                         'pickup_instructions', 'payment_instructions', 'return_policy')) = 6 as ok
union all
select 'products has search_keywords', exists (
  select 1 from information_schema.columns
  where table_schema = 'public' and table_name = 'products' and column_name = 'search_keywords')
union all
select 'staff can read the profile',
  has_column_privilege('authenticated', 'public.stores', 'opening_hours', 'select')
  and has_column_privilege('authenticated', 'public.stores', 'payment_instructions', 'select')
union all
select 'staff can edit the profile',
  has_column_privilege('authenticated', 'public.stores', 'opening_hours', 'update')
  and has_column_privilege('authenticated', 'public.stores', 'return_policy', 'update')
union all
select 'staff still cannot read the bot token',
  not has_column_privilege('authenticated', 'public.stores', 'telegram_bot_token', 'select')
union all
select 'staff still cannot read the webhook secret',
  not has_column_privilege('authenticated', 'public.stores', 'webhook_secret', 'select')
union all
select 'staff cannot change name, bot token, or on/off switch',
  not has_column_privilege('authenticated', 'public.stores', 'name', 'update')
  and not has_column_privilege('authenticated', 'public.stores', 'telegram_bot_token', 'update')
  and not has_column_privilege('authenticated', 'public.stores', 'is_active', 'update')
union all
select 'edit rule exists (own store only)', exists (
  select 1 from pg_policies
  where tablename = 'stores' and policyname = 'staff edits own store profile' and cmd = 'UPDATE')
union all
select 'not-logged-in visitors cannot read stores',
  not has_column_privilege('anon', 'public.stores', 'opening_hours', 'select');
