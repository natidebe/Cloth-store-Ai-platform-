import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';

import { jacket, me, settings, STORE_ID } from '@/test/fixtures';
import { renderApp } from '@/test/render';
import { server, storeApiBase } from '@/test/server';

/** Phase 13: the shop's words, the type screen, condition and warranty. */
describe('shop types', () => {
  it('an electronics shop says "storage" in the stock screen', async () => {
    server.use(http.get(`${storeApiBase}/me`, () => HttpResponse.json(me('owner', 'electronics'))));
    const { user } = renderApp(`/s/${STORE_ID}/products/p-jacket/stock`);
    expect(await screen.findByText('Colors and storage (optional)')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add storage' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Add storage' }));
    expect(
      within(screen.getByRole('dialog')).getByLabelText('Storage (e.g. 128GB)'),
    ).toBeInTheDocument();
  });

  it('the owner changes the type and renames an option', async () => {
    let sent: unknown;
    server.use(
      http.put(`${storeApiBase}/settings`, async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json({ ...settings(), shop_type: 'electronics' });
      }),
    );
    const { user } = renderApp(`/s/${STORE_ID}/settings`);
    // The row shows the shop's words today.
    await user.click(await screen.findByText('Color · Size'));
    expect(await screen.findByRole('heading', { name: 'Shop type & words' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /Clothing & shoes/ })).toHaveAttribute(
      'aria-checked',
      'true',
    );

    await user.click(screen.getByRole('radio', { name: /Electronics & phones/ }));
    expect(screen.getByText('The bot asks: “Which storage?”')).toBeInTheDocument();
    expect(screen.getByText(/Condition \(New \/ Used\) and Warranty/)).toBeInTheDocument();
    // Second option, English: "Model" instead of "Storage".
    const english = screen.getAllByLabelText('English')[1]!;
    expect(english).toHaveAttribute('placeholder', 'Storage');
    await user.type(english, 'Model');
    expect(screen.getByText('The bot asks: “Which model?”')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(sent).toEqual({
        shop_type: 'electronics',
        option_labels: { option2: { en: 'Model' } },
      }),
    );
  });

  it('an electronics product has a condition and a warranty', async () => {
    let sent: { product?: Record<string, unknown> } & Record<string, unknown> = {};
    server.use(
      http.get(`${storeApiBase}/me`, () => HttpResponse.json(me('owner', 'electronics'))),
      http.post(`${storeApiBase}/products`, async ({ request }) => {
        sent = (await request.json()) as typeof sent;
        return HttpResponse.json({ ...jacket(), id: 'p-new' }, { status: 201 });
      }),
    );
    const { user } = renderApp(`/s/${STORE_ID}/products/new`);
    await user.type(await screen.findByLabelText('Name'), 'iPhone 13');
    // The type's categories are offered.
    expect(screen.getByRole('option', { name: 'Phones' })).toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: 'Used' }));
    await user.selectOptions(screen.getByLabelText('Warranty'), '6 months');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(sent.product ?? sent).toMatchObject({ condition: 'used', warranty_months: 6 }),
    );
  });

  it('a clothing shop has no condition or warranty', async () => {
    renderApp(`/s/${STORE_ID}/products/new`);
    expect(await screen.findByLabelText('Name')).toBeInTheDocument();
    expect(screen.queryByText('Condition')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Warranty')).not.toBeInTheDocument();
  });
});
