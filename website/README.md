# StoreFront.et website

Lives on its own branch, **`Website`** (not merged into `main`, which is the bot
server Render deploys).

The promotional website, built with React from the design in `design/`
(kept out of git) and the text in `docs/website-content.md`.

- **Amharic** at `/`, **English** at `/en/`.
- `npm run build` renders both pages to plain HTML (`dist/`), so they show
  instantly on slow mobile data and in Google and Telegram link previews;
  React then takes over the page in the browser.
- No server needed: any static host can serve `dist/`.

## Run it

```bash
npm ci
npm run dev          # http://localhost:5174 (English: /en/)
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

## Publish it (Cloudflare Pages, free)

1. Cloudflare dashboard → Workers & Pages → Create → Pages → connect the
   GitHub repository.
2. Production branch **`Website`**; **root directory `website`**; build command
   `npm ci && npm run build`; output directory `dist`.
3. Environment variable `SITE_URL` = the site's address (e.g.
   `https://storefront.et`): adds the language links for Google, the sitemap
   and robots.txt.
4. Custom domain: Pages project → Custom domains.

Every push to the `Website` branch then republishes the site. Netlify or Vercel work the
same way (base directory `website`, publish directory `website/dist`).
