/**
 * After `vite build`: render each language to static HTML.
 *   dist/index.html     English (the default)
 *   dist/am/index.html  Amharic
 *   dist/sitemap.xml    when SITE_URL is set (e.g. SITE_URL=https://storefront.et)
 * The pages show fully before any JavaScript loads (slow mobile data, search
 * engines, Telegram link previews); React then takes them over.
 */
import { mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const dist = resolve(root, 'dist');
const ssr = resolve(root, '.ssr');
const siteUrl = process.env.SITE_URL ? process.env.SITE_URL.replace(/\/+$/, '') : null;

const { render, langs } = await import(pathToFileURL(resolve(ssr, 'entry-server.js')).href);
const template = await readFile(resolve(dist, 'index.html'), 'utf8');
const paths = { en: '/', am: '/am/' };

for (const lang of langs) {
  const { html, head } = render(lang, siteUrl);
  const page = template
    .replace('<html lang="en">', `<html lang="${lang}">`)
    .replace('<!--app-head-->', head)
    .replace('<!--app-html-->', html);
  if (!page.includes(html) || !page.includes(`lang="${lang}"`)) {
    throw new Error(`the template is missing a placeholder (${lang})`);
  }
  const file = resolve(dist, `.${paths[lang]}index.html`);
  await mkdir(dirname(file), { recursive: true });
  await writeFile(file, page);
  console.warn(`wrote ${file.slice(root.length + 1)}`);
}

if (siteUrl) {
  const urls = langs.map((lang) => `  <url><loc>${siteUrl}${paths[lang]}</loc></url>`).join('\n');
  await writeFile(
    resolve(dist, 'sitemap.xml'),
    `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls}\n</urlset>\n`,
  );
  await writeFile(
    resolve(dist, 'robots.txt'),
    `User-agent: *\nAllow: /\nSitemap: ${siteUrl}/sitemap.xml\n`,
  );
}

await rm(ssr, { recursive: true, force: true });
