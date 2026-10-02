# Mini App API (Phase 10b)

What the Telegram Mini App (the React app in `frontend/`) calls. Built and
tested in the backend; the screens come from the design. See `BUILD_PLAN.md`
Phase 10b and decisions D41–D47; inventory rules are in
`inventory-management.md`.

## Signing in

There are no passwords. Telegram gives the page `Telegram.WebApp.initData`,
signed with the bot the app was opened from. Send it with **every** request:

```
X-Telegram-Init-Data: <Telegram.WebApp.initData>
```

| Answer | Meaning | What the app shows |
|---|---|---|
| 401 | not opened from the right bot in Telegram, or older than a day | "Open the dashboard from the bot in Telegram" |
| 403 | not in the store's staff group / not an owner / not a platform admin | the `detail` text |
| 404 | the store, product or variant doesn't exist (or is another store's) | not found |
| 409 | refused by a rule (e.g. stock below 0, delete an ordered product) | the `detail` text |
| 422 | a field is wrong (e.g. negative price) | highlight the field |

Errors always look like `{"detail": "…"}`.

## Opening it

| From | URL | Signed by |
|---|---|---|
| A store: `/dashboard` to the store's bot, or "📊 Dashboard" in the staff group | `/app/?store=<store id>` | the store's bot |
| The platform bot: its "Open" menu button or any message | `/app/platform` | the platform bot |

The backend serves the built app (`frontend/dist`) for every path under
`/app/`; the app reads `?store=` and its own path.

## Roles (D42)

- **owner**: the store's creator, or an admin of its staff group: everything.
- **staff**: a member of the staff group: analytics, orders, products and stock
  (add products, change stock and sizes, edit details, upload photos), but
  never prices (403 "only the owner can change prices") and no settings.

---

## Store dashboard: `/api/v1/app/stores/{store_id}/…`

### `GET /me` (everyone)
```json
{
  "user": {"id": 123, "name": "Nati Man", "username": "nati", "language_code": "am"},
  "role": "owner",
  "store": {"id": "…", "name": "nati fashion", "status": "active", "plan": "free",
            "bot_username": "Abdisafashionbot", "staff_group_linked": true, "channel_linked": true}
}
```
`status`: `pending` (waiting for approval: show a banner), `active`, `suspended`.

### `GET /analytics?period=today|7d|30d` (everyone)
Days are Addis Ababa days; `7d` and `30d` include today. Money values are strings.
```json
{
  "period": "7d", "from": "2026-09-26T00:00:00+03:00", "to": "2026-10-03T00:00:00+03:00",
  "revenue": "14500", "payments": 3, "average_order": "4833.33",
  "orders_placed": 5, "orders_paid": 3, "paid_rate": 0.6, "unpaid_orders": 2,
  "delivery_orders": 2, "pickup_orders": 3, "new_customers": 4,
  "per_day": [{"day": "2026-09-26", "placed": 1, "paid": 1, "revenue": "3500"}, "…7 days"],
  "top_products": [{"product_id": "…", "name": "Classic Denim Jacket", "code": "P102",
                    "quantity": 3, "revenue": "10500"}],
  "low_stock": [{"variant_id": "…", "product_id": "…", "product_name": "Polo T-Shirt", "code": "P104",
                 "color": "Navy", "size": "XL", "stock": 1}],
  "ai_calls_today": 12, "ai_daily_limit": 300,
  "telegram_orders": 5, "in_shop_sales": 3, "in_shop_revenue": "27000",
  "discount_total": "1500", "discounted_items": 2,
  "sellers": [{"telegram_id": 123, "name": "Abdi", "sales": 3, "revenue": "27000", "discount": "1000"}]
}
```
Every number counts Telegram orders and counter sales together; the last
block splits them (Phase 12, D57).
- revenue = money staff confirmed in the period; `paid_rate` 0.6 = 60 % of orders were paid.
- `low_stock`: 2 or fewer left right now (0 = sold out), fewest first.

