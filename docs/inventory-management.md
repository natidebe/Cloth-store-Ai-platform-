# Inventory management

How products, stock and prices work in the platform, from adding a product
to the moment stock goes down. Decisions referred to (D3, D19, …) are in the
Decisions table in `BUILD_PLAN.md`.

---

## 1. The big picture

```
Owner/staff add a product           Customer orders                Staff confirm payment
(dashboard, or SQL for now)         in the Telegram bot            (staff group or dashboard)
          │                                  │                                │
          ▼                                  ▼                                ▼
 products + variants  ──────►  order placed: items HELD  ──────►  stock goes DOWN, order PAID
 (color × size, stock)          for 5 minutes (D19)               (refused if sold out)
          │
          ▼
 posted to the store's channel; the post updates itself when stock or price changes
```

Three rules hold everything together:

1. **Stock lives on the variant** (one color + one size), never on the product.
2. **Stock only goes down when staff confirm the payment (D3).** Placing an
   order doesn't reduce stock; it only holds the items for 5 minutes (D19).
3. **The database never oversells.** Stock can't go below zero, and a payment
   is refused if the items are no longer there.

---

## 2. Products and variants

### Product: what the customer chooses

| Field | Example | Notes |
|---|---|---|
| `name` | Classic Denim Jacket | |
| `brand` | Levi's | optional |
| `category` | clothing | becomes a button in the bot ("Clothing") |
| `base_price` | 3500 | the price of every variant, unless a variant has its own |
| `description` | 100% cotton, classic fit | up to 700 characters, shown in the channel post |
| `photo_url` | `https://…/jacket.jpg` | one photo (D36); must be an `https://` link, anything else is ignored |
| `search_keywords` | jacket, denim, ጃኬት | nicknames customers type, in English or Amharic (D23) |
| `code` | P101 | **generated automatically** per store: P101, P102, … (D40) |

### Variant: what is actually in stock

One row per color + size:

| Field | Example | Notes |
|---|---|---|
| `color` | Blue | |
| `size` | XL | |
| `stock_quantity` | 3 | never below 0 (a database rule) |
| `price_override` | 3800 | optional: this variant costs more or less than `base_price` |
| `cost_price` | 2000 | optional, what the shop paid; never shown to customers or the AI |
| `store_id` | (filled in automatically from the product) | |

