import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';

import { analytics, jacket, me, order, settings, sneaker, STORE_ID } from './fixtures';

const base = `/api/v1/app/stores/${STORE_ID}`;

/** The backend, as the tests' default (an owner of a store with two products). */
export const handlers = [
  http.get(`${base}/me`, () => HttpResponse.json(me('owner'))),
  http.get(`${base}/products`, () =>
    HttpResponse.json({ products: [jacket(), sneaker()], categories: ['clothing', 'sneakers'] }),
  ),
  http.get(`${base}/products/:id`, ({ params }) =>
    HttpResponse.json(params.id === 'p-nike' ? sneaker() : jacket()),
  ),
  http.get(`${base}/analytics`, () => HttpResponse.json(analytics())),
  http.get(`${base}/orders`, () => HttpResponse.json({ orders: [order()], more: false })),
  http.get(`${base}/settings`, () => HttpResponse.json(settings())),
  http.get(`${base}/variants/:id/availability`, ({ params }) =>
    HttpResponse.json({
      variant_id: params.id,
      stock: 5,
      held: 0,
      available: 5,
      listed_price: '3500',
      holds: [],
    }),
  ),
  http.get(`${base}/connections`, () =>
    HttpResponse.json({
      staff_group: { id: -500, title: 'nati fashion staff', bot_can_see: true },
      channel: null,
    }),
  ),
];

export const server = setupServer(...handlers);
export { base as storeApiBase };
