# Putting it online (Phase 11)

How to run the platform on a real server instead of a laptop + ngrok, what
it costs, and the checklist before real shops use it. **Nothing is deployed
yet**: this is the plan to choose from (BUILD_PLAN.md: "nothing gets deployed
without asking me").

Prices checked in October 2026 (sources at the end). They change often:
check the provider's page before paying.

---

## 1. What the server has to do

- Run **one** container from the `Dockerfile` (backend + the built Mini App).
  **Only one**: customer locks and spam limits live in memory (Phases 7, 10).
- Be **always on**: Telegram sends every message to it, and a sweep runs
  every minute (inbox recovery, staff hand-back, channel posts).
- Have a public **https** address (Telegram requires it).
- About **512 MB RAM** is enough to start (FastAPI + Supabase + AI client).

The database stays on **Supabase** (it's not part of the server).

---

## 2. Three options

| | **A. Render** (Starter) | **B. Fly.io** | **C. Hetzner VPS** (CX23) |
|---|---|---|---|
| Price / month | **$7** (0.5 vCPU, 512 MB) | pay per use: about **$2–5** for one small always-on machine (the smallest, 256 MB, is $2.19; this app wants 512 MB) | **€5.99** (2 vCPU, 4 GB, 40 GB disk) |
| How you deploy | Connect GitHub; it builds the `Dockerfile` on every push to the chosen branch | `fly deploy` from your computer (their CLI) | You set up the server: Docker, a web server for https (Caddy), updates |
| https address | Automatic (`*.onrender.com`, or your domain) | Automatic (`*.fly.dev`, or your domain) | You set it up (Caddy does it automatically with a domain) |
| Logs, restarts | In the dashboard; restarts on crash | `fly logs`; restarts on crash | You (Docker restarts it; logs with `docker logs`) |
| Effort | **Lowest** | Low–medium | **Highest** (you are the system admin) |
| Watch out | Not the free plan: it sleeps after 15 minutes idle. Prices changed twice in 2026 | Usage billing: the bill moves with use; card required | Security updates and backups are your job |
| Best for | **The pilot** | Small and cheap, if you're comfortable with a CLI | Later, many stores, lowest price per resource |

**Railway** (Hobby, $5/month minimum + usage) also works like Render, but the
bill depends on use: an always-on 512 MB service ends up above the $5
minimum. Render's fixed price is easier to plan.

### Plus the database: Supabase Pro, **$25/month**

The free plan **pauses the project after 7 days without activity** and has
**no automatic backups**. For real orders, use Pro: no pausing, daily
backups kept 7 days.

### Plus the AI

Gemini (or OpenAI) is billed by Google/OpenAI per use. The daily limit per
store (`AI_DAILY_CALLS_PER_STORE`, default 300) caps it; buttons and the
order flow use no AI.

### My recommendation for the pilot

**Render Starter ($7) + Supabase Pro ($25) ≈ $32/month**, plus the AI.
It's the least work and the least that can go wrong while you test with real
shops. Move to Hetzner later if the bill matters more than the effort.

---

## 3. Deploying on Render (option A), step by step

**Chosen for the pilot (October 2026).** The service is described in
`render.yaml` (a Render "Blueprint"): Docker, Starter plan, Frankfurt, one
instance, health check, auto-deploy from `main`. Secrets are typed into
Render's form, never committed.

1. **Push both branches**, then **merge them into `main`** on GitHub
   (Pull requests → New: `backend-scaffold` → `main`, merge; then
   `Front-end` → `main`, merge). They merge without conflicts.
2. **New secrets first** (§4, item 1), so the server starts with the new ones.
3. Render → **New → Blueprint** → connect GitHub → choose this repository.
   Render reads `render.yaml` and asks for the secret values:

   | Variable | Value |
   |---|---|
   | `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | Supabase → Project Settings → API (the new key) |
   | `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY` | e.g. `gemini`, your model, the new key |
   | `PUBLIC_BASE_URL` | leave a placeholder for now (e.g. `https://example.com`), see step 5 |
   | `CATALOG_WEBHOOK_SECRET` | a new long random text |
   | `PLATFORM_BOT_TOKEN`, `SUPPORT_USERNAME` | the platform bot's token; your support username (no @) |

   Apply → Render builds the image (a few minutes) and starts it.
4. Open `https://<service>.onrender.com/api/v1/health` → `{"status":"ok"}`.
5. Set `PUBLIC_BASE_URL` to that exact address (Render → the service →
   Environment), save: it redeploys.
6. **Stop the server on your computer and ngrok.** Only ONE copy may run
   against the database (two would both handle messages and run the
   sweeps).
7. **Point every bot at Render**, from your computer: put the Render
   address and the new keys in `backend/.env`, then
   `python -m scripts.connect_all`, and check with `--check`. (Or in Render
   → Shell: `cd /srv/backend && python -m scripts.connect_all`.)
8. **Supabase → Database → Webhooks**: in both catalog webhooks, change the
   URL to `https://<service>.onrender.com/api/v1/catalog/webhook` and the
   `X-Webhook-Secret` header to the new `CATALOG_WEBHOOK_SECRET`.
9. **Test**: message a store's bot, place an order, confirm the payment in
   the staff group, open `/dashboard`, change a stock number and see the
   channel post update, open the platform bot.
10. **Uptime monitor** (§4, item 4).

Later updates: merge into `main` → Render builds and restarts by itself.
Logs: Render → the service → Logs.

---

## 4. Before real shops use it (go-live checklist)

1. **Replace the secrets that were exposed** during development (they were
   in screenshots and chat):
   - Supabase: create a new secret/service key (Project Settings → API),
     put it in the server's settings, then disable the old one.
   - The AI key (Google AI Studio / OpenAI): create a new one, delete the old one.
   - `CATALOG_WEBHOOK_SECRET`: a new one, in both the server and Supabase's webhooks.
   - Each store's webhook secret: `python -m scripts.connect_store "<store>" --new-secret`.
   - Bot tokens shown in screenshots: in @BotFather, `/revoke` → new token
     → Mini App Settings → Change bot (same bot, it just reconnects).
2. **Supabase Pro** (no pausing, daily backups).
3. **All migrations** 001–011 have been run (in order).
4. **Monitoring** (free): an uptime check on `https://<address>/api/v1/health`
   every 5 minutes (UptimeRobot or Better Stack) that alerts you on
   Telegram or by email when it's down.
   **Error tracking** (Sentry, free Developer plan): sentry.io → create a
   project (platform: FastAPI) → copy its **DSN** (Project Settings →
   Client Keys) → Render → Environment → `SENTRY_DSN` = the DSN → save
   (Render redeploys). Every crash, 5xx and `logger.error` then arrives in
   Sentry with the exact error, grouped, with the commit that caused it;
   it emails you by default (Alerts → add a rule for "a new issue" for
   instant mail). Nothing secret is sent: no request bodies, no local
   variables, and bot tokens, keys, Mini App login data and customers'
   phone numbers are removed (`app/core/monitoring.py`). Empty DSN: off.
5. **One instance only**, always on.
6. Tell every shop: **check the money arrived** in Telebirr/the bank before
   pressing Confirm payment (screenshots can be fake).
7. You (platform admin) are in `platform_admin_telegram`, and
   `SUPPORT_USERNAME` is set (the "Contact support" button).

---

## 5. Running the image yourself

```bash
docker build -t cloth-store .                              # from the repo root, on main
docker run --env-file backend/.env -p 8000:8000 cloth-store
```

Then `http://localhost:8000/api/v1/health`. For Telegram it still needs an
https address in front (ngrok, or the host).

---

## Sources (October 2026)

- Render: [makerkit.dev](https://makerkit.dev/pricing-calculator/render), [srvrlss.io](https://www.srvrlss.io/provider/render/), [bex.co](https://bex.co/blog/2026/09/04/render-price-changes-cost-sheet), [livemy.app](https://livemy.app/blog/render-pricing)
- Fly.io: [fly.io/pricing](https://fly.io/pricing/), [github.com/robhunter/agentdeals#2247](https://github.com/robhunter/agentdeals/issues/2247), [bex.co](https://bex.co/blog/2026/08/16/flyio-pure-usage-pricing-always-on-app-cost)
- Hetzner: [vpsfor.dev](https://vpsfor.dev/posts/hetzner-cx22-pricing-2026/), [cloudhim.com](https://www.cloudhim.com/cloud-costs/hetzner-cx22-pricing-2026), [northflank.com](https://northflank.com/blog/hetzner-cloud-server-price-increases)
- Railway: [budgetforge.dev](https://www.budgetforge.dev/tools/railway-pricing-2026), [temps.sh](https://temps.sh/blog/railway-pricing-2026)
- Supabase: [supabase.com/pricing](https://Supabase.io/pricing), [jetadmin.io](https://www.jetadmin.io/blog/supabase-pricing-2026-guide-to-plans-limits-and-real-world-costs/)
