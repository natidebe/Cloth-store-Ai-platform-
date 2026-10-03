import { Store } from 'lucide-react';

import { site, startLink, supportLink } from '@/config';
import { pathOf, useContent, useLang, type Lang } from '@/content';
import ui from '@/styles/ui.module.css';

import s from './layout.module.css';

export function Logo({ small }: { small?: boolean }) {
  return (
    <span className={`${s.logo} ${small ? s.logoSmall : ''}`}>
      {!small && (
        <span className={s.logoMark} aria-hidden="true">
          <Store size={20} strokeWidth={2.2} />
        </span>
      )}
      <span>
        StoreFront<span className={s.logoEt}>.et</span>
      </span>
    </span>
  );
}

const LANGS: { lang: Lang; label: string }[] = [
  { lang: 'am', label: 'አማርኛ' },
  { lang: 'en', label: 'English' },
];

export function Header() {
  const t = useContent();
  const lang = useLang();
  return (
    <header className={s.header}>
      <a className={s.skip} href="#main">
        {t.nav.skip}
      </a>
      <div className={`${ui.container} ${s.headerInner}`}>
        <a href={pathOf[lang]} className={s.home} aria-label={site.name}>
          <Logo />
        </a>
        <nav className={s.nav} aria-label={site.name}>
          <a href="#how">{t.nav.how}</a>
          <a href="#features">{t.nav.features}</a>
          <a href="#pricing">{t.nav.pricing}</a>
          <a href="#faq">{t.nav.faq}</a>
        </nav>
        <div className={s.headerEnd}>
          <div className={s.langs} role="group" aria-label={t.nav.language}>
            {LANGS.map((item) => (
              <a
                key={item.lang}
                href={pathOf[item.lang]}
                hrefLang={item.lang}
                lang={item.lang}
                className={`${s.lang} ${item.lang === lang ? s.langOn : ''}`}
                aria-current={item.lang === lang ? 'page' : undefined}
              >
                {item.label}
              </a>
            ))}
          </div>
          <a className={`${ui.button} ${ui.primary} ${s.headerStart}`} href={startLink}>
            {t.nav.start}
          </a>
        </div>
      </div>
    </header>
  );
}

export function Footer() {
  const t = useContent();
  const links = [
    { href: site.guideUrl, label: t.footer.guide },
    { href: '#pricing', label: t.footer.pricing },
    { href: site.privacyUrl, label: t.footer.privacy },
    { href: site.termsUrl, label: t.footer.terms },
    { href: site.statusUrl, label: t.footer.status },
  ].filter((link): link is { href: string; label: string } => link.href !== null);
  return (
    <footer className={s.footer}>
      <div className={`${ui.container} ${s.footerInner}`}>
        <div>
          <Logo small />
          <p className={s.footerText}>{t.footer.tagline}</p>
          <p className={s.footerText}>
            {t.footer.support}{' '}
            <a href={supportLink} className={s.footerLink}>
              @{site.support}
            </a>
          </p>
        </div>
        <nav className={s.footerLinks} aria-label={t.footer.tagline}>
          {links.map((link) => (
            <a key={link.label} href={link.href} className={s.footerLink}>
              {link.label}
            </a>
          ))}
        </nav>
      </div>
      <p className={`${ui.container} ${s.copyright}`}>{t.footer.copyright}</p>
    </footer>
  );
}
