# StoreFront.et website

Lives on its own branch, **`Website`** (not merged into `main`, which is the bot
server Render deploys).

The promotional website, built with React from the design in `design/`
(kept out of git) and the text in `docs/website-content.md`.

- **English** at `/` (the default), **Amharic** at `/am/`.
- `npm run build` renders both pages to plain HTML (`dist/`), so they show
  instantly on slow mobile data and in Google and Telegram link previews;
  React then takes over the page in the browser.
- No server needed: any static host can serve `dist/`.

## Run it

```bash
npm ci
npm run dev          # http://localhost:5174 (Amharic: /am/)
npm test             # tests
npm run build        # dist/; then `npm run preview` to look at the result
```

## Change things

| What | Where |
|---|---|
| Any text | `src/content/am.ts` and `src/content/en.ts` (the build fails if one language misses a piece) |
| Links: platform bot, support, demo channel, video, status page, guide, privacy, terms | `src/config.ts` |
| Colors, fonts | `src/styles/global.css` |

Links set to `null` in `src/config.ts` hide what needs them, so the site
never has a dead link: the demo section appears once `demoUrl` or `videoUrl`
is set; footer links appear once their page exists.

**Before going live:** the "Start free" buttons open `@Platform_16bot`. When
`@StoreFrontETbot` is created (and its token set as `PLATFORM_BOT_TOKEN` on
Render), change `platformBot` in `src/config.ts`.

## Publish it (Cloudflare Workers, free)

Cloudflare dashboard → Workers & Pages → Create → import the GitHub repository
as a Worker named `storefront-et` (the name in `wrangler.jsonc`). Its build
settings (the Worker → Settings → Build):

| Setting | Value |
|---|---|
| Git branch | **`Website`** |
| Root directory | **`website`** |
| Build command | `npx -y npm@11.11.0 ci && npm run build` |
| Deploy command | `npx wrangler deploy` |

- npm 11.11 is the npm that made the lock file (Node 22's own npm 10 refuses
  it); Node 22 comes from `.node-version`; `wrangler.jsonc` says to upload `dist/`.
- Builds for other branches: off (they have no `website/` folder).
- Optional, once the address is final: build variable `SITE_URL` = the site's
  address (e.g. `https://storefront.et`): adds the language links for Google,
  the sitemap and robots.txt.
- Custom domain: the Worker → Settings → Domains & Routes.

Every push to the `Website` branch then republishes the site.
