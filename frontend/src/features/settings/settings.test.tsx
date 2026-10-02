import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';

import { me, settings, STORE_ID } from '@/test/fixtures';
import { renderApp } from '@/test/render';
import { server, storeApiBase } from '@/test/server';

describe('Settings', () => {
  it('is locked for staff', async () => {
    server.use(http.get(`${storeApiBase}/me`, () => HttpResponse.json(me('staff'))));
    renderApp(`/s/${STORE_ID}/settings`);
    expect(
      await screen.findByText('Only the store owner can change settings.'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Store profile/ })).not.toBeInTheDocument(); // not clickable
  });

  it('switches the language to Amharic and remembers it', async () => {
    const { user } = renderApp(`/s/${STORE_ID}/settings`);
    await user.click(await screen.findByRole('tab', { name: 'አማርኛ' }));
    expect(await screen.findByRole('heading', { name: 'ቅንብሮች' })).toBeInTheDocument();
    expect(
      JSON.parse(localStorage.getItem('store-dashboard-preferences') ?? '{}').state.language,
    ).toBe('am');
  });

  it('saves the store profile as lists', async () => {
    let sent: Record<string, unknown> | undefined;
    server.use(
      http.put(`${storeApiBase}/settings`, async ({ request }) => {
        sent = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(settings());
      }),
    );
    const { user } = renderApp(`/s/${STORE_ID}/settings/profile`);
    expect(await screen.findByText('Telebirr')).toBeInTheDocument();

    // Add a delivery area.
    await user.click(screen.getByRole('button', { name: 'Add area' }));
    await user.type(screen.getByLabelText('Area'), 'CMC');
    await user.type(screen.getByLabelText('Fee (ETB)'), '200');
    await user.click(screen.getByRole('button', { name: 'Done' }));
    expect(await screen.findByText('CMC')).toBeInTheDocument();

    // Close on Sunday is the default; open Saturday stays open.
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent).toBeDefined());
    expect(sent?.delivery_areas).toEqual([
      { area: 'Bole', fee: 150 },
      { area: 'CMC', fee: 200 },
    ]);
    expect(sent?.payment_accounts).toEqual([
      { name: 'Telebirr', number: '0911 000 000', holder: null },
    ]);
    expect((sent?.opening_week as Record<string, unknown>).sun).toEqual({ open: false });
    expect((sent?.opening_week as Record<string, unknown>).mon).toEqual({
      open: true,
      from: '08:30',
      to: '19:00',
    });
  });

  it('shows a /link code and the connected group', async () => {
    server.use(
      http.post(`${storeApiBase}/link-code`, () =>
        HttpResponse.json({
          code: 'K5W3BJPA',
          command: '/link K5W3BJPA',
          minutes: 30,
          expires_at: '',
        }),
      ),
    );
    renderApp(`/s/${STORE_ID}/settings/connect`);
    expect(await screen.findByText('/link K5W3BJPA')).toBeInTheDocument();
    expect(await screen.findByText('nati fashion staff')).toBeInTheDocument();
    expect(screen.getByText('Waiting for the bot to see the code…')).toBeInTheDocument();
  });
});