### `GET /products?search=&category=` (everyone)
```json
{
  "categories": ["clothing", "sneakers"],
  "products": [{
    "id": "…", "code": "P102", "name": "Classic Denim Jacket", "brand": "Levi's",
    "category": "clothing", "base_price": 3500, "photo_url": "https://…", "description": "…",
    "search_keywords": "jacket, ጃኬት",
    "total_stock": 12, "variant_count": 5, "on_sale": true, "low_stock": true,
    "price_min": "3500", "price_max": "3800",
    "variants": [{"id": "…", "color": "Blue", "size": "M", "stock": 5,
                  "price_override": null, "price": "3500"}]
  }]
}
```
`search` matches name, brand, code and keywords (Amharic too).

### `GET /products/{product_id}` (everyone)
One product, same shape as a list item.

### `POST /variants/{variant_id}/stock` (everyone)
```json
{"change": 5}      // +5 arrived, -1 sold at the counter
{"set": 12}        // the exact count after counting
```
→ `{"variant_id": "…", "stock": 12}`. Never below 0 (409).

### Counter sales (Phase 12, D53–D57)

A walk-in customer buys in the shop. The listed price never changes; each
item keeps the listed price and the agreed price (the difference is the
discount). The owner may agree any price up to the listed one; staff down
to the store's limit (`staff_discount_percent`, in `GET /me` → `store`).

#### `GET /variants/{variant_id}/availability` (everyone)
Before selling, especially the last piece:
```json
{"variant_id": "…", "stock": 1, "held": 1, "available": 0, "listed_price": "10000",
 "holds": [{"order_number": "AB12CD", "quantity": 1, "minutes_left": 3}]}
```
`available` 0 with `holds`: an online order is holding it (D55): show the
warning; if staff still sell it, send `allow_held: true`.

#### `POST /counter-sales` (everyone; staff within the limit)
```json
{"items": [{"variant_id": "…", "quantity": 1, "price": 9000}],
 "payment_method": "Cash", "payment_note": null,
 "customer_name": null, "customer_phone": null, "note": "agreed 9,000",
 "allow_held": false, "request_id": "<a new uuid per sale>"}
```
- `payment_method`: any text (D56); `GET /me` → `store.payment_methods` gives
  "Cash" plus the store's payment accounts; "Other" + `payment_note` for the rest.
- `request_id`: made once per sale by the app, so pressing Confirm twice
  saves once (`already_saved: true` the second time).

→ 201 `{"order_id", "number": "AB12CD", "total": "9000", "list_total": "10000",
"discount": "1000", "already_saved": false, "held_orders": []}`. Stock goes
down, the channel post updates, the staff group gets a note.

Refusals (`detail` is the message to show; the `X-Error-Code` header says which):
| Status | `X-Error-Code` | When |
|---|---|---|
| 403 | `discount_too_large` | staff below the limit ("at most 10% off… ask the owner") |
| 409 | `held_by_online_order` | an online order holds it: ask, then send again with `allow_held: true` |
| 409 | `insufficient_stock` | not enough in stock |
| 422 | `price_above_list`, `duplicate_item`, … | check the input |

The staff limit is changed in `PUT /settings` → `staff_discount_percent` (owners).

### `GET /orders?channel=all|telegram|in_shop&status=all|unpaid|paid&limit=30&before=<created_at>` (everyone)
View only (confirming payments stays in the staff group, D46). Newest first;
for more, pass the last order's `created_at` as `before`.
```json
{"orders": [{"id": "…", "number": "AB12CD", "status": "pending", "payment_status": "unpaid",
             "total": 7300, "currency": "ETB", "fulfillment": "delivery",
             "customer": {"name": "Abebe", "phone": "0911223344"}, "delivery_address": null,
             "created_at": "…", "items": [{"name": "Classic Denim Jacket", "code": "P102",
             "color": "Blue", "size": "M", "quantity": 2, "price": 3650, "list_price": null}],
             "channel": "telegram", "payment_method": null, "sold_by": null, "note": null}],
 "more": false}
```
Counter sales come in the same list with `"channel": "in_shop"`, `sold_by`,
`payment_method`, and each item's `list_price` next to the `price` paid.

### `POST /products` (everyone; staff: no prices)
```json
{
  "product": {"name": "Polo T-Shirt", "brand": "Lacoste", "category": "clothing",
              "base_price": 1800, "description": "…", "search_keywords": "polo, ፖሎ",
              "photo_url": "https://… (from POST /photos)"},
  "variants": [{"color": "White", "size": "M", "stock": 8},
               {"color": "Navy", "size": "XL", "stock": 3, "price": 2000}]
}
```
→ 201, the product (as above). The code (P105…) is generated. If the store
is active and has a channel, it's posted there automatically.

