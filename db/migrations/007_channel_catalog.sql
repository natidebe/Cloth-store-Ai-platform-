-- 007_channel_catalog.sql
--
-- Run in the Supabase SQL Editor, after 006_staff_handover.sql.
-- Everything is in one transaction: if any part fails, nothing is applied.
-- Safe to run again (parts that already exist are skipped or replaced).
--
-- Phase 8d, decisions used (see BUILD_PLAN.md):
--   D30  products are added in the dashboard and posted to the channel
--   D31  one channel per store; its id is saved on the store
--   D35  owner and staff can add and edit products (already allowed by 002)
--   D36  one photo per product
--   D40  product codes are generated automatically (P101, P102, ...)


begin;


-- ---------------------------------------------------------------------------
-- 1. Product details for the channel post
-- ---------------------------------------------------------------------------

alter table products
  add column if not exists code text,         -- e.g. P101 (generated, below)
  add column if not exists description text,  -- material, fit, style ...
  add column if not exists photo_url text;    -- one photo (D36), from Supabase Storage

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'products_description_length') then
    alter table products add constraint products_description_length
      check (coalesce(length(description), 0) <= 700);
  end if;
  if not exists (select 1 from pg_constraint where conname = 'products_code_unique') then
    alter table products add constraint products_code_unique unique (store_id, code);
  end if;
end;
$$;

-- The store's Telegram channel (D31), e.g. -1001234567890.
alter table stores add column if not exists channel_id bigint;

-- Staff can read the channel id in the dashboard (002/004 grant column by column).
grant select (channel_id) on table stores to authenticated;


-- ---------------------------------------------------------------------------
-- 2. Generated product codes (D40)
-- ---------------------------------------------------------------------------
-- Each store counts its own products: P101, P102, ... The counter lives on
-- the store row; taking a number is one UPDATE, so two products saved at
-- the same moment can't get the same code.

alter table stores add column if not exists next_product_code int not null default 101;

create or replace function set_product_code()
returns trigger
language plpgsql
security definer          -- staff can't update stores, but their new product needs a code
set search_path = public
as $$
declare
  v_number int;
begin
  if new.code is null or btrim(new.code) = '' then
    update stores set next_product_code = next_product_code + 1
    where id = new.store_id
    returning next_product_code - 1 into v_number;
    new.code := 'P' || v_number;
  else
    new.code := upper(btrim(new.code));  -- a code typed by hand: "p900" -> "P900"
  end if;
  return new;
end;
$$;

drop trigger if exists trg_set_product_code on products;
create trigger trg_set_product_code
before insert or update of code on products
for each row execute function set_product_code();

-- Codes for the products that already exist, oldest first.
do $$
declare
  v_product record;
  v_number int;
begin
  for v_product in select id, store_id from products where code is null order by created_at, id loop
    update stores set next_product_code = next_product_code + 1
    where id = v_product.store_id
    returning next_product_code - 1 into v_number;
    update products set code = 'P' || v_number where id = v_product.id;
  end loop;
end;
$$;


-- ---------------------------------------------------------------------------
-- 3. product_posts: which channel post shows which product
-- ---------------------------------------------------------------------------
-- Used to edit posts when stock or price changes (D39) and to recognise a
-- post a customer forwards to the bot. If the product is deleted the post
-- row stays (product_id becomes empty) so the post can still be marked as
-- no longer available; product_code remembers which product it was.
-- caption_hash is a fingerprint of the caption last posted: if the caption
-- worked out now is different, the post needs an edit.

create table if not exists product_posts (
  id bigint generated always as identity primary key,
  store_id uuid not null references stores(id) on delete cascade,
  product_id uuid references products(id) on delete set null,
  product_code text,
  channel_id bigint not null,
  message_id bigint not null,
  has_photo boolean not null default false,  -- photo posts edit the caption, text posts the text
  caption_hash text,
  posted_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint product_posts_unique unique (store_id, channel_id, message_id)
);

create index if not exists idx_product_posts_product on product_posts(product_id);

alter table product_posts enable row level security;
drop policy if exists "staff sees own product posts" on product_posts;
create policy "staff sees own product posts" on product_posts
  for select using (is_store_member(store_id));


-- ---------------------------------------------------------------------------
-- 4. Product photos in Supabase Storage (D36)
-- ---------------------------------------------------------------------------
-- A public "product-photos" bucket (Telegram fetches the photo by its
-- address). Staff may upload only into their own store's folder:
-- product-photos/<store id>/<file>.
-- Skipped automatically where Supabase Storage doesn't exist (local tests).

do $$
begin
  if exists (select 1 from information_schema.schemata where schema_name = 'storage') then
    insert into storage.buckets (id, name, public)
    values ('product-photos', 'product-photos', true)
    on conflict (id) do nothing;

    execute $sql$ drop policy if exists "staff upload own store photos" on storage.objects $sql$;
    execute $sql$
      create policy "staff upload own store photos" on storage.objects
        for insert to authenticated
        with check (bucket_id = 'product-photos'
                    and public.is_store_member(((storage.foldername(name))[1])::uuid))
    $sql$;
    execute $sql$ drop policy if exists "staff change own store photos" on storage.objects $sql$;
    execute $sql$
      create policy "staff change own store photos" on storage.objects
        for update to authenticated
        using (bucket_id = 'product-photos'
               and public.is_store_member(((storage.foldername(name))[1])::uuid))
    $sql$;
    execute $sql$ drop policy if exists "staff delete own store photos" on storage.objects $sql$;
    execute $sql$
      create policy "staff delete own store photos" on storage.objects
        for delete to authenticated
        using (bucket_id = 'product-photos'
               and public.is_store_member(((storage.foldername(name))[1])::uuid))
    $sql$;
  end if;
end;
$$;


commit;
