# Cloth Store AI Platform

A Telegram sales assistant for clothing and shoe shops. Each shop connects
its own Telegram bot and channel:

- **Customers** order from the shop's channel (🛒 Order on a post) or by
  chatting with the bot, in Amharic or English: color, size, quantity,
  delivery or pickup, a summary, and the payment instructions.
- **Staff** get every order, payment screenshot and hard question in their
  Telegram staff group, confirm payments with a button, and reply to
  customers from there.
- **Owners and staff** manage products, stock, prices and the shop's
  settings, and see sales, in a **Telegram Mini App** (`/dashboard`).
- **New shops** sign up in the platform bot; a platform admin approves them.

One server runs every shop. Built step by step in [`BUILD_PLAN.md`](BUILD_PLAN.md).

## The code

| Folder | What | Branch |
|---|---|---|
| `backend/` | FastAPI (Python): the bots' webhook, the order flow, staff group, channel posts, the Mini App's API | `backend-scaffold` |
| `frontend/` | The Mini App (React + TypeScript), see [`frontend/README.md`](frontend/README.md) | `Front-end` |
| `db/migrations/` | The database (Supabase/Postgres), run in order 001 → 011 | `backend-scaffold` |
| `docs/` | Architecture, [inventory](docs/inventory-management.md), [Mini App API](docs/mini-app-api.md), [deployment](docs/deployment.md) | `backend-scaffold` |

`main` gets both through pull requests and is what's deployed.

## Running it on your computer

You need Python 3.10+, Node.js 22.13+, a Supabase project, a Telegram bot
from @BotFather, an AI key (e.g. Gemini), and an https tunnel (ngrok).

1. **Database**: in the Supabase SQL Editor, run `db/migrations/001` … `011`
   in order (each file says what it does). Optional sample data: `db/seed/`.
2. **Backend**:
   ```bash
   cd backend
   python -m venv .venv
   .venv\Scripts\activate            # Windows (macOS/Linux: source .venv/bin/activate)
   pip install -r requirements.txt
   copy .env.example .env            # then fill it in (each setting is explained there)
   uvicorn app.main:app --reload     # http://localhost:8000/docs
   ```
3. **https for Telegram**: `ngrok http 127.0.0.1:8000`; put the https address
   in `.env` as `PUBLIC_BASE_URL` and restart.
4. **The Mini App** (on the `Front-end` branch or `main`):
   ```bash
   cd frontend
   npm install
   npm run build                     # the backend serves frontend/dist at /app/
   ```
5. **Connect the bots**: create a store (platform bot's Mini App, or
   `POST /api/v1/stores` in `/docs`), then `python -m scripts.connect_all`.

## Tests

```bash
cd backend && python -m pytest -q    # ~440 tests; the database ones run against your Supabase project
cd frontend && npm test              # Mini App (with the backend mocked)
```

## Scripts (`backend/`, `python -m scripts.<name>`)

| Script | What it does |
|---|---|
| `connect_all [--check]` | Points every store's bot and the platform bot at `PUBLIC_BASE_URL` (after a deploy or a new ngrok address) |
| `connect_store "<store>" [--info \| --new-secret \| --disconnect]` | The same for one store |
| `connect_platform_bot` | Sets up the platform bot (sign-up Mini App) |
| `publish_product "<store>" P101 \| --all \| --check` | Posts products to the store's channel |
| `login_token you@example.com` | A login token for trying the dashboard endpoints in `/docs` |
| `try_llm` | Checks the AI key and model |

## Putting it online

`Dockerfile` (repo root) builds one image with the backend and the Mini App.
Hosting options, costs and the go-live checklist: [`docs/deployment.md`](docs/deployment.md).
