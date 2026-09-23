
create extension if not exists "pgcrypto";


create table stores (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  telegram_bot_token text,
  plan text default 'basic',
  created_at timestamptz default now()
);


create table store_staff (
  id uuid primary key default gen_random_uuid(),
  store_id uuid references stores(id) on delete cascade not null,
  user_id uuid references auth.users(id) on delete cascade not null,
  role text default 'staff',
  created_at timestamptz default now(),
  unique(store_id, user_id)
);


create table products (
  id uuid primary key default gen_random_uuid(),
  store_id uuid references stores(id) on delete cascade not null,
  name text not null,
  brand text,
  category text,
  base_price numeric,
  created_at timestamptz default now()
);


create table product_variants (
  id uuid primary key default gen_random_uuid(),
  product_id uuid references products(id) on delete cascade not null,
  store_id uuid references stores(id),
  color text,
  size text,
  sku text,
  stock_quantity int default 0,
  cost_price numeric,
  price_override numeric,
  created_at timestamptz default now()
);


create or replace function set_variant_store_id()
returns trigger as $$
begin
  select store_id into new.store_id from products where id = new.product_id;
  return new;
end;
$$ language plpgsql;

create trigger trg_set_variant_store_id
before insert on product_variants
for each row execute function set_variant_store_id();

alter table product_variants
  add constraint unique_sku_per_store unique (store_id, sku);


create table customers (
  id uuid primary key default gen_random_uuid(),
  store_id uuid references stores(id) on delete cascade not null,
  telegram_id bigint,
  name text,
  phone text,
  address text,
  created_at timestamptz default now(),
  unique(store_id, telegram_id)
);


create table orders (
  id uuid primary key default gen_random_uuid(),
  store_id uuid references stores(id) on delete cascade not null,
  customer_id uuid references customers(id) on delete set null,
  status text default 'pending',         
  payment_status text default 'unpaid',  
  total_price numeric,
  created_at timestamptz default now()
);


create table order_items (
  id uuid primary key default gen_random_uuid(),
  order_id uuid references orders(id) on delete cascade not null,
  variant_id uuid references product_variants(id),
  quantity int not null default 1,
  price numeric not null
);


create table payments (
  id uuid primary key default gen_random_uuid(),
  order_id uuid references orders(id) on delete cascade not null,
  amount numeric not null,
  method text,         
  paid_at timestamptz default now(),
  confirmed_by uuid references auth.users(id)
);


create index idx_products_store on products(store_id);
create index idx_variants_product on product_variants(product_id);
create index idx_customers_store on customers(store_id);
create index idx_orders_store on orders(store_id);
create index idx_order_items_order on order_items(order_id);



alter table stores enable row level security;
alter table store_staff enable row level security;
alter table products enable row level security;
alter table product_variants enable row level security;
alter table customers enable row level security;
alter table orders enable row level security;
alter table order_items enable row level security;
alter table payments enable row level security;

create policy "staff sees own stores" on stores
  for select using (
    id in (select store_id from store_staff where user_id = auth.uid())
  );

create policy "staff sees own store_staff rows" on store_staff
  for select using (
    store_id in (select store_id from store_staff where user_id = auth.uid())
  );

create policy "staff manages own products" on products
  for all using (
    store_id in (select store_id from store_staff where user_id = auth.uid())
  );

create policy "staff manages own variants" on product_variants
  for all using (
    store_id in (select store_id from store_staff where user_id = auth.uid())
  );

create policy "staff manages own customers" on customers
  for all using (
    store_id in (select store_id from store_staff where user_id = auth.uid())
  );

create policy "staff manages own orders" on orders
  for all using (
    store_id in (select store_id from store_staff where user_id = auth.uid())
  );

create policy "staff manages own order_items" on order_items
  for all using (
    order_id in (
      select id from orders
      where store_id in (select store_id from store_staff where user_id = auth.uid())
    )
  );

create policy "staff manages own payments" on payments
  for all using (
    order_id in (
      select id from orders
      where store_id in (select store_id from store_staff where user_id = auth.uid())
    )
  );