**The price a customer pays** = `price_override` if set, otherwise the
product's `base_price`. A variant with neither has no price and **can't be
sold** (the bot doesn't offer it).

---

## 3. "Available" vs "in stock"

Two numbers matter, and they're different on purpose:

- **Stock** (`stock_quantity`): what's physically in the shop.
- **Available**: stock minus what other customers' fresh orders are holding.

```
available = stock_quantity − (items in unpaid orders placed in the last 5 minutes)
```

| Where | Uses | Why |
|---|---|---|
| The bot (colors, sizes, quantity buttons) | **available** | so two customers can't both be promised the last pair |
| The channel post | **stock** | so posts don't flicker for 5 minutes every time someone orders |
| Confirming a payment | **available** (other orders' holds) | the final check before stock goes down |

---

## 4. The life of an order, step by step

### 4.1 Choosing

- The bot only shows colors, sizes and quantities that are **available** and
  **have a price**. Sizes appear only for the chosen color.
- Quantity buttons go up to what's available (at most 5 buttons; a customer
  can type up to 10).
- If something sells out while the customer is choosing, the bot says so and
  asks again. It never switches to a different item on its own.

### 4.2 The cart (D32)

- One order can hold several items (up to 10 lines).
- The same variant added twice is added up, but never beyond what's available.
- Just before the summary, every item is checked against the database again.
  An item that sold out meanwhile is removed, with a note to the customer.

### 4.3 Placing the order: the 5-minute hold (D19)

- When the customer confirms, the order is created with status `pending` /
  `unpaid`, at the **database prices** (never prices from the chat or the AI).
- Its items are **held for 5 minutes**: other customers see them as not
  available during that time. Stock itself doesn't change.
- This applies to pickup **and** delivery orders.
- After 5 minutes the hold simply ends; nothing needs to run. The order stays
  `pending` / `unpaid` until staff confirm a payment.

### 4.4 Paying

- **Pickup:** the customer gets the store's payment accounts and sends a
  screenshot. Staff check the money arrived in their Telebirr/bank app.
- **Delivery (D29):** the customer pays when the items arrive. Staff call to
  arrange the address.

### 4.5 Confirming the payment: stock goes down (D3)

Staff press **✅ Confirm payment** in the staff group (or the dashboard calls
`POST /api/v1/admin/stores/{store}/orders/{order}/confirm-payment`). In one
step, all-or-nothing, the database:

1. checks every item is still there (stock minus *other* orders' holds),
2. **reduces the stock** of every item,
3. records the payment (amount, method, who confirmed it),
4. marks the order `confirmed` / `paid`.

If any item sold out in the meantime, **nothing changes** and staff see a
popup saying which item is gone. A payment can't be confirmed twice.

> **Process rule for every shop:** the bot does not verify payments. A
> screenshot can be fake. Staff must check the money arrived before pressing
> Confirm.

---

## 5. The channel catalog stays in sync (Phase 8d)

Each product has one post in the store's Telegram channel: photo, name,
price ("from …" when variants differ), the colors and sizes **in stock**,
description, code, and the **🛒 እዘዝ / Order** button.

| Change in the database | What happens to the post |
|---|---|
| New product saved (store active, channel linked) | posted automatically |
| Price, stock, name, description changed | the post is edited (a few seconds later) |
| A size sells out | that size disappears from the post |
| Everything sold out | the post shows **❌ ተሽጧል / SOLD OUT** |
| Product deleted | the post says **❌ No longer available** |

How:
- Supabase database webhooks report every change to `products` and
  `product_variants` (from the dashboard, a payment, or the Table Editor).
  Changes within 3 seconds are grouped into one post edit.
- Every 5 minutes a check compares all posts with the database and fixes any
  that are out of date (in case a webhook was missed).
- A post is only edited when its text actually changes.

Customers who tap Order on a sold-out post get "Sorry, X is sold out" and
buttons for similar products in the same category (D34).

---

## 6. Who changes stock, and how

| Who | How | When |
|---|---|---|
| **Confirming a payment** | automatic (stock − ordered quantity) | every paid order |
| **Owner / staff** | the dashboard (product form with a color × size stock grid) | new stock arrives, corrections, counter sales |
| **Owner / staff, for now** | SQL in the Supabase SQL Editor, or the Table Editor | until the dashboard exists |
| The bot, the AI | **never** | the AI only reads prices and stock, it can't change them |

The dashboard writes products and variants directly in Supabase. The
database's security rules allow staff to change **only their own store's**
products. Every change is picked up by the channel sync above.

### Common tasks in SQL (until the dashboard exists)

Replace the store id and product code with your own. Every store numbers its
own products (each starts at P101), so always give both.

**Add stock to existing variants**
```sql
update product_variants v
set stock_quantity = v.stock_quantity + s.added
from products p,
     (values ('Blue', 'M', 5), ('Blue', 'L', 3)) as s(color, size, added)
where v.product_id = p.id
  and p.store_id = '<store id>' and p.code = 'P102'
  and v.color = s.color and v.size = s.size
returning v.color, v.size, v.stock_quantity;
```

**Set the exact stock (after a count)**
```sql
update product_variants v
set stock_quantity = 10
from products p
where v.product_id = p.id
  and p.store_id = '<store id>' and p.code = 'P102'
  and v.color = 'Blue' and v.size = 'M';
```

**Add a new color or size**
```sql
insert into product_variants (product_id, color, size, stock_quantity, price_override)
select p.id, 'Grey', 'L', 4, null
from products p
where p.store_id = '<store id>' and p.code = 'P102';
```

**Change the price**
```sql
-- all variants without their own price
update products set base_price = 3900
where store_id = '<store id>' and code = 'P102';

-- one variant only
update product_variants v set price_override = 4200
from products p
where v.product_id = p.id and p.store_id = '<store id>' and p.code = 'P102'
  and v.color = 'Blue' and v.size = 'XL';
```

**See a product's stock**
```sql
select v.color, v.size, v.stock_quantity,
       coalesce(v.price_override, p.base_price) as price
from product_variants v join products p on p.id = v.product_id
where p.store_id = '<store id>' and p.code = 'P102'
order by v.color, v.size;
```

**Take a product off sale:** set all its variants' stock to 0. The post
shows SOLD OUT and the bot stops offering it. This is the normal way:
a product that has ever been ordered can't be deleted (its order lines
point to it).
```sql
update product_variants v set stock_quantity = 0
from products p
where v.product_id = p.id and p.store_id = '<store id>' and p.code = 'P102';
```

---

## 7. Edge cases and how they're handled

| Situation | What happens |
|---|---|
| Two customers order the last pair at the same time | The first order holds it; the second customer is told it just sold out |
| A customer orders but never pays | The hold ends after 5 minutes; others can buy the item again. The order stays `pending` / `unpaid` |
| Stock is lowered after an order was placed | The payment confirmation is refused if the items are no longer there |
| A shop sells an item at the counter | Staff lower the stock (dashboard or SQL). The post and the bot update |
| Staff type a negative stock | Refused by the database (stock can't go below 0) |
| A product is deleted | Only possible if it was **never ordered**: order lines point to its variants, so the database refuses (order history stays intact). Its post then says "No longer available". For an ordered product, set its stock to 0 instead |
| Photo link is not a real `https://` link | The photo is ignored; the post goes out as text |
| A text post later gets a photo | Telegram can't turn text into a photo post: delete the post and its `product_posts` row, then publish again |
| A product is added while the store is pending | It isn't posted automatically; publish it after approval (`publish_product … --all`) |

---

## 8. Not built yet (planned or open)

- **Dashboard screens** (teammate): product form with the color × size stock
  grid, photo upload, Publish button, stock list.
- **Order status after payment:** marking orders out for delivery, delivered
  or cancelled is only possible in the database for now.
- **Unpaid orders** are never closed automatically (harmless: their hold
  already ended, but the list of pending orders grows).
- **Returns and refunds:** if a paid order is returned, staff add the stock
  back by hand. `payment_status` already allows `refunded`.
- **Stock history:** there's no log of who changed stock and when (only
  payments record who confirmed them).
- **Low-stock alerts** to the staff group.
- **Auto-posting after approval and automatic photo re-posts** (offered, not
  built).

---

## 9. Where it lives in the code

| Part | File |
|---|---|
| Tables, stock rule, prices | `db/migrations/001`, `002` |
| Holds, `place_order`, `confirm_payment` | `db/migrations/005_orders_agent.sql` |
| Product codes, channel posts, photo | `db/migrations/007_channel_catalog.sql` |
| Reading products, variants, availability | `backend/app/services/supabase_service.py` |
| Choosing, cart, sold-out handling in the chat | `backend/app/agents/flow.py` |
| Placing orders, payment messages | `backend/app/agents/tools.py` |
| Confirming payments (staff group) | `backend/app/agents/staff.py` |
| Channel posts and their sync | `backend/app/agents/catalog.py`, `backend/app/api/v1/catalog.py` |
| Posting by hand | `backend/scripts/publish_product.py` |
