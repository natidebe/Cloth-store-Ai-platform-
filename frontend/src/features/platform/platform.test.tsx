import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';

import type { PlatformMe } from '@/api/types';
import { renderApp } from '@/test/render';
import { server } from '@/test/server';

const user = { id: 1, name: 'Nati', username: null, language_code: 'en' };

function platformMe(overrides: Partial<PlatformMe> = {}): PlatformMe {
  return {
    user,
    is_platform_admin: false,
    stores: [],
    support_url: 'https://t.me/support',
    ...overrides,
  };
}

const store = {
  id: 's1',
  name: 'nati fashion',
  status: 'pending' as const,
  plan: 'free' as const,
  bot_username: 'nati_bot',
  staff_group_linked: false,
  channel_linked: false,
  dashboard_url: '',
};

describe('the platform bot', () => {
  it('a new user creates a store, then waits for approval', async () => {
    let me = platformMe();
    let sent: unknown;
    server.use(
      http.get('/api/v1/platform-app/me', () => HttpResponse.json(me)),
      http.post('/api/v1/platform-app/stores', async ({ request }) => {
        sent = await request.json();
        me = platformMe({ stores: [store] });
        return HttpResponse.json({ ...store, bot_connected: true, note: '' }, { status: 201 });
      }),
    );
    const { user: u } = renderApp('/platform');
    expect(await screen.findByRole('heading', { name: 'Create your store' })).toBeInTheDocument();
    await u.click(screen.getByRole('button', { name: 'Create store' }));
    expect(
      await screen.findByText('The store name must be 2 to 80 characters'),
    ).toBeInTheDocument();
    await u.type(screen.getByLabelText('Store name'), 'nati fashion');
    await u.click(screen.getByLabelText('Bot token'));
    await u.paste(`123456789:${'A'.repeat(35)}`); // pasted from @BotFather, as users do
    await u.click(screen.getByRole('button', { name: 'Create store' }));
    expect(
      await screen.findByRole('heading', { name: 'Waiting for approval' }),
    ).toBeInTheDocument();
    expect(sent).toEqual({ name: 'nati fashion', bot_token: `123456789:${'A'.repeat(35)}` });
    expect(screen.getByRole('button', { name: 'Contact support' })).toBeInTheDocument();
  });

  it('a platform admin approves a pending store', async () => {
    const approved: string[] = [];
    server.use(
      http.get('/api/v1/platform-app/me', () =>
        HttpResponse.json(platformMe({ is_platform_admin: true })),
      ),
      http.get('/api/v1/platform-app/admin/stores', () =>
        HttpResponse.json([
          {
            id: 's1',
            name: 'nati fashion',
            status: 'pending',
            plan: 'free',
            telegram_bot_username: 'nati_bot',
            created_at: null,
            orders: 0,
          },
        ]),
      ),
      http.post('/api/v1/platform-app/admin/stores/:id/approve', ({ params }) => {
        approved.push(String(params.id));
        return HttpResponse.json({ status: 'active' });
      }),
    );
    const { user: u } = renderApp('/platform/admin');
    await u.click(await screen.findByRole('button', { name: 'Approve' }));
    await waitFor(() => expect(approved).toEqual(['s1']));
  });

  it('someone who isn’t an admin can’t see the admin list', async () => {
    server.use(
      http.get('/api/v1/platform-app/me', () =>
        HttpResponse.json(platformMe({ stores: [{ ...store, status: 'active' }] })),
      ),
    );
    renderApp('/platform/admin');
    expect(await screen.findByText('Platform admins only.')).toBeInTheDocument();
  });
});
