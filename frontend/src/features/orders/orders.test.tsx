import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';

import { analytics, order, STORE_ID } from '@/test/fixtures';
import { renderApp } from '@/test/render';
import { server, storeApiBase } from '@/test/server';

describe('Orders', () => {
  it('shows today’s total and the orders, and opens one', async () => {
    server.use(
      http.get(`${storeApiBase}/orders`, () =>
        HttpResponse.json({
          orders: [
            order(),
            {
              ...order(),
              id: 'o-2',
              number: 'SHOP01',
              status: 'delivered',
              payment_status: 'paid',
              fulfillment: 'pickup',
              channel: 'in_shop',
              customer: { name: null, phone: null },
              total: 3000,
              payment_method: 'Cash',
              sold_by: 'Sara',
              items: [{ ...order().items[0], quantity: 1, price: 3000, list_price: 3500 }],
            },
          ],
          more: false,
        }),
      ),
    );
    const { user } = renderApp(`/s/${STORE_ID}/orders`);
    expect(await screen.findByText('#AB12CD · Abebe')).toBeInTheDocument();
    expect(screen.getByText('14,500 ETB')).toBeInTheDocument(); // today
    expect(screen.getByText('#SHOP01 · Walk-in customer')).toBeInTheDocument();
    expect(screen.getByText('Paid')).toBeInTheDocument();
    expect(screen.getByText('New')).toBeInTheDocument();

    await user.click(screen.getByText('#SHOP01 · Walk-in customer'));
    const sheet = await screen.findByRole('dialog');
    expect(within(sheet).getByText('Sold by Sara')).toBeInTheDocument();
    expect(within(sheet).getByText('Paid with Cash')).toBeInTheDocument();
    expect(within(sheet).getByText('−500 ETB')).toBeInTheDocument(); // the discount
  });

  it('filters by channel', async () => {
    const channels: string[] = [];
    server.use(
      http.get(`${storeApiBase}/orders`, ({ request }) => {
        channels.push(new URL(request.url).searchParams.get('channel') ?? '');
        return HttpResponse.json({ orders: [order()], more: false });
      }),
    );
    const { user } = renderApp(`/s/${STORE_ID}/orders`);
    await screen.findByText('#AB12CD · Abebe');
    await user.click(screen.getByRole('radio', { name: '🏪 In-shop' }));
    await waitFor(() => expect(channels).toEqual(['all', 'in_shop']));
  });

  it('shows the empty state for a shop with no orders', async () => {
    server.use(
      http.get(`${storeApiBase}/orders`, () => HttpResponse.json({ orders: [], more: false })),
    );
    renderApp(`/s/${STORE_ID}/orders`);
    expect(await screen.findByText('No orders yet')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Share the shop link' })).toBeInTheDocument();
  });

  it('loads the next page of orders', async () => {
    let calls = 0;
    server.use(
      http.get(`${storeApiBase}/orders`, ({ request }) => {
        calls += 1;
        const before = new URL(request.url).searchParams.get('before');
        const next = {
          ...order(),
          id: `o-${calls}`,
          number: `N${calls}`,
          created_at: before ? '2026-10-01T09:00:00+00:00' : '2026-10-02T09:00:00+00:00',
        };
        return HttpResponse.json({ orders: [next], more: !before });
      }),
    );
    const { user } = renderApp(`/s/${STORE_ID}/orders`);
    await user.click(await screen.findByRole('button', { name: 'Show more' }));
    await waitFor(() => expect(screen.getByText('#N2 · Abebe')).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: 'Show more' })).not.toBeInTheDocument();
  });
});

describe('Analytics', () => {
  it('splits Telegram and in-shop and shows discounts by staff', async () => {
    const periods: string[] = [];
    server.use(
      http.get(`${storeApiBase}/analytics`, ({ request }) => {
        periods.push(new URL(request.url).searchParams.get('period') ?? '');
        return HttpResponse.json(analytics());
      }),
    );
    const { user } = renderApp(`/s/${STORE_ID}/orders`);
    await user.click(await screen.findByRole('button', { name: /Sales today/ }));
    expect(await screen.findByText('10,000 ETB')).toBeInTheDocument(); // Telegram
    expect(screen.getByText('4,500 ETB')).toBeInTheDocument(); // in-shop
    expect(screen.getByText('1 items sold below the listed price')).toBeInTheDocument();
    expect(screen.getByText('Sara')).toBeInTheDocument();

    await user.click(screen.getByRole('tab', { name: 'This week' }));
    await waitFor(() => expect(periods).toContain('week'));
  });
});
