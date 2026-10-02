import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';

import { jacket, me } from '@/test/fixtures';
import { renderApp } from '@/test/render';
import { server, storeApiBase } from '@/test/server';
import { STORE_ID } from '@/test/fixtures';

describe('Products', () => {
  it('lists products with stock, badges and the numbers on top', async () => {
    renderApp(`/s/${STORE_ID}/products`);
    expect(await screen.findByText('Classic Denim Jacket')).toBeInTheDocument();
    expect(screen.getByText('3,500 – 3,800 ETB')).toBeInTheDocument();
    expect(screen.getByText('Sold out')).toBeInTheDocument(); // the sneaker has 0
    expect(screen.getAllByText('Low stock').length).toBeGreaterThan(0);
    expect(screen.getByText('Owner')).toBeInTheDocument();
    // Orders today, from the analytics.
    expect(await screen.findByText('3')).toBeInTheDocument();
  });

  it('searches by name, code or keyword (Amharic too) and filters by category', async () => {
    const { user } = renderApp(`/s/${STORE_ID}/products`);
    await screen.findByText('Classic Denim Jacket');
    await user.type(screen.getByRole('searchbox'), 'ጫማ');
    await waitFor(() => expect(screen.queryByText('Classic Denim Jacket')).not.toBeInTheDocument());
    expect(screen.getByText('Nike Air Max 90')).toBeInTheDocument();
    await user.clear(screen.getByRole('searchbox'));
    await user.click(screen.getByRole('radio', { name: 'Clothing' }));
    await waitFor(() => expect(screen.queryByText('Nike Air Max 90')).not.toBeInTheDocument());
  });

  it('quick stock saves only the changed sizes', async () => {
    const saved: unknown[] = [];
    server.use(
      http.post(`${storeApiBase}/variants/:id/stock`, async ({ params, request }) => {
        saved.push({ id: params.id, body: await request.json() });
        return HttpResponse.json({ variant_id: params.id, stock: 7 });
      }),
    );
    const { user } = renderApp(`/s/${STORE_ID}/products`);
    await user.click(await screen.findByText('Classic Denim Jacket'));
    const sheet = await screen.findByRole('dialog', { name: 'Quick stock' });
    const save =
      within(sheet).queryByRole('button', { name: 'Save stock' }) ??
      screen.getByRole('button', { name: 'Save stock' });
    expect(save).toBeDisabled(); // nothing changed yet
    await user.click(within(sheet).getByRole('button', { name: 'Blue · M +1' }));
    await user.click(within(sheet).getByRole('button', { name: 'Blue · M +1' }));
    await user.click(screen.getByRole('button', { name: 'Save stock' }));
    await waitFor(() => expect(saved).toEqual([{ id: 'v-blue-m', body: { set: 7 } }]));
  });

  it('shows the empty state when there are no products', async () => {
    server.use(
      http.get(`${storeApiBase}/products`, () =>
        HttpResponse.json({ products: [], categories: [] }),
      ),
    );
    renderApp(`/s/${STORE_ID}/products`);
    expect(await screen.findByText('No products yet')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add product' })).toBeInTheDocument();
  });

  it('locks the price for staff, and sends no price when they save', async () => {
    let sent: Record<string, unknown> | undefined;
    server.use(
      http.get(`${storeApiBase}/me`, () => HttpResponse.json(me('staff'))),
      http.patch(`${storeApiBase}/products/:id`, async ({ request }) => {
        sent = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...jacket(), description: 'Soft' });
      }),
    );
    const { user } = renderApp(`/s/${STORE_ID}/products/p-jacket`);
    const price = await screen.findByLabelText('Price');
    expect(price).toBeDisabled();
    expect(screen.getByText('Only the owner can change prices')).toBeInTheDocument();
    await user.clear(screen.getByLabelText('Description'));
    await user.type(screen.getByLabelText('Description'), 'Soft');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent).toBeDefined());
    expect(sent).not.toHaveProperty('base_price');
    expect(sent).toMatchObject({ description: 'Soft', name: 'Classic Denim Jacket' });
  });

  it('a new product needs a name, then goes on to its stock grid', async () => {
    server.use(
      http.post(`${storeApiBase}/products`, () =>
        HttpResponse.json({ ...jacket(), id: 'p-new' }, { status: 201 }),
      ),
    );
    const { user } = renderApp(`/s/${STORE_ID}/products/new`);
    await user.click(await screen.findByRole('button', { name: 'Save' }));
    expect(await screen.findByText('The product needs a name')).toBeInTheDocument();
    await user.type(screen.getByLabelText('Name'), 'Polo');
    await user.type(screen.getByLabelText('Price'), '1800');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByRole('heading', { name: 'Stock and prices' })).toBeInTheDocument();
  });

  it('a bag (no color, no size): just one stock counter', async () => {
    const bag = {
      ...jacket(),
      id: 'p-bag',
      name: 'Leather Bag',
      variants: [],
      total_stock: 0,
      variant_count: 0,
    };
    let saved: unknown;
    server.use(
      http.get(`${storeApiBase}/products/p-bag`, () => HttpResponse.json(bag)),
      http.put(`${storeApiBase}/products/p-bag/variants`, async ({ request }) => {
        saved = await request.json();
        return HttpResponse.json({ product: bag, added: 1, updated: 0, removed: 0, note: '' });
      }),
    );
    const { user } = renderApp(`/s/${STORE_ID}/products/p-bag/stock`);
    expect(await screen.findByText('Colors and sizes (optional)')).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Stock +1' }));
    await user.click(screen.getByRole('button', { name: 'Stock +1' }));
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(saved).toEqual({
        variants: [{ color: null, size: null, stock: 2, price: null }],
        remove: [],
      }),
    );
  });

  it('a belt in colors only: rows of colors, no size needed', async () => {
    // A belt saved as one variant without color: its first color takes over its stock.
    const belt = {
      ...jacket(),
      id: 'p-belt',
      name: 'Belt',
      total_stock: 3,
      variant_count: 1,
      variants: [
        { id: 'v-belt', color: null, size: null, stock: 3, price_override: null, price: '900' },
      ],
    };
    let saved: unknown;
    server.use(
      http.get(`${storeApiBase}/products/p-belt`, () => HttpResponse.json(belt)),
      http.put(`${storeApiBase}/products/p-belt/variants`, async ({ request }) => {
        saved = await request.json();
        return HttpResponse.json({ product: belt, added: 1, updated: 1, removed: 0, note: '' });
      }),
    );
    const { user } = renderApp(`/s/${STORE_ID}/products/p-belt/stock`);
    expect(await screen.findByRole('group', { name: 'Stock' })).toBeInTheDocument();
    expect(screen.getByRole('spinbutton', { name: 'Stock' })).toHaveValue(3);

    for (const color of ['Black', 'Brown']) {
      await user.click(screen.getByRole('button', { name: 'Add color' }));
      await user.type(screen.getByLabelText('Color name (e.g. Black)'), color);
      await user.click(
        within(screen.getByRole('dialog')).getByRole('button', { name: 'Add color' }),
      );
    }
    // Black has the 3 from before; Brown is new.
    expect(await screen.findByRole('button', { name: 'Black: 3' })).toBeInTheDocument();
    expect(screen.queryByRole('columnheader', { name: /\d/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Brown$/ }));
    await user.click(screen.getByRole('button', { name: 'Stock +1' }));
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(saved).toEqual({
        variants: [
          { id: 'v-belt', color: 'Black', size: null, stock: 3, price: null },
          { color: 'Brown', size: null, stock: 1, price: null },
        ],
        remove: [],
      }),
    );
  });
});
