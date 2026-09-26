# Backend Build Plan

A step-by-step plan for building the backend of the Cloth Store AI Platform.
Claude Code: read this whole file before doing anything.

---

## 1. The big picture

Each clothing or shoe store connects its own Telegram bot. When a customer
sends a message, this is what happens:

1. **Telegram calls our server** at `POST /api/v1/webhook/{store_id}`.
2. **We answer Telegram right away** ("got it"), then do the real work in
   the background so Telegram isn't kept waiting.
3. **We load the conversation** so far and check live stock and prices in
   Supabase.
4. **We ask the AI model** what to do. It can reply, save an order, or pass
   the customer to the store's staff.
5. **We send the reply** back to the customer on Telegram.

One backend serves many stores, so keeping each store's data separate is
the most important rule in this project.

**Source files to read first:**

- `README.md` — what the platform does
- `docs/backend-architecture.md` — the folder structure and what each file does
- `db/migrations/001_init_schema.sql` — the database (already applied in
  Supabase)

If the architecture doc and this plan disagree, this plan wins, and the
doc gets updated to match.

---

## 2. About me and how to work with me

- This is my first project using AI model APIs.
- Explain what you're doing and why, in plain language.
- Before using a new idea (async, Pydantic, webhooks, tool calling,
  background tasks, …), give me a short explanation first.
- Quality and understanding matter more than speed.

---

## 3. Tools we use

| What | Tool |
|------|------|
| Language | Python 3.10 (decision D1) |
| Web framework | FastAPI, async |
| Database | Supabase (Postgres) via the official `supabase` client |
| Calling Telegram | `httpx` |
| Data shapes and settings | Pydantic v2, `pydantic-settings` |
| AI model | Start with OpenAI GPT-5 mini; must be easy to switch to Claude Haiku, Gemini, or DeepSeek |
| Tests | `pytest` |

Always check current library versions instead of relying on memory, and
tell me if an API has changed.

---

## 4. Ground rules

**How we work**

1. **One phase at a time.** Finish it, then stop.
2. **End every phase with a short report:** what was built, which files
   changed, how I can test it myself, what I need to do by hand (env vars,
   running a migration), and a suggested commit message. Then wait for me
   to say "continue".
3. **Ask before deciding** anything about business logic or the database.
   Open questions live in the Decisions table at the end.
4. **Git:** all backend work is committed and pushed to the
   `backend-scaffold` branch, never `main`.

**Safety**

5. **No real secrets in files.** Only placeholders in `.env.example`, and
   `.env` stays in `.gitignore`.
6. **Never edit `001_init_schema.sql`.** Database changes go in new
   numbered files (`002_…`, `003_…`) that I run in the Supabase SQL Editor.

**Code design**

7. Follow the folder structure in `docs/backend-architecture.md`. Propose any
   change and explain why before making it.
8. **One job per service:**
   - only `supabase_service.py` talks to the database
   - only `llm_service.py` talks to the AI model
   - only `telegram_service.py` talks to Telegram
9. **Keep stores separate.** The backend uses Supabase's admin key, which
   skips the database's security rules, so our code does the protecting:
   - every database function takes `store_id` and filters by it
   - `store_id` always comes from the webhook URL, never from the AI
10. **The AI never sets prices.** Prices always come from the database
    (the variant's `price_override`, otherwise the product's `base_price`).
11. **All-or-nothing actions happen in the database.** Things like "create
    the order and reduce stock" must fully succeed or fully fail, so they
    run inside one Postgres function.

---

## 5. The phases

Each phase has a goal, what we build, and how I check it works.

### Phase 0 — Understand and plan ✅ Done

---

### Phase 1 — Project skeleton ✅ Done

**Goal:** a server that starts and answers a health check.

Already done: folder structure, `requirements.txt`, `.env.example`,
`.gitignore`, `main.py`, `GET /api/v1/health` with a test.

Still to do:
- Add `LLM_MODEL`, `PUBLIC_BASE_URL` and `LOG_LEVEL` to the settings and
  to `.env.example`.
- Make logs structured, so each line can show the store, customer, model
  and outcome.