### `PATCH /products/{product_id}` (everyone; staff: not the price)
Any of the product fields; only those sent change.

### `PUT /products/{product_id}/variants` (everyone; staff: no price changes): the color × size grid
```json
{
  "variants": [{"id": "…", "color": "Blue", "size": "M", "stock": 10},
               {"color": "Black", "size": "L", "stock": 4, "price": 3600}],
  "remove": ["<variant id>"]
}
```
Rows match existing variants by `id`, else by color + size; matched rows are
updated, new ones added. `remove` deletes never-ordered variants; ordered
ones are kept with stock 0. → `{"product": …, "added": 1, "updated": 1,
"removed": 0, "note": "Blue XL: ordered before, so kept with stock 0 instead of deleted."}`

### `POST /products/{product_id}/off-sale` (owners)
All its variants to stock 0 (the post shows SOLD OUT). → `{"ok": true, "variants": 5}`

### `DELETE /products/{product_id}` (owners)
Only never-ordered products; otherwise 409 "take it off sale instead".

### `POST /products/{product_id}/publish` (owners)
Post to the channel, or update its post. 409 if the store isn't approved or has no channel.

### `POST /photos` (everyone)
`multipart/form-data` with `file` (JPEG, PNG or WebP, up to 5 MB) →
`{"photo_url": "https://…/product-photos/<store>/<random>.jpg"}`; then save it
as the product's `photo_url`.

### `GET /settings`, `PUT /settings` (owners)
The Store profile screen, as lists (migration 011):
```json
{"payment_accounts": [{"name": "Telebirr", "number": "0911 000 000", "holder": "nati fashion"}],
 "delivery_areas": [{"area": "Bole", "fee": 150}],
 "opening_week": {"mon": {"open": true, "from": "08:30", "to": "19:00"}, "…": "…", "sun": {"open": false}},
 "location": "Bole, Edna Mall", "pickup_instructions": "…", "return_policy": "…",
 "payment_instructions": "Telebirr: 0911 000 000 (nati fashion)", "delivery_info": "Bole: 150 ETB",
 "opening_hours": "Mon–Sat 08:30–19:00, Sun closed"}
```
PUT: only the fields sent change; an empty text clears one. Saving the lists
also writes the texts customers get (`payment_instructions`, `delivery_info`,
`opening_hours`), so the bot works unchanged.

### `GET /connections` (owners)
`{"staff_group": {"id": -500, "title": "nati fashion staff", "bot_can_see": true}, "channel": null}`.
The Connect screen asks every 3 seconds while a `/link` code waits.

### `POST /link-code` (owners)
→ `{"code": "K5W3BJPA", "command": "/link K5W3BJPA", "minutes": 30, "expires_at": "…"}`.
Show the command to send in the staff group (or the channel). Works once.

### `PUT /bot-token` (owners)
`{"bot_token": "…"}` → `{"bot_username": "…", "bot_connected": true, "note": "…"}`.
After a different bot, the app must be reopened from the new bot.

---

## Platform bot: `/api/v1/platform-app/…`

### `GET /me` (anyone)
```json
{"user": {"id": 123, "name": "…"}, "is_platform_admin": true, "support_url": "https://t.me/…",
 "stores": [{"id": "…", "name": "nati fashion", "status": "pending", "plan": "free",
             "bot_username": "…", "staff_group_linked": false, "channel_linked": false,
             "dashboard_url": "https://…/app/?store=…"}]}
```
Show "Create your store" if `stores` is empty; each store links to its
dashboard (open it from the store's bot: `t.me/<bot_username>?start=dashboard`).

### `POST /stores` (anyone)
`{"name": "nati fashion", "bot_token": "<from @BotFather>"}` → 201, a store as
above plus `bot_connected` and `note`. 400: the token doesn't work; 409: the
bot already belongs to a store.

### Platform admins only (D45)
- `GET /admin/stores` → `[{"id", "name", "status", "plan", "telegram_bot_username", "created_at", "orders"}]`
- `POST /admin/stores/{id}/approve`, `POST /admin/stores/{id}/suspend` → `{"store_id", "status", "plan"}`
- `PUT /admin/stores/{id}/plan` `{"plan": "free" | "basic" | "pro"}`
