import type { ComponentType } from 'react';
import { createBrowserRouter, Navigate, type RouteObject } from 'react-router';

import { Entry, RouteError } from './RouteScreens';
import { StoreShell } from './StoreShell';

/**
 * Each screen is its own chunk, downloaded when it's first opened, so the
 * first screen shows quickly on a slow mobile connection.
 */
function screen(load: () => Promise<{ default: ComponentType }>): Pick<RouteObject, 'lazy'> {
  return { lazy: async () => ({ Component: (await load()).default }) };
}

const products = () =>
  import('@/features/products/ProductsScreen').then((m) => ({ default: m.ProductsScreen }));
const productDetails = () =>
  import('@/features/products/ProductDetailsScreen').then((m) => ({
    default: m.ProductDetailsScreen,
  }));
const stockGrid = () =>
  import('@/features/products/StockGridScreen').then((m) => ({ default: m.StockGridScreen }));
const orders = () =>
  import('@/features/orders/OrdersScreen').then((m) => ({ default: m.OrdersScreen }));
const settings = () =>
  import('@/features/settings/SettingsScreen').then((m) => ({ default: m.SettingsScreen }));
const storeProfile = () =>
  import('@/features/settings/StoreProfileScreen').then((m) => ({ default: m.StoreProfileScreen }));
const connect = () =>
  import('@/features/settings/ConnectScreen').then((m) => ({ default: m.ConnectScreen }));
const changeBot = () =>
  import('@/features/settings/ChangeBotScreen').then((m) => ({ default: m.ChangeBotScreen }));
const platformShell = () =>
  import('@/features/platform/PlatformScreens').then((m) => ({ default: m.PlatformShell }));
const platformHome = () =>
  import('@/features/platform/PlatformScreens').then((m) => ({ default: m.PlatformHome }));
const createStore = () =>
  import('@/features/platform/CreateStoreScreen').then((m) => ({ default: m.CreateStoreScreen }));
const adminStores = () =>
  import('@/features/platform/AdminStoresScreen').then((m) => ({ default: m.AdminStoresScreen }));

export const routes: RouteObject[] = [
  {
    errorElement: <RouteError />,
    children: [
      { index: true, element: <Entry /> },
      {
        path: 's/:storeId',
        element: <StoreShell />,
        children: [
          { index: true, element: <Navigate to="products" replace /> },
          { path: 'products', ...screen(products) },
          { path: 'products/new', ...screen(productDetails) },
          { path: 'products/:productId', ...screen(productDetails) },
          { path: 'products/:productId/stock', ...screen(stockGrid) },
          { path: 'orders', ...screen(orders) },
          { path: 'settings', ...screen(settings) },
          { path: 'settings/profile', ...screen(storeProfile) },
          { path: 'settings/connect', ...screen(connect) },
          { path: 'settings/bot', ...screen(changeBot) },
        ],
      },
      {
        path: 'platform',
        ...screen(platformShell),
        children: [
          { index: true, ...screen(platformHome) },
          { path: 'create', ...screen(createStore) },
          { path: 'admin', ...screen(adminStores) },
        ],
      },
      { path: '*', element: <Navigate to="/" replace /> },
    ],
  },
];

export function createRouter() {
  return createBrowserRouter(routes, { basename: '/app' });
}
