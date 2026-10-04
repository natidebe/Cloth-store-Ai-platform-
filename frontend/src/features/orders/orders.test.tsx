import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';

import { recentMonths } from '@/lib/format';
import { analytics, me, order, STORE_ID } from '@/test/fixtures';
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
    expect(await screen.findByText(/#AB12CD · Abebe/)).toBeInTheDocument();
    expect(screen.getByText('14,500 ETB')).toBeInTheDocument(); // today
    expect(screen.getByText(/#SHOP01 · Walk-in customer/)).toBeInTheDocument();
    expect(screen.getByText('Paid')).toBeInTheDocument();
    expect(screen.getByText('New')).toBeInTheDocument();

    await user.click(screen.getByText(/#SHOP01 · Walk-in customer/));
    const sheet = await screen.findByRole('dialog');
    expect(within(sheet).getByText('Sold by Sara')).toBeInTheDocument();
    expect(within(sheet).getByText('Paid with Cash')).toBeInTheDocument();
    expect(within(sheet).getByText('−500 ETB')).toBeInTheDocument(); // the discount
  });

  it('shows what was bought first, then the number and the customer', async () => {
    server.use(
      http.get(`${storeApiBase}/orders`, () =>
        HttpResponse.json({
          orders: [
            {
              ...order(),
              items: [
                order().items[0],
                { ...order().items[0], name: 'Samba', color: 'White', size: '42', quantity: 1 },
              ],
            },
          ],
          more: false,
        }),
      ),
    );
    renderApp(`/s/${STORE_ID}/orders`);
    expect(await screen.findByText('Classic Denim Jacket · Blue · M × 2')).toBeInTheDocument();
    expect(screen.getByText('+ 1 more')).toBeInTheDocument();
    expect(screen.getByText(/^#AB12CD · Abebe · Telegram · /)).toBeInTheDocument();
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
    await screen.findByText(/#AB12CD · Abebe/);
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
    await waitFor(() => expect(screen.getByText(/#N2 · Abebe/)).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: 'Show more' })).not.toBeInTheDocument();
  });
});

describe('Order stages and the export (Phase 15)', () => {
  it('shows on the way, delivered and picked up', async () => {
    server.use(
      http.get(`${storeApiBase}/orders`, () =>
        HttpResponse.json({
          orders: [
            {
              ...order(),
              id: 'o-1',
              number: 'WAY001',
              status: 'out_for_delivery',
              payment_status: 'paid',
            },
            {
              ...order(),
              id: 'o-2',
              number: 'DEL001',
              status: 'delivered',
              payment_status: 'paid',
            },
            {
              ...order(),
              id: 'o-3',
              number: 'PIC001',
              status: 'delivered',
              payment_status: 'paid',
              fulfillment: 'pickup',
            },
          ],
          more: false,
        }),
      ),
    );
    const { user } = renderApp(`/s/${STORE_ID}/orders`);
    expect(await screen.findByText('On the way')).toBeInTheDocument();
    expect(screen.getByText('Delivered')).toBeInTheDocument();
    expect(screen.getByText('Picked up')).toBeInTheDocument();

    await user.click(screen.getByText(/#WAY001/));
    const sheet = await screen.findByRole('dialog');
    expect(within(sheet).getByText(/buttons in the staff group/)).toBeInTheDocument();
  });

  it('lets the owner send a month to their Telegram', async () => {
    let asked: unknown;
    server.use(
      http.post(`${storeApiBase}/orders/export`, async ({ request }) => {
        asked = await request.json();
        return HttpResponse.json({ sent: true, file: 'x.xlsx', orders: 3, revenue: 9000 });
      }),
    );
    const { user } = renderApp(`/s/${STORE_ID}/orders`);
    await user.click(await screen.findByRole('button', { name: 'Export for the accountant' }));
    const sheet = await screen.findByRole('dialog');
    const [thisMonth, lastMonth] = recentMonths();
    expect(within(sheet).getAllByRole('radio')).toHaveLength(4);
    await user.click(within(sheet).getAllByRole('radio')[1] as HTMLElement);
    await user.click(within(sheet).getByRole('button', { name: 'Send to my Telegram' }));
    await waitFor(() => expect(asked).toEqual({ month: lastMonth }));
    expect(
      await screen.findByText('Sent! Open your chat with @nati_fashion_bot.'),
    ).toBeInTheDocument();
    expect(thisMonth).toMatch(/^\d{4}-\d{2}$/);
  });

  it('says what to do when the bot cannot write to the owner', async () => {
    server.use(
      http.post(`${storeApiBase}/orders/export`, () =>
        HttpResponse.json(
          {
            detail:
              "The bot can't send you the file yet. Open @nati_fashion_bot, press Start, then try again.",
          },
          { status: 409 },
        ),
      ),
    );
    const { user } = renderApp(`/s/${STORE_ID}/orders`);
    await user.click(await screen.findByRole('button', { name: 'Export for the accountant' }));
    await user.click(await screen.findByRole('button', { name: 'Send to my Telegram' }));
    expect(await screen.findByText(/press Start, then try again/)).toBeInTheDocument();
  });

  it('is not offered to staff', async () => {
    server.use(http.get(`${storeApiBase}/me`, () => HttpResponse.json(me('staff'))));
    renderApp(`/s/${STORE_ID}/orders`);
    await screen.findByText(/#AB12CD · Abebe/);
    expect(
      screen.queryByRole('button', { name: 'Export for the accountant' }),
    ).not.toBeInTheDocument();
  });

  it('counts months back across the new year, in Addis Ababa', () => {
    // 22:00 UTC on Jan 31 is already Feb 1 in Addis.
    expect(recentMonths(new Date('2027-01-31T22:00:00Z'), 3)).toEqual([
      '2027-02',
      '2027-01',
      '2026-12',
    ]);
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
