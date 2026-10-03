import { StrictMode } from 'react';
import { renderToString } from 'react-dom/server';

import { site } from '@/config';
import { contents, pathOf, type Lang } from '@/content';

import { App } from './App';

export const langs: Lang[] = ['am', 'en'];

/** One page as HTML, with what goes in its <head> (used at build time only). */
export function render(lang: Lang, siteUrl: string | null) {
  const html = renderToString(
    <StrictMode>
      <App lang={lang} />
    </StrictMode>,
  );
  const t = contents[lang];
  const absolute = (l: Lang) => (siteUrl ? `${siteUrl}${pathOf[l]}` : null);
  const head = [
    `<title>${escape(t.meta.title)}</title>`,
    `<meta name="description" content="${escape(t.meta.description)}" />`,
    `<meta property="og:type" content="website" />`,
    `<meta property="og:site_name" content="${escape(site.name)}" />`,
    `<meta property="og:title" content="${escape(t.meta.title)}" />`,
    `<meta property="og:description" content="${escape(t.meta.description)}" />`,
    `<meta property="og:locale" content="${lang === 'am' ? 'am_ET' : 'en_US'}" />`,
    `<meta name="twitter:card" content="summary" />`,
  ];
  // Links between the two languages need the site's address (SITE_URL).
  const url = absolute(lang);
  if (url) {
    head.push(
      `<link rel="canonical" href="${url}" />`,
      `<meta property="og:url" content="${url}" />`,
    );
    for (const other of langs) {
      head.push(`<link rel="alternate" hreflang="${other}" href="${absolute(other)}" />`);
    }
    head.push(`<link rel="alternate" hreflang="x-default" href="${absolute('am')}" />`);
  }
  return { html, head: head.join('\n    ') };
}

function escape(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}
