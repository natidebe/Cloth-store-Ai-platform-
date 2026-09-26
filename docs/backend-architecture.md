# Backend Architecture

This document describes how the FastAPI backend works: the business flow,
the message lifecycle, the folder structure, and how each part maps to the
database schema in `db/migrations/`.

---

## 1. What the backend does

Each store connects its own Telegram bot. Customers message the bot to ask
about stock and prices, place orders, and check order status. The backend:

1. Receives every customer message through a webhook
2. Runs the AI assistant, which answers questions and collects orders using
   real data from the database
3. Hands conversations to the store's staff when a person is needed
   (bargaining, complaints, payment checks)
4. Onboards new stores: creates the store, checks and connects its bot, and
   sets up its owner and staff (section 10)

The staff dashboard is a separate frontend. It talks to Supabase directly
using the `anon` key and Row Level Security for everyday work (products,
orders). Anything that touches the `stores` table or bot tokens (creating a
store, approving it, linking the staff group) goes through this backend,
because only the backend may write to `stores`.

---

## 2. Business flow (non-technical)

Example: a customer asks Selam Shoes for white Air Force 1 in size 42.

| Step | Who | What happens |
|------|-----|--------------|
| 1 | Customer | Asks "Do you have white AF1 in 42?" |
| 2 | Assistant | Checks real stock and replies with availability and price. If sold out, offers other sizes or colors |
| 3 | Assistant | Collects name, phone, and delivery address |
| 4 | Customer | Confirms the order summary |
| 5 | Assistant + Staff | Order is created as pending. Staff get an alert. Customer gets payment instructions |
| 6 | Customer | Pays by Telebirr or bank and sends a screenshot |
| 7 | Staff | Verifies the payment and marks the order as paid |
| 8 | Assistant | Tells the customer payment is received and when delivery is expected |
| 9 | Staff | Delivers the order |
| 10 | Assistant | Answers "where is my order?" questions |

**The assistant handles:** stock and price questions, order details,
payment instructions, order status.

**Staff handle:** payment confirmation, bargaining, complaints, returns,
delivery, and keeping stock numbers correct.

---

## 3. Business rules

These rules must be enforced **in code**, not only in the prompt.

1. **The assistant never confirms payments.** Only staff can mark an order
   as paid, through the admin endpoint. A customer saying "I paid" changes
   nothing.
2. **The assistant never invents stock or prices.** Every answer about
   availability or price must come from a `check_stock` result.
3. **The assistant never gives discounts.** Bargaining is escalated to staff.
4. **Orders are created as pending and unpaid.** They become confirmed only
   after staff verify payment.
5. **When staff take over a conversation, the assistant stays silent** until
   staff hand it back.
6. **Every query is scoped to one store.** The backend uses the
   service_role key, which bypasses Row Level Security, so the code must
   always filter by `store_id`.

---

## 4. Message lifecycle

```
Telegram
   │
   ▼
FastAPI Webhook
   ├─ Verify Telegram secret (per store)
   ├─ Find store
   ├─ Check store is active
   ├─ Check duplicate update (store_id + update_id)
   └─ Return 200 immediately
          │
          ▼
Background Job
   └─ Sequential per store + customer
          │
          ▼
Handoff Check
   └─ Staff in control → STOP (message is still saved)
          │
          ▼
Save incoming message
          │
          ▼
Load Context
   ├─ Recent conversation
   ├─ Current order draft
   ├─ Customer profile
   └─ Relevant order history
          │
          ▼
Orchestrator ◄──────────────────┐
   │                            │ tool result
   ▼                            │ (max ~5 rounds)
LLM Service ── tool call ──► Tools
   │                            ├─ check_stock
   │                            ├─ update_order_draft
   │                            ├─ confirm_order
   │                            ├─ check_order_status
   │                            └─ escalate_to_staff ──► Staff group
   ▼
Final AI Response
   │
   ▼
Did STAFF take over during this run?
   ├─ yes → don't send
   └─ no  → save bot reply → send Telegram reply

Any failure at any step → polite fallback to customer + alert to staff
```

