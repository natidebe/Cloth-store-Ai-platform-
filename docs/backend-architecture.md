# Backend Architecture

This document describes the structure of the FastAPI backend and how each
part maps to the database schema defined in `db/migrations/001_init_schema.sql`.

## Responsibilities

The backend is a pure API service with three jobs:

1. Receive incoming Telegram messages (one webhook per store)
2. Run the AI agent logic: classify the message, check live data, decide
   how to respond
3. Read from and write to Supabase — inventory, customers, orders, payments

It has no server-rendered pages and no built-in admin UI — the staff
dashboard is a separate frontend project that talks to Supabase directly
(using the `anon` key + Row Level Security), not through this backend.

## Folder structure

```
backend/
├── app/
│   ├── main.py
│   ├── core/
│   │   ├── config.py
│   │   └── security.py
│   ├── api/
│   │   └── v1/
│   │       ├── webhook.py
│   │       ├── admin.py
│   │       └── health.py
│   ├── services/
│   │   ├── telegram_service.py
│   │   ├── supabase_service.py
│   │   ├── llm_service.py
│   │   └── conversation_service.py
│   ├── models/
│   │   └── schemas.py
│   ├── agents/
│   │   ├── orchestrator.py
│   │   ├── prompts.py
│   │   └── tools.py
│   └── utils/
│       └── logging.py
├── tests/
├── requirements.txt
└── .env.example
```

## File-by-file, mapped to the schema

### `main.py`
Creates the FastAPI app, registers all routers from `api/v1/`, sets up
startup/shutdown hooks (e.g. initializing the Supabase client once at
startup rather than per-request).

### `core/config.py`
Loads environment variables: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`,
`LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, `PUBLIC_BASE_URL`, `LOG_LEVEL`.
Note: individual store Telegram bot tokens
are **not** environment variables — they're stored in `stores.telegram_bot_token`,
since each store has its own bot. The `.env` only holds platform-level
secrets.

### `core/security.py`
Verifies that incoming webhook requests genuinely came from Telegram
(Telegram supports a secret token check per webhook), preventing spoofed
requests from hitting your agent logic.

### `api/v1/webhook.py`
`POST /webhook/{store_id}` — the single entry point for every customer
message, across every store. Looks up the store, hands the message to
`conversation_service.py`, and returns a fast response to Telegram (it
expects a quick 200 OK; slow LLM calls should be handled asynchronously
where possible).

### `api/v1/admin.py`
Endpoints the staff dashboard (or you, manually) can call for actions the
agent doesn't handle itself — e.g. manually resolving an escalation, or
retrying a stuck order.

### `api/v1/health.py`
A simple `GET /health` endpoint — useful once you deploy this somewhere
and want uptime monitoring.

### `services/telegram_service.py`
Two jobs: parses the raw Telegram update payload into a clean message
object, and sends replies back to the customer via Telegram's `sendMessage`
API using the correct store's bot token (fetched from `stores` via
`supabase_service.py`).

### `services/supabase_service.py`
Every database interaction lives here — nothing else in the app writes
raw queries. Functions map directly onto tables:

| Function (conceptual)              | Table(s) touched                     |
|-------------------------------------|----------------------------------------|
| `get_store_by_id`                   | `stores`                              |
| `find_variant_stock(product, color, size)` | `products`, `product_variants` |
| `get_or_create_customer(telegram_id)` | `customers`                         |
| `create_order(customer, items)`     | `orders`, `order_items` (via the `place_order` database function) |
| `confirm_payment(order_id, amount, method)` | `payments`, `orders`, `product_variants` (via the `confirm_payment` database function) |
| `update_stock(variant_id, delta)`   | `product_variants` (via the `adjust_stock` database function) |

Anything that must fully succeed or fully fail runs as one Postgres
function, defined in `db/migrations/002_platform_updates.sql`:

- `place_order`: checks the customer and items belong to the store, takes
  prices from the database, checks stock, and creates the order and its
  items. It does **not** reduce stock.
- `confirm_payment`: records the payment, reduces stock, and marks the order
  paid. Stock goes down here, once staff confirm the money arrived.
- `adjust_stock`: changes stock without ever letting it go below zero.

They fail with a short error code as the message (`out_of_stock`,
`variant_not_found`, …) and a readable explanation in the details, so the
service can turn them into a sensible reply. Only `service_role` can call
them.

This service uses the **service_role** key, since it's a trusted backend
process — it bypasses Row Level Security intentionally (RLS is there to
protect staff-facing dashboard access, not this server).

### `services/llm_service.py`
The only file that talks to whichever LLM provider you're using
(GPT-5 mini, Claude Haiku, DeepSeek, etc). Takes a system prompt + message
history, returns the model's response — including any structured tool-call
output. Swapping providers means editing only this file.

### `services/conversation_service.py`
Tracks each customer's recent message history and any in-progress order
state (e.g. "we know size and color, still need address"). Note: the
current schema doesn't have a dedicated `conversations` or `messages`
table — this service can start with an in-memory or Redis-backed store for
simplicity, or you can extend the schema later with a `messages` table if
you want full conversation history persisted in Postgres.

### `models/schemas.py`
Pydantic models defining the shape of data moving through the app:
the incoming Telegram payload, an `OrderRequest` (mirrors the fields needed
for `orders` + `order_items`), and an `AgentDecision` (what the orchestrator
decided to do with a message).

### `agents/orchestrator.py`
The core decision loop:
1. Receive the parsed message + conversation history
2. Decide what context is needed (does this look like a stock question? an
   order? a complaint?)
3. Call `supabase_service.py` for any live data needed (e.g. stock levels)
4. Call `llm_service.py` with the assembled context
5. Act on the result: reply directly, write an order via `supabase_service.py`,
   or flag for staff via `telegram_service.py`

### `agents/prompts.py`
System prompt templates — the store's persona, rules, and how the catalog
data should be formatted when injected into the prompt.

### `agents/tools.py`
Structured tool/function definitions the LLM can invoke, each one
corresponding to a `supabase_service.py` function:

- `check_stock(product_name, color, size)` → reads `product_variants`
- `create_order(customer_info, items)` → writes `customers`, `orders`,
  `order_items`
- `escalate_to_staff(reason)` → no database write required; triggers a
  Telegram notification to the store's staff group

### `utils/logging.py`
Structured logging for every step — which store, which customer, which
model handled the request, and the outcome. This is also where you'd track
per-model performance if you're comparing providers, as discussed earlier.

## Environment variables (`.env.example`)

```
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
LLM_PROVIDER=        # e.g. openai, anthropic, deepseek, gemini
LLM_MODEL=           # e.g. gpt-5-mini
LLM_API_KEY=
PUBLIC_BASE_URL=     # public HTTPS URL of this server, for Telegram webhooks
LOG_LEVEL=           # DEBUG, INFO, WARNING, ERROR, CRITICAL
```

## Notes on future schema extensions

Two things discussed that aren't in the current schema but may be worth
adding later, once the backend structure above is running:

- A `messages` table, if you want full conversation history persisted in
  Postgres rather than in-memory/Redis (useful for auditing or analytics)
- An `escalations` table, if you want structured tracking of
  staff-handled cases rather than relying on Telegram group messages alone
