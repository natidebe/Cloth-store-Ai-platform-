import { screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';

import { api, ApiError } from '@/api/client';
import { STORE_ID } from '@/test/fixtures';
import { renderApp } from '@/test/render';
import { server, storeApiBase } from '@/test/server';

describe('access', () => {
  it('someone outside the staff group sees the design’s locked screen', async () => {
    server.use(
      http.get(`${storeApiBase}/me`, () => HttpResponse.json({ detail: 'no' }, { status: 403 })),
    );
    renderApp(`/s/${STORE_ID}/products`);
    expect(await screen.findByText('You are not in this store’s staff group')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument();
  });

  it('opened outside Telegram: asks to open it from the bot', async () => {
    server.use(
      http.get(`${storeApiBase}/me`, () => HttpResponse.json({ detail: 'no' }, { status: 401 })),
    );
    renderApp(`/s/${STORE_ID}/products`);
    expect(await screen.findByText('Open this from Telegram')).toBeInTheDocument();
  });

  it('a server error offers Try again', async () => {
    server.use(http.get(`${storeApiBase}/me`, () => HttpResponse.json({}, { status: 500 })));
    renderApp(`/s/${STORE_ID}/products`);
    expect(await screen.findByText('Something went wrong')).toBeInTheDocument();
    expect(screen.getByText('HTTP 500')).toBeInTheDocument();
  });

  it('the bot’s link /app/?store=… opens the store', async () => {
    renderApp(`/?store=${STORE_ID}`);
    expect(await screen.findByRole('heading', { name: 'Products' })).toBeInTheDocument();
  });
});

describe('the API client', () => {
  it('sends the Telegram login and turns errors into ApiError', async () => {
    let header: string | null = 'unset';
    server.use(
      http.get('/api/v1/test', ({ request }) => {
        header = request.headers.get('X-Telegram-Init-Data');
        return HttpResponse.json({ detail: [{ msg: 'price must be 0 or more' }] }, { status: 422 });
      }),
    );
    const error = await api('/test').catch((e: unknown) => e);
    expect(header).toBe(''); // outside Telegram: empty (the server refuses it)
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(422);
    expect((error as ApiError).message).toBe('price must be 0 or more');
  });
});
