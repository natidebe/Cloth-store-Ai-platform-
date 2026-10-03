import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { site, startLink, supportLink } from '@/config';
import { am } from '@/content/am';
import { en } from '@/content/en';
import { langOfPath } from '@/content';

import { App } from './App';
import { render as renderPage } from './entry-server';

describe('the page', () => {
  it('is in Amharic, with every start button opening the platform bot', () => {
    render(<App lang="am" />);
    expect(screen.getByRole('heading', { level: 1, name: am.hero.title })).toBeInTheDocument();
    const starts = screen
      .getAllByRole('link')
      .filter((link) => link.getAttribute('href') === startLink);
    // Header, hero, the three plans, the final call to action.
    expect(starts).toHaveLength(6);
    expect(startLink).toBe(`https://t.me/${site.platformBot}?start=web`);
    expect(screen.getAllByRole('link', { name: `@${site.support}` })[0]).toHaveAttribute(
      'href',
      supportLink,
    );
  });

  it('shows the three real screenshots, described for screen readers', () => {
    render(<App lang="am" />);
    for (const alt of Object.values(am.hero.shots)) {
      expect(screen.getAllByRole('img', { name: alt })[0]).toHaveAttribute('src');
    }
    // The owner card shows the Analytics screen too; the staff card, Orders.
    expect(screen.getAllByRole('img', { name: am.hero.shots.analytics })).toHaveLength(2);
    expect(screen.getAllByRole('img', { name: am.hero.shots.orders })).toHaveLength(1);
  });

  it('is in English at /en/, and the language switch marks the current one', () => {
    render(<App lang="en" />);
    expect(screen.getByRole('heading', { level: 1, name: en.hero.title })).toBeInTheDocument();
    const switcher = screen.getByRole('group', { name: en.nav.language });
    expect(within(switcher).getByRole('link', { name: 'English' })).toHaveAttribute(
      'aria-current',
      'page',
    );
    expect(within(switcher).getByRole('link', { name: 'አማርኛ' })).toHaveAttribute('href', '/');
    expect(langOfPath('/en/')).toBe('en');
    expect(langOfPath('/')).toBe('am');
  });

  it('has the sections the menu points to, and no placeholder left', () => {
    const { container } = render(<App lang="en" />);
    for (const id of ['how', 'features', 'pricing', 'faq', 'main']) {
      expect(container.querySelector(`#${id}`)).not.toBeNull();
    }
    for (const link of screen.getAllByRole('link')) {
      const href = link.getAttribute('href') ?? '';
      expect(href).not.toBe('');
      if (href.startsWith('#')) expect(container.querySelector(href)).not.toBeNull();
    }
    expect(container.textContent).not.toContain('{bot}');
    expect(container.textContent).toContain(`@${site.platformBot}`);
  });

  it('hides the demo section and missing footer pages until they exist', () => {
    render(<App lang="en" />);
    expect(screen.queryByText(en.demo.title)).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: en.footer.privacy })).not.toBeInTheDocument();
    expect(
      within(screen.getByRole('contentinfo')).getByRole('link', { name: en.footer.pricing }),
    ).toHaveAttribute('href', '#pricing');
  });

  it('opens a question in the FAQ', async () => {
    const user = userEvent.setup();
    render(<App lang="en" />);
    const question = en.faq.items[0]!;
    const details = screen.getByText(question.q).closest('details')!;
    expect(details.open).toBe(false);
    await user.click(screen.getByText(question.q));
    expect(details.open).toBe(true);
    expect(within(details).getByText(question.a)).toBeVisible();
  });
});

describe('the built page head', () => {
  it('has the title and description in the page language', () => {
    const { head, html } = renderPage('am', null);
    expect(head).toContain(`<title>${am.meta.title}</title>`);
    expect(head).toContain(am.meta.description);
    expect(head).not.toContain('canonical'); // no address known yet
    expect(html).toContain(am.hero.title);
  });

  it('links the two languages once the address is known', () => {
    const { head } = renderPage('en', 'https://storefront.et');
    expect(head).toContain('<link rel="canonical" href="https://storefront.et/en/" />');
    expect(head).toContain('hreflang="am" href="https://storefront.et/"');
    expect(head).toContain('hreflang="x-default" href="https://storefront.et/"');
  });
});
