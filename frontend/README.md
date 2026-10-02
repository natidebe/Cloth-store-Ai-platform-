# Store dashboard (Telegram Mini App)

The shop owner's and staff's dashboard, opened inside Telegram (Phase 10b).
Built from the design in `dashboard ui design/`; the backend API it calls is
in `docs/mini-app-api.md`.

## Run it

```bash
npm install          # once
npm run build        # -> dist/, which the backend serves at /app/
```

Then restart the backend and open the dashboard from Telegram: send
`/dashboard` to a store's bot (as a member of its staff group), or open the
platform bot. The backend serves `frontend/dist`; until it's built, `/app/`
shows a small placeholder page.

Other commands:

| Command | What it does |
|---|---|
| `npm run dev` | Development server on http://localhost:5173/app/ (proxies `/api` to the backend on :8000). Outside Telegram there's no login, so the screens show "Open this from Telegram"; use it for layout work, or test inside Telegram with an ngrok address |
| `npm test` | All tests (Vitest + Testing Library, the backend mocked with MSW) |
| `npm run lint` | ESLint (TypeScript, React hooks rules) |
| `npm run typecheck` | TypeScript, strict |
| `npm run format` | Prettier |

Needs Node.js 22.13 or newer.

## How it's built

| Concern | Choice |
|---|---|
| UI | React 19 + TypeScript (strict), built with Vite |
| Server state | **TanStack Query**: every read is a query (`src/api/queries.ts`, keys in `keys`); every change a mutation that refreshes exactly what it changed. Screens never call `fetch` themselves |
| App state | **Zustand**: the language choice (remembered on the phone), the stock-grid draft (unsaved edits across the grid screen), toasts (`src/state/`) |
| Screens | React Router (data router, each screen its own lazily-loaded chunk); unsaved grid edits ask before leaving |
| Forms | react-hook-form + zod validation |
| Languages | i18next: `src/i18n/en.ts` and `am.ts` (TypeScript checks both have the same keys). Default: the Telegram app's language; Settings → Language changes it |
| Telegram | `src/lib/telegram.ts`, a typed wrapper over Telegram's official `telegram-web-app.js`: login data, Back button, theme, haptics, confirm dialogs |
| Styling | CSS Modules + design tokens (`src/styles/global.css`), light and dark following Telegram's theme |
| Tests | Vitest + Testing Library; MSW mocks the backend; tests open the real app at a route |

## Folders

```
src/
  api/          client (Telegram login header, ApiError), endpoints, queries (TanStack Query), types
  app/          App, router, store/platform shells, access screens (not in group, error)
  components/   shared UI (buttons, fields, stepper, sheet, badges...) and icons
  features/
    products/   list + quick stock, product details, stock & prices grid
    orders/     analytics (today / 7 / 30 days) + the order list (view only)
    settings/   settings, store profile, connect group/channel, change bot
    platform/   create store, waiting for approval, platform admin
  i18n/         English and Amharic texts
  lib/          telegram, theme, formatting, photo resizing, color swatches
  state/        Zustand stores
  styles/       design tokens
  test/         MSW server, fixtures, render helper
```

## Who sees what (D42)

- **Owner** (the store's creator, or an admin of its staff group): everything.
- **Staff** (members of the staff group): analytics, orders, products and
  stock (add products, change stock and sizes, edit details), but no prices
  and no settings (shown locked, as in the design).
