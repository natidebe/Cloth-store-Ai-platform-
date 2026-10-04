import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';

import type { AdminStore, Me, PlatformMe } from '@/api/types';
import { daysLeft, planState } from '@/lib/plan';
import { me, shopTypes, STORE_ID } from '@/test/fixtures';
import { renderApp } from '@/test/render';
import { server, storeApiBase } from '@/test/server';

/** Phase 14: plan end dates, Record payment, the owner's banner. */

const DAY = 24 * 60 * 60 * 1000;
const inDays = (days: number) => new Date(Date.now() + days * DAY).toISOString();

function adminMe(): PlatformMe {
  return {
    user: { id: 1, name: 'Nati', username: null, language_code: 'en' },
    is_platform_admin: true,
    stores: [],
    support_url: null,
    shop_types: shopTypes(),
  };
}

function adminStore(name: string, fields: Partial<AdminStore>): AdminStore {
  return {
    id: name,
    name,
    status: 'active',
    plan: 'basic',
    telegram_bot_username: `${name}_bot`,
    created_at: null,
    orders: 0,
    plan_ends_at: inDays(30),
    suspended_reason: null,
    ...fields,
  };
}

function ownerMe(fields: Partial<Me['store']>, role: Me['role'] = 'owner'): Me {
  const base = me(role);
  return { ...base, store: { ...base.store, plan: 'basic', ...fields } };
}

describe('the plan state', () => {
  it('counts days in Addis Ababa and knows soon, today, grace and paused', () => {
    expect(daysLeft(inDays(3))).toBe(3);
    expect(planState(inDays(20), null).kind).toBe('ok');
    expect(planState(inDays(5), null)).toEqual({ kind: 'soon', days: 5 });
    expect(planState(inDays(-1), null).kind).toBe('grace');
    expect(planState(inDays(-10), 'unpaid').kind).toBe('paused');
    expect(planState(null, null).kind).toBe('notStarted');
  });
});

describe('the platform admin', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/v1/platform-app/me', () => HttpResponse.json(adminMe())),
      http.get('/api/v1/platform-app/admin/stores', () =>
        HttpResponse.json([
          adminStore('later', { plan_ends_at: inDays(40) }),
          adminStore('soon', { plan: 'pro', plan_ends_at: inDays(3) }),
          adminStore('paused', {
            status: 'suspended',
            suspended_reason: 'unpaid',
            plan_ends_at: inDays(-5),
          }),
          adminStore('waiting', { status: 'pending', plan: 'free', plan_ends_at: null }),
        ]),
      ),
    );
  });

  it('sees each shop’s plan and days left, ending soonest first', async () => {
    const { user } = renderApp('/platform/admin');
    expect(await screen.findByText(/Pro · until .* · 3 days left/)).toBeInTheDocument();
    expect(screen.getByText('Basic · paused: didn’t pay')).toBeInTheDocument();
    expect(screen.getByText('Trial starts when approved')).toBeInTheDocument();
    const names = screen.getAllByText(/^(later|soon|paused|waiting)$/).map((el) => el.textContent);
    expect(names).toEqual(['paused', 'soon', 'later', 'waiting']);

    await user.click(screen.getByRole('radio', { name: 'Ending soon' }));
    const ending = screen.getAllByText(/^(later|soon|paused|waiting)$/).map((el) => el.textContent);
    expect(ending).toEqual(['paused', 'soon']);
  });

  it('records a payment', async () => {
    let sent: unknown;
    server.use(
      http.post('/api/v1/platform-app/admin/stores/:id/payments', async ({ request, params }) => {
        sent = { id: params.id, ...((await request.json()) as object) };
        return HttpResponse.json(
          {
            payment_id: 'p1',
            plan: 'pro',
            period_start: inDays(3),
            period_end: inDays(95),
            resumed: false,
          },
          { status: 201 },
        );
      }),
    );
    const { user } = renderApp('/platform/admin');
    await screen.findByText(/3 days left/);
    const card = screen.getByText('soon').closest('section')!;
    await user.click(within(card).getByRole('button', { name: 'Record payment' }));
    const sheet = await screen.findByRole('dialog');
    expect(within(sheet).getByLabelText('Amount (ETB)')).toHaveValue('9000'); // Pro's price
    await user.click(within(sheet).getByRole('radio', { name: 'CBE' }));
    await user.type(within(sheet).getByLabelText('Reference (optional)'), 'TX9');
    await user.click(within(sheet).getByRole('button', { name: 'Record payment' }));
    await waitFor(() =>
      expect(sent).toEqual({
        id: 'soon',
        plan: 'pro',
        amount: 9000,
        method: 'CBE',
        reference: 'TX9',
      }),
    );
  });
});

describe('the shop owner', () => {
  it('sees a banner in the last week, and the plan in Settings', async () => {
    server.use(
      http.get(`${storeApiBase}/me`, () => HttpResponse.json(ownerMe({ plan_ends_at: inDays(3) }))),
    );
    const { user } = renderApp(`/s/${STORE_ID}/products`);
    expect(await screen.findByText(/Your Basic plan ends in 3 days/)).toBeInTheDocument();
    await user.click(screen.getByRole('link', { name: 'Settings' }));
    expect(await screen.findByText(/Basic · until .* \(3 days left\)/)).toBeInTheDocument();
  });

  it('sees no banner while there’s time, and a paused shop is told', async () => {
    server.use(
      http.get(`${storeApiBase}/me`, () =>
        HttpResponse.json(ownerMe({ plan_ends_at: inDays(30) })),
      ),
    );
    renderApp(`/s/${STORE_ID}/products`);
    expect(await screen.findByRole('link', { name: 'Settings' })).toBeInTheDocument();
    expect(screen.queryByText(/plan ends/)).not.toBeInTheDocument();
  });

  it('staff don’t get the banner', async () => {
    server.use(
      http.get(`${storeApiBase}/me`, () =>
        HttpResponse.json(ownerMe({ plan_ends_at: inDays(2) }, 'staff')),
      ),
    );
    renderApp(`/s/${STORE_ID}/products`);
    expect(await screen.findByRole('link', { name: 'Settings' })).toBeInTheDocument();
    expect(screen.queryByText(/plan ends/)).not.toBeInTheDocument();
  });

  it('a shop paused for not paying is told so', async () => {
    server.use(
      http.get(`${storeApiBase}/me`, () =>
        HttpResponse.json(ownerMe({ plan_ends_at: inDays(-4), suspended_reason: 'unpaid' })),
      ),
    );
    renderApp(`/s/${STORE_ID}/products`);
    expect(await screen.findByText(/Paused: the plan wasn’t renewed/)).toBeInTheDocument();
  });
});
