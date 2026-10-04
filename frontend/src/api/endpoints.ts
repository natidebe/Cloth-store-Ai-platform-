import { api } from './client';
import type {
  AdminStore,
  Availability,
  CounterSaleInput,
  CounterSaleResult,
  Analytics,
  ExportResult,
  BotChange,
  Connections,
  GridResult,
  GridRow,
  LinkCode,
  Me,
  OrderFilter,
  OrderPage,
  Period,
  Plan,
  PaymentInput,
  PaymentResult,
  PlatformMe,
  ShopType,
  Product,
  ProductFields,
  ProductList,
  StoreCard,
  StoreSettings,
} from './types';

/** One function per backend endpoint (docs/mini-app-api.md). */

const store = (storeId: string) => `/app/stores/${encodeURIComponent(storeId)}`;

export const storeApi = {
  me: (storeId: string) => api<Me>(`${store(storeId)}/me`),

  analytics: (storeId: string, period: Period) =>
    api<Analytics>(`${store(storeId)}/analytics?period=${period}`),

  products: (storeId: string) => api<ProductList>(`${store(storeId)}/products`),

  product: (storeId: string, productId: string) =>
    api<Product>(`${store(storeId)}/products/${productId}`),

  createProduct: (storeId: string, product: ProductFields, variants: GridRow[] = []) =>
    api<Product>(`${store(storeId)}/products`, { method: 'POST', body: { product, variants } }),

  updateProduct: (storeId: string, productId: string, fields: ProductFields) =>
    api<Product>(`${store(storeId)}/products/${productId}`, { method: 'PATCH', body: fields }),

  saveGrid: (storeId: string, productId: string, variants: GridRow[], remove: string[] = []) =>
    api<GridResult>(`${store(storeId)}/products/${productId}/variants`, {
      method: 'PUT',
      body: { variants, remove },
    }),

  setStock: (storeId: string, variantId: string, stock: number) =>
    api<{ variant_id: string; stock: number }>(`${store(storeId)}/variants/${variantId}/stock`, {
      method: 'POST',
      body: { set: stock },
    }),

  takeOffSale: (storeId: string, productId: string) =>
    api<{ ok: boolean; variants: number }>(`${store(storeId)}/products/${productId}/off-sale`, {
      method: 'POST',
    }),

  deleteProduct: (storeId: string, productId: string) =>
    api<{ ok: boolean }>(`${store(storeId)}/products/${productId}`, { method: 'DELETE' }),

  publish: (storeId: string, productId: string) =>
    api<{ ok: boolean; message: string }>(`${store(storeId)}/products/${productId}/publish`, {
      method: 'POST',
    }),

  uploadPhoto: (storeId: string, file: Blob, name: string) => {
    const form = new FormData();
    form.append('file', file, name);
    return api<{ photo_url: string }>(`${store(storeId)}/photos`, { method: 'POST', form });
  },

  orders: (storeId: string, channel: OrderFilter, before?: string) => {
    const query = new URLSearchParams({ channel, limit: '20' });
    if (before) query.set('before', before);
    return api<OrderPage>(`${store(storeId)}/orders?${query.toString()}`);
  },

  /** Owners: "2026-09" → the bot sends the month's Excel file to their private chat. */
  exportOrders: (storeId: string, month: string) =>
    api<ExportResult>(`${store(storeId)}/orders/export`, { method: 'POST', body: { month } }),

  availability: (storeId: string, variantId: string) =>
    api<Availability>(`${store(storeId)}/variants/${variantId}/availability`),

  counterSale: (storeId: string, sale: CounterSaleInput) =>
    api<CounterSaleResult>(`${store(storeId)}/counter-sales`, { method: 'POST', body: sale }),

  settings: (storeId: string) => api<StoreSettings>(`${store(storeId)}/settings`),

  saveSettings: (storeId: string, settings: Partial<StoreSettings>) =>
    api<StoreSettings>(`${store(storeId)}/settings`, { method: 'PUT', body: settings }),

  connections: (storeId: string) => api<Connections>(`${store(storeId)}/connections`),

  linkCode: (storeId: string) => api<LinkCode>(`${store(storeId)}/link-code`, { method: 'POST' }),

  changeBot: (storeId: string, botToken: string) =>
    api<BotChange>(`${store(storeId)}/bot-token`, { method: 'PUT', body: { bot_token: botToken } }),
};

export const platformApi = {
  me: () => api<PlatformMe>('/platform-app/me'),

  createStore: (name: string, botToken: string, shopType: ShopType) =>
    api<StoreCard & { bot_connected: boolean; note: string }>('/platform-app/stores', {
      method: 'POST',
      body: { name, bot_token: botToken, shop_type: shopType },
    }),

  adminStores: () => api<AdminStore[]>('/platform-app/admin/stores'),
  recordPayment: (storeId: string, payment: PaymentInput) =>
    api<PaymentResult>(`/platform-app/admin/stores/${storeId}/payments`, {
      method: 'POST',
      body: payment,
    }),

  approve: (storeId: string) =>
    api<{ status: string }>(`/platform-app/admin/stores/${storeId}/approve`, { method: 'POST' }),

  suspend: (storeId: string) =>
    api<{ status: string }>(`/platform-app/admin/stores/${storeId}/suspend`, { method: 'POST' }),

  setPlan: (storeId: string, plan: Plan) =>
    api<{ plan: string }>(`/platform-app/admin/stores/${storeId}/plan`, {
      method: 'PUT',
      body: { plan },
    }),
};