- A step-by-step guide: create the virtual environment, install packages,
  run the server.

**Check:** I start the server and `/api/v1/health` returns OK.

---

### Phase 2 — Database updates (migration 002) — written, waiting for Supabase run

**Goal:** get the database into its final shape before writing code that
depends on it, so nothing has to be redone later.

What goes in `002_platform_updates.sql` (each part explained to me first):

1. **Fix the security-rule loop.** The `store_staff` rule reads its own
   table, which makes Postgres fail. A small helper function fixes it for
   all tables.
2. **New store settings:** `staff_chat_id` (staff's Telegram group),
   `webhook_secret` (proves messages really come from Telegram),
   `is_active` (turn a store off).
3. **Hide secrets from the staff dashboard:** staff must not be able to
   read the bot token or webhook secret.
4. **Product variant fixes:** keep `store_id` correct if a variant moves to
   another product, and make it required.
5. **Allowed values** for order status, payment status, payment method and
   staff role (from decisions D4–D7).
6. **Missing indexes** to keep lookups fast.
7. **Order delivery details** (address, phone, currency) if needed, based
   on D4 and D5.
8. **`place_order` function:** in one step it checks the items belong to
   the store, reads prices, checks stock, creates the order and its items,
   calculates the total, and reduces stock if D3 says so. If anything is
   wrong (e.g. out of stock), nothing is saved.
9. **`adjust_stock` function:** changes stock safely and never lets it go
   below zero.

**Check:** I run the migration, then a short SQL checklist confirms it
worked, including placing a test order.

---

### Phase 3 — Data models

**Goal:** define the shape of every piece of data moving through the app.

- Models matching the database tables: Store, Product, ProductVariant,
  Customer, Order, OrderItem, Payment.
- A model for incoming Telegram messages (only the fields we need).
- Internal models: `IncomingMessage`, `OrderDraft` (an order still being
  filled in) and `AgentDecision`.

**Check:** tests showing good data is accepted and bad data is rejected.

---

### Phase 4 — Database service

**Goal:** one place for all database reads and writes.

- Connect to Supabase once when the server starts.
- Functions, all scoped to one store:
  - `get_store` (ignores switched-off stores)
  - `search_variants` (by name, color, size; with stock and price)
  - `get_or_create_customer`, `update_customer`
  - `create_order` (uses `place_order`)
  - `get_customer_orders` (for "where is my order?")
  - `update_stock` (uses `adjust_stock`)
  - `record_payment`
- Clear errors for "not found", "out of stock" and database problems.

**Check:** a script or tests I run against my test store, plus a guide for
adding sample products and variants.

---

### Phase 5 — Telegram connection (echo bot)

**Goal:** real Telegram messages reach our server and get a reply.

- Verify each request really came from Telegram (secret token check).
- `telegram_service.py`: read incoming messages, send replies, notify
  staff, register a store's webhook.
- The webhook checks the store exists and is active, answers Telegram
  immediately, and processes the message in the background.
- For now, the bot just repeats the customer's message back.
- A small script to connect a store's bot to our server.

**Check:** with a guide to ngrok (makes my local server reachable from the
internet), I message my bot and see my message echoed back.

---

### Phase 6 — AI model service

**Goal:** talk to the AI model in a way that makes switching providers easy.

- One common interface: send instructions, conversation history and
  available tools; get back a reply and/or tool requests, plus token usage.
- OpenAI version first.
- A **fake** version for tests, so tests cost nothing.
- Provider and model chosen from settings.
- Timeouts, automatic retries, and a log of tokens and model for each call.
- An explanation of how to add another provider later (not built yet).

**Check:** a script sends one test message in English and one in Amharic
and prints the replies and token usage.

---

### Phase 7 — Conversation memory

**Goal:** the bot remembers each customer's conversation.

Memory is stored in the database, not in the server's memory, because
server memory is wiped on restart and later phases need information that
must survive (is the bot paused? did we already handle this message?).

- Proposed migration `003_conversations.sql` (I approve it first, D8):
  - `conversations`: one per customer per store, holding the order in
    progress and whether the bot is paused
  - `messages`: the chat history
  - `processed_updates`: messages already handled, so Telegram resends are
    ignored
- `conversation_service.py`: a database version for real use and an
  in-memory version for tests.
- Only the most recent messages are sent to the AI; old conversations
  expire.

---

### Phase 8 — The agent

**Goal:** the bot actually helps customers and takes orders.

- **Instructions for the AI (`prompts.py`):** store name, friendly and short
  tone, only mention products that exist, never invent prices or stock,
  pass to staff when unsure, how to collect an order. Reply in the
  customer's language (Amharic or English), and translate product requests
  into catalog terms before searching.
- **Tools the AI can use (`tools.py`):**
  - `check_stock` — look up products, colors, sizes
  - `update_order_draft` — save details as the customer gives them
  - `confirm_order` — only when everything is filled in and the customer
    said yes
  - `check_order_status`
  - `escalate_to_staff` — hand over to a human with a reason and summary

  The AI never supplies the store, customer or prices; our code fills
  those in.
- **The loop (`orchestrator.py`):** ask the AI → run any tools it asks for →
  give it the results → repeat until it has a final answer, with a maximum
  number of rounds as a safety limit.
- Replace the echo bot with the agent.

**Check:** a full conversation in Telegram: ask about a product, pick a
size, give contact details, confirm the order, then see the order in
Supabase and the stock change.

---

### Phase 9 — Handing over to staff

**Goal:** staff can take over smoothly when the bot can't help.

- Send the staff group a clear summary: who the customer is, what they
  want, and why it was handed over.
- Pause the bot for that customer so the bot and a human don't both reply.
  How it resumes is decision D9.
- Staff endpoints in `admin.py`: resolve a handover, confirm a payment,
  resume the bot. Only logged-in staff of that store can use them.

---

### Phase 10 — Making it robust

**Goal:** the bot behaves well when things go wrong.

- If the AI or database fails, the customer gets a polite message and
  staff are told. The bot never goes silent.
- Ignore duplicate messages Telegram resends.
- Limit how fast one customer can send messages, to stop spam and keep AI
  costs under control.
- Handle photos, stickers and voice notes politely.
- Tests for the agent using the fake AI model.
- A test proving one store can never see another store's data.

---

### Phase 11 — Ready to deploy

**Goal:** everything needed to put it online.

- A `Dockerfile`.
- Update `README.md`: setup, running locally, running tests.
- 2–3 low-cost hosting options with pros and cons. Nothing gets deployed
  without asking me.

---

## 6. Decisions

Answer each before the phase listed, and record the answer here.

| # | Question | Needed by | Answer |
|---|----------|-----------|--------|
| D1 | Upgrade to Python 3.11+ (currently 3.10.11) or stay on 3.10? | Phase 1 | Stay on 3.10 |
| D2 | Move `backend-architecture.md` into a `docs/` folder? | Phase 1 | Yes — now `docs/backend-architecture.md` |
| D3 | Reduce stock when the order is placed, or when staff confirm payment? | Phase 2 | When staff confirm payment |
| D4 | Delivery, pickup, or both? Save the address and phone on each order? | Phase 2 | Both, chosen per order; address and phone saved on each order |
| D5 | Which currency (ETB?), and save it on orders? | Phase 2 | ETB only; not saved on orders |
| D6 | Which payment methods (Telebirr, bank transfer, cash on delivery, …)? | Phase 2 | `telebirr`, `bank_transfer`, `cash_on_delivery`, `cash_in_store` |
| D7 | Which order stages (e.g. pending → confirmed → shipped → delivered, or cancelled)? | Phase 2 | `pending` → `confirmed` → `ready_for_pickup` / `out_for_delivery` → `completed`, or `cancelled` |
| D7b | Which payment statuses? | Phase 2 | `unpaid`, `pending_verification`, `paid`, `refunded` |
| D8 | Approve the `003_conversations.sql` tables? | Phase 7 | |
| D9 | How does the bot resume after a handover (staff command, button, time limit)? | Phase 9 | |

---

## 7. Where to start

Phase 1 is done. Next is **Phase 2** (needs decisions D3–D7 first) — no
coding until I say "continue".
