-- Test product for "nati fashion": Nike sneakers with their variants
-- (color x size, stock). Run in the Supabase SQL Editor, all at once.
-- The product code is generated automatically (P105, ...).
-- Run it only once (a second run adds a duplicate product).
-- (The Polo T-Shirt, P104, was already added.)
-- For Selam Shoes, replace the store id with 592886fe-2a8d-4c03-91f2-512258c739d9.

insert into products (store_id, name, brand, category, base_price, description, search_keywords)
values ('e3090b36-47f9-4faf-8177-60e4aabcf693', 'Nike Air Max 90', 'Nike', 'sneakers', 6500,
        'Iconic running-style sneaker with visible Air cushioning.',
        'nike, air max, airmax, sneakers, shoes, ጫማ, ናይኪ');

insert into product_variants (product_id, color, size, stock_quantity, price_override)
select p.id, v.color, v.size, v.stock, v.price
from products p
cross join (values
    ('White', '40', 3, null::numeric),
    ('White', '41', 4, null),
    ('White', '42', 5, null),
    ('White', '43', 3, null),
    ('Black', '41', 2, null),
    ('Black', '42', 4, null),
    ('Black', '44', 2, 6800)
) as v(color, size, stock, price)
where p.store_id = 'e3090b36-47f9-4faf-8177-60e4aabcf693'
  and p.name = 'Nike Air Max 90';

-- Check: code, number of variants, total stock (expected: 7 variants, 23 in stock).
select p.code, p.name, count(v.id) as variants, sum(v.stock_quantity) as total_stock
from products p
left join product_variants v on v.product_id = p.id
where p.store_id = 'e3090b36-47f9-4faf-8177-60e4aabcf693'
  and p.name = 'Nike Air Max 90'
group by p.code, p.name;
