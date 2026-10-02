import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';

import { STORE_ID } from '@/test/fixtures';
import { renderApp } from '@/test/render';
import { server, storeApiBase } from '@/test/server';

describe('Orders', () => {
  it('shows the numbers, top products and the orders', async () => {
    renderApp(`/s/${STORE_ID}/orders`);
    expect(await screen.findByText('14,500 ETB')).toBeInTheDocument(); // revenue
    expect(screen.getByText('2 of 3 paid')).toBeInTheDocument();
    expect(screen.getByText('67%')).toBeInTheDocument();
    expect(screen.getByText('3 sold')).toBeInTheDocument();
    expect(screen.getByText('Order #AB12CD')).toBeInTheDocument();
    expect(screen.getByText('Classic Denim Jacket · Blue · M × 2')).toBeInTheDocument();
    expect(screen.getByText('7,300 ETB')).toBeInTheDocument();
  });

  it('switches the period and filters the orders', async () => {
    const periods: string[] = [];
    const filters: string[] = [];
    server.use(
      http.get(`${storeApiBase}/analytics`, ({ request }) => {
        periods.push(new URL(request.url).searchParams.get('period') ?? '');
        return HttpResponse.json({
          period: 'today',
          from: '',
          to: '',
          revenue: 0,
          payments: 0,
          average_order: 0,
          orders_placed: 0,
          orders_paid: 0,
          paid_rate: 0,
          unpaid_orders: 0,
          delivery_orders: 0,
          pickup_orders: 0,
          new_customers: 0,
          per_day: [],
          top_products: [],
          low_stock: [],
          ai_calls_today: 0,
          ai_daily_limit: 300,
        });
      }),
      http.get(`${storeApiBase}/orders`, ({ request }) => {
        filters.push(new URL(request.url).searchParams.get('status') ?? '');
        return HttpResponse.json({ orders: [], more: false });
      }),
    );
    renderApp(`/s/${STORE_ID}/orders`);
    // No orders at all: the design's empty state.
    expect(await screen.findByText('No orders yet')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Share the shop link' })).toBeInTheDocument();
    expect(filters).toEqual(['all']);
    expect(periods).toEqual(['today']);
    expect(screen.queryByText('Revenue')).not.toBeInTheDocument(); // hidden for an empty shop
  });

  it('loads the next page of orders', async () => {
    let calls = 0;
    server.use(
      http.get(`${storeApiBase}/orders`, ({ request }) => {
        calls += 1;
        const before = new URL(request.url).searchParams.get('before');
        const order = {
          id: `o-${calls}`,
          number: `N${calls}`,
          status: 'pending',
          payment_status: 'unpaid',
          total: 100,
          currency: 'ETB',
          fulfillment: 'pickup',
          customer: { name: 'A', phone: '09' },
          delivery_address: null,
          created_at: before ? '2026-10-01T09:00:00+00:00' : '2026-10-02T09:00:00+00:00',
          items: [],
        };
        return HttpResponse.json({ orders: [order], more: !before });
      }),
    );
    const { user } = renderApp(`/s/${STORE_ID}/orders`);
    await user.click(await screen.findByRole('button', { name: 'Show more' }));
    await waitFor(() => expect(screen.getByText('Order #N2')).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: 'Show more' })).not.toBeInTheDocument();
  });
});