### Step details

**Webhook.** Must respond fast. Telegram retries if it doesn't get a 200
quickly, so all slow work (database, LLM) happens in the background job.

**Duplicate check.** Telegram can resend the same update. The key is
`store_id + update_id`, because each bot numbers its updates separately.

**Sequential per customer.** If a customer sends three quick messages, they
are processed one after another, never in parallel, so the order draft is
never corrupted. An in-memory lock is fine while running one server. With
more than one server, this must move to Redis.

**Handoff check (first).** If staff control the conversation, the assistant
does nothing. The customer's message is still saved so staff see it.

**Save incoming message first.** The customer's message is saved before the
LLM runs, so it's never lost, even if the run fails or is stopped.

**Tool loop.** The LLM either replies or asks for a tool. The orchestrator
runs the tool and sends the result back. This repeats until a final reply,
with a limit of about 5 rounds to prevent runaway costs.

**Handoff check (second).** Staff may take over while the LLM is working.
The check asks "did *staff* take over during this run?", not just "is
handoff on?". Otherwise the assistant's own `escalate_to_staff` call would
block its "I'm connecting you with our team" message.

**Failure path.** If the LLM, database, or Telegram fails, the customer gets
a polite fallback message and staff get an alert. The bot never goes silent.

---

## 5. Tools

| Tool | What it does | Rules enforced in code |
|------|--------------|------------------------|
| `check_stock(query, color, size)` | Finds matching variants with stock and price | Only this store's products |
| `update_order_draft(fields)` | Saves item, size, color, name, phone, address as they're collected | Validates formats (e.g. phone) |
| `confirm_order()` | Creates the order from the draft (through the `place_order` database function) | All required fields present; customer confirmed; stock re-checked at this moment; prices read from the database; safe to call twice (one order only); status = pending, payment = unpaid |
| `check_order_status()` | Returns this customer's recent orders | Only this customer, only this store |
| `escalate_to_staff(reason, summary)` | Alerts the staff group and pauses the assistant for this customer | Sets handoff state |

There is no payment tool. Payment confirmation happens only through the
admin endpoint.

Tools never accept `store_id`, `customer_id`, or prices from the LLM. The
orchestrator fills those in from the webhook and the database.

---

## 6. Folder structure

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

### Separation rules

- Only `supabase_service.py` talks to the database
- Only `llm_service.py` talks to the LLM provider
- Only `telegram_service.py` talks to the Telegram API

---

## 7. File-by-file

### `main.py`
Creates the FastAPI app, registers routers, and initializes shared clients
(Supabase, HTTP client) once at startup.

### `core/config.py`
Loads platform-level environment variables. Store bot tokens are **not**
here; they live in the `stores` table.

### `core/security.py`
Verifies the Telegram secret header against the store's `webhook_secret`,
and protects admin endpoints: it verifies the staff member's Supabase login
token (`Authorization: Bearer ...`) and checks they have a `store_staff` row
for that store. There is no shared admin key.

### `api/v1/webhook.py`
`POST /api/v1/webhook/{store_id}`. Runs the webhook checks, starts the
background job, and returns 200 immediately. Messages from a group (not a
private chat) are only used for the `/link <code>` command that connects a
store's staff group (section 10); anything else from a group is ignored.

### `api/v1/admin.py`
Actions for staff (called from the dashboard):
- Confirm payment for an order
- Resolve an escalation and hand the conversation back to the assistant
- Retry a failed order

Store onboarding (section 10):
- Create a store (the caller becomes its owner)
- Invite and remove staff (owner only)
- Create a staff-group link code (owner only)
- Change the bot token (owner only, D17)
- Approve, suspend, change plan, list all stores (platform admin only)

### `api/v1/health.py`
`GET /api/v1/health` for uptime monitoring.

