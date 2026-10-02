import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';

import type { CounterSaleInput } from '@/api/types';
import { jacket, me, settings, STORE_ID } from '@/test/fixtures';
import { renderApp } from '@/test/render';
import { server, storeApiBase } from '@/test/server';
import { useCounterDraft } from '@/state/counterDraft';

const result = {
  order_id: 'o-9',
  number: 'S00001',
  total: '3000.00',
  list_total: '3500.00',
  discount: '500.00',
  already_saved: false,
  held_orders: [],
};

beforeEach(() => useCounterDraft.getState().reset());

/** Items screen: add one Blue M jacket at `price` (blank = listed). */
async function addJacket(user: ReturnType<typeof renderApp>['user'], price: string) {
  await user.click(await screen.findByRole('button', { name: 'Add item' }));
  await user.click(await screen.findByRole('button', { name: /Classic Denim Jacket/ }));
  await user.click(await screen.findByRole('radio', { name: /Blue · M/ }));
  if (price) await user.type(screen.getByLabelText('Agreed price (each)'), price);
}

describe('Counter sale', () => {
  it('sells at an agreed price and shows the sale', async () => {
    const bodies: CounterSaleInput[] = [];
    server.use(
      http.post(`${storeApiBase}/counter-sales`, async ({ request }) => {
        bodies.push((await request.json()) as CounterSaleInput);
        return HttpResponse.json(result);
      }),
    );
    const { user } = renderApp(`/s/${STORE_ID}/counter`);
    await addJacket(user, '3000');
    expect(screen.getByText('−14%')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Add to sale' }));

    // The line, and the totals.
    expect(await screen.findByText('Blue · M · ×1')).toBeInTheDocument();
    expect(screen.getByText('−500 ETB')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Continue' }));

    await user.click(await screen.findByRole('radio', { name: 'Telebirr' }));
    await user.type(screen.getByLabelText('Name'), 'Hana');
    await user.click(screen.getByRole('button', { name: /Confirm sale/ }));

    expect(await screen.findByText('Sale saved')).toBeInTheDocument();
    expect(screen.getByText('Sale #S00001')).toBeInTheDocument();
    expect(bodies).toHaveLength(1);
    expect(bodies[0]).toMatchObject({
      items: [{ variant_id: 'v-blue-m', quantity: 1, price: '3000.00' }],
      payment_method: 'Telebirr',
      customer_name: 'Hana',
      allow_held: false,
    });
  });

  it('sells a bag without asking for a color or size', async () => {
    const bag = {
      ...jacket(),
      id: 'p-bag',
      code: 'P200',
      name: 'Leather Bag',
      total_stock: 4,
      variant_count: 1,
      price_min: '2500',
      price_max: '2500',
      variants: [
        { id: 'v-bag', color: null, size: null, stock: 4, price_override: null, price: '2500' },
      ],
    };
    server.use(
      http.get(`${storeApiBase}/products`, () =>
        HttpResponse.json({ products: [bag], categories: ['bags'] }),
      ),
    );
    const { user } = renderApp(`/s/${STORE_ID}/counter`);
    await user.click(await screen.findByRole('button', { name: 'Add item' }));
    await user.click(await screen.findByRole('button', { name: /Leather Bag/ }));
    expect(await screen.findByText('4 in stock')).toBeInTheDocument();
    expect(screen.queryByRole('radiogroup', { name: 'Color and size' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Add to sale' }));
    expect(await screen.findByText('×1')).toBeInTheDocument();
  });

  it('stops staff below their discount limit', async () => {
    server.use(http.get(`${storeApiBase}/me`, () => HttpResponse.json(me('staff'))));
    const { user } = renderApp(`/s/${STORE_ID}/counter`);
    await addJacket(user, '');
    expect(screen.getByText('Lowest you can give: 3,150 ETB')).toBeInTheDocument();
    await user.type(screen.getByLabelText('Agreed price (each)'), '3000'); // 14% off, the limit is 10%
    expect(screen.getByText('Below your limit (10% off). Ask the owner.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add to sale' })).toBeDisabled();
  });

  it('asks before selling what an online order holds, then sells anyway', async () => {
    const bodies: CounterSaleInput[] = [];
    server.use(
      http.post(`${storeApiBase}/counter-sales`, async ({ request }) => {
        const body = (await request.json()) as CounterSaleInput;
        bodies.push(body);
        if (!body.allow_held) {
          return HttpResponse.json(
            { detail: 'Online order #AB12CD is holding Blue · M.' },
            { status: 409, headers: { 'X-Error-Code': 'held_by_online_order' } },
          );
        }
        return HttpResponse.json({ ...result, held_orders: ['AB12CD'] });
      }),
    );
    const { user } = renderApp(`/s/${STORE_ID}/counter`);
    await addJacket(user, '');
    await user.click(screen.getByRole('button', { name: 'Add to sale' }));
    await user.click(await screen.findByRole('button', { name: 'Continue' }));
    await user.click(await screen.findByRole('button', { name: /Confirm sale/ }));

    const sheet = await screen.findByRole('dialog');
    expect(
      within(sheet).getByText('Online order #AB12CD is holding Blue · M.'),
    ).toBeInTheDocument();
    await user.click(within(sheet).getByRole('button', { name: 'Sell anyway' }));

    expect(await screen.findByText('Sale saved')).toBeInTheDocument();
    expect(screen.getByText(/Online order #AB12CD can’t be filled now/)).toBeInTheDocument();
    expect(bodies.map((b) => b.allow_held)).toEqual([false, true]);
    expect(bodies[0]?.request_id).toBe(bodies[1]?.request_id); // the same sale
  });
});

describe('Staff discount limit', () => {
  it('shows the limit in Settings and saves "No discounts"', async () => {
    const saved: unknown[] = [];
    server.use(
      http.put(`${storeApiBase}/settings`, async ({ request }) => {
        const body = await request.json();
        saved.push(body);
        return HttpResponse.json({ ...settings(), staff_discount_percent: '0' });
      }),
    );
    const { user } = renderApp(`/s/${STORE_ID}/settings`);
    await user.click(await screen.findByText('Up to 10% below listed price'));
    expect(await screen.findByText('10%')).toBeInTheDocument();
    expect(screen.getByText(/Listed 1,000 ETB → lowest for staff 900 ETB/)).toBeInTheDocument();

    await user.click(screen.getByRole('radio', { name: /No discounts/ }));
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(saved).toEqual([{ staff_discount_percent: '0' }]));
  });
});