### `services/telegram_service.py`
Parses incoming updates (text, photos, stickers, voice), sends replies with
the correct store's bot token, sends staff alerts, and registers webhooks.
For onboarding it also checks a new bot token with Telegram (`getMe`), which
returns the bot's id and username.

### `services/supabase_service.py`
All database reads and writes, always scoped by `store_id`:

| Function | Tables |
|----------|--------|
| `get_store(store_id)` | `stores` (inactive stores return nothing) |
| `search_variants(store_id, query, color, size)` | `products`, `product_variants` |
| `get_or_create_customer(store_id, telegram_id, name)` | `customers` |
| `update_customer(store_id, customer_id, ...)` | `customers` |
| `create_order(store_id, customer_id, items)` | `place_order` database function |
| `get_customer_orders(store_id, customer_id)` | `orders`, `order_items` |
| `update_stock(store_id, variant_id, delta)` | `adjust_stock` database function (never below zero) |
| `record_payment(store_id, order_id, amount, method, staff_id)` | `confirm_payment` database function (reduces stock, saves payment, marks paid) |
| `is_duplicate_update(store_id, update_id)` | `processed_updates` |
| `get_handoff_state` / `set_handoff_state` | `conversations` |
| `create_store(name, bot_token, owner_user_id)` | `stores`, `store_staff` (section 10) |
| `set_store_status(store_id, status)` / `list_stores()` | `stores` (platform admin only) |
| `add_staff` / `remove_staff(store_id, ...)` | `store_staff` |
| `create_link_code(store_id)` / `use_link_code(code, chat_id)` | link codes, `stores.staff_chat_id` |

### `services/llm_service.py`
A provider-agnostic interface. Takes a system prompt, message history, and
tool definitions. Returns text and/or tool calls, plus token usage. The
provider and model come from `LLM_PROVIDER` and `LLM_MODEL`. Includes
timeouts and retries. Logs model, tokens, and cost for every call.

### `services/conversation_service.py`
Stores recent messages, the current order draft, and handoff state per
`(store_id, telegram_id)`. Stored in the database (migration 003) so it
survives restarts. An in-memory version exists only for tests.

### `models/schemas.py`
Pydantic models for the database tables, the incoming Telegram update, and
internal objects: `IncomingMessage`, `OrderDraft`, `AgentDecision`.

### `agents/orchestrator.py`
Runs the lifecycle in section 4: handoff checks, context loading, the tool
loop with its round limit, the final send, and the failure path.

### `agents/prompts.py`
The system prompt: store name, friendly and short tone, rules from
section 3, how to collect an order, and replying in the customer's language
(Amharic or English).

### `agents/tools.py`
Tool definitions from section 5 and the code that runs them.

### `utils/logging.py`
Structured logs with store, customer, model, tokens, and outcome for every
message.

---

## 8. Database changes needed

`001_init_schema.sql` is already applied and is never edited. Changes go in
new numbered migrations (see `BUILD_PLAN.md`, Phases 2 and 7).

**`002_platform_updates.sql`** (Phase 2):
- Fix the recursive `store_staff` security rule with an
  `is_store_member(store_id)` helper, used by every table's rules
- Add to `stores`: `staff_chat_id` (Telegram group for staff alerts),
  `webhook_secret` (per-store secret for verifying requests), `is_active`
  (switch a store on or off)
- Hide `telegram_bot_token` and `webhook_secret` from the staff dashboard
- Variant fixes: keep `store_id` correct when `product_id` changes; make it
  required
- Allowed values for order status, payment status, payment method, and
  staff role
- Missing indexes: `product_variants(store_id)`, `orders(customer_id)`
- Order delivery details (address, phone, currency) if needed
- `place_order` function: in one transaction, checks the items belong to
  the store, reads prices, checks stock, creates `orders` + `order_items`,
  calculates the total (stock is reduced at payment, not here)
- `adjust_stock` function: changes stock atomically, never below zero
- `confirm_payment` function: reduces stock, saves the payment, and marks
  the order paid in one step; refused if an item sold out

**`003_conversations.sql`** (Phase 7):
- `conversations` — one row per store + customer: current order draft,
  handoff state, who took over
- `messages` — full message history for staff context and auditing
- `processed_updates` — `(store_id, update_id)` with a unique constraint,
  for the duplicate check

**`004_store_onboarding.sql`** (Phase 9b):
- A way to mark platform admins (D15)
- Store status: `pending`, `active`, `suspended` (the bot answers only when
  active)
- One store per bot: the bot's Telegram id is saved and must be unique
- Short-lived staff-group link codes (store, code, expiry, used or not)

---

## 9. Environment variables

```
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
LLM_PROVIDER=          # openai, anthropic, gemini, deepseek
LLM_MODEL=             # e.g. gpt-5-mini
LLM_API_KEY=
PUBLIC_BASE_URL=       # used to register Telegram webhooks
LOG_LEVEL=INFO         # DEBUG, INFO, WARNING, ERROR, CRITICAL (uppercase)
```

There is no admin key: admin endpoints use the staff member's Supabase
login (see `core/security.py`).

---

## 10. Store onboarding

New stores join without anyone touching code or Supabase (BUILD_PLAN.md,
Phase 9b). Until that phase, the one test store is created by hand.

### The store owner's journey

| Step | Owner does (in the dashboard) | Backend does |
|------|-------------------------------|--------------|
| 1. Sign up | Creates an account (email + password) | Nothing: Supabase login, handled by the dashboard |
| 2. Create store | Enters the store name, pastes the bot token from @BotFather | Checks the token with Telegram (`getMe`); refuses a bot another store uses; saves the store with a generated `webhook_secret`; makes the user `owner` |
| 3. Approval | Sees "under review" (if D14 requires approval) | Store stays `pending`, so the bot answers no one |
| 4. Link staff group | Adds the bot to the staff Telegram group, sends `/link <code>` shown in the dashboard | Webhook receives the group message, checks the code (right store, not expired, not used), saves the group as `staff_chat_id` |
| 5. Add products | Fills in products, colors, sizes, stock, prices | Nothing: dashboard writes directly, allowed by the security rules |
| 6. Invite staff | Enters a staff member's email | Sends a Supabase invitation; adds them to `store_staff` as `staff` |
| 7. Go live | Nothing | When the store becomes `active`, registers the webhook with Telegram (`setWebhook` with the store's secret) |

### Platform admin

A platform admin (D15) sees every store with its status, plan, and number of
orders, and can **approve**, **suspend** (the bot stops answering and the
webhook is removed), and **change plan**. Platform admin endpoints check the
caller is a platform admin; being a store owner is not enough.

### Onboarding rules (enforced in code)

1. Only the backend writes to `stores`. The dashboard never sees a bot token
   or webhook secret after it is saved.
2. A bot token is saved only after Telegram confirms it is valid.
3. One bot belongs to one store.
4. The webhook secret is always generated by the backend, never typed.
5. A store's bot answers customers only while the store is `active`.
6. Only the owner can invite or remove staff, link the staff group, or
   change the bot token. Only a platform admin can approve or suspend.
7. Link codes are single-use and expire after a short time.

---

## 11. Open decisions

All decisions are tracked in the Decisions table in `BUILD_PLAN.md`:

- **Stock** goes down when staff confirm payment (D3, decided). Because
  nothing is reserved, unpaid orders don't need to expire.
- **How do staff hand a conversation back** to the assistant? (D9) A button
  in the dashboard, a command in the staff group, or automatically after a
  period of time.
- **Store onboarding** (D14–D18): do new stores need approval before going
  live? Who is a platform admin? Which plans exist and what do they limit?
  Can an owner change the bot token later? Can one person be in several
  stores?
