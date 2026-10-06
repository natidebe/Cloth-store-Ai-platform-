import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';

import { storeApi } from './endpoints';
import type {
  CounterSaleInput,
  ExportCalendar,
  GridRow,
  OrderFilter,
  Period,
  Product,
  ProductFields,
  ProductList,
  StoreSettings,
} from './types';

/**
 * Server state: every read is a query with a key from `keys`, every change a
 * mutation that refreshes exactly what it changed. Screens never fetch by hand.
 */
export const keys = {
  store: (storeId: string) => ['store', storeId] as const,
  me: (storeId: string) => [...keys.store(storeId), 'me'] as const,
  analytics: (storeId: string, period: Period) =>
    [...keys.store(storeId), 'analytics', period] as const,
  products: (storeId: string) => [...keys.store(storeId), 'products'] as const,
  product: (storeId: string, productId: string) =>
    [...keys.store(storeId), 'product', productId] as const,
  orders: (storeId: string, filter: OrderFilter) =>
    [...keys.store(storeId), 'orders', filter] as const,
  settings: (storeId: string) => [...keys.store(storeId), 'settings'] as const,
  connections: (storeId: string) => [...keys.store(storeId), 'connections'] as const,
};

// --- Reads ---------------------------------------------------------------------

export function useMe(storeId: string) {
  return useQuery({
    queryKey: keys.me(storeId),
    queryFn: () => storeApi.me(storeId),
    staleTime: 60_000,
  });
}

export function useAnalytics(storeId: string, period: Period) {
  return useQuery({
    queryKey: keys.analytics(storeId, period),
    queryFn: () => storeApi.analytics(storeId, period),
    placeholderData: keepPreviousData, // keep the old numbers while switching periods
  });
}

export function useProducts(storeId: string) {
  return useQuery({ queryKey: keys.products(storeId), queryFn: () => storeApi.products(storeId) });
}

export function useProduct(storeId: string, productId: string | undefined) {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: keys.product(storeId, productId ?? 'new'),
    queryFn: () => storeApi.product(storeId, productId as string),
    enabled: Boolean(productId),
    // Show the row from the product list at once, then refresh it.
    initialData: () =>
      queryClient
        .getQueryData<ProductList>(keys.products(storeId))
        ?.products.find((p) => p.id === productId),
  });
}

export function useOrders(storeId: string, filter: OrderFilter) {
  return useInfiniteQuery({
    queryKey: keys.orders(storeId, filter),
    queryFn: ({ pageParam }) => storeApi.orders(storeId, filter, pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => (last.more ? last.orders.at(-1)?.created_at : undefined),
  });
}

/** Stock and online holds of one variant, fresh each time it's picked (D55). */
export function useAvailability(storeId: string, variantId: string | null) {
  return useQuery({
    queryKey: [...keys.store(storeId), 'availability', variantId ?? ''] as const,
    queryFn: () => storeApi.availability(storeId, variantId as string),
    enabled: Boolean(variantId),
    staleTime: 0,
  });
}

/** A sale in the shop: afterwards the orders, the stock and the numbers change. */
export function useCounterSale(storeId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (sale: CounterSaleInput) => storeApi.counterSale(storeId, sale),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: [...keys.store(storeId), 'orders'] });
      void queryClient.invalidateQueries({ queryKey: keys.products(storeId) });
      void queryClient.invalidateQueries({ queryKey: [...keys.store(storeId), 'product'] });
      void queryClient.invalidateQueries({ queryKey: [...keys.store(storeId), 'analytics'] });
    },
  });
}

export function useSettings(storeId: string, enabled = true) {
  return useQuery({
    queryKey: keys.settings(storeId),
    queryFn: () => storeApi.settings(storeId),
    enabled,
  });
}

export function useConnections(storeId: string, poll: boolean) {
  return useQuery({
    queryKey: keys.connections(storeId),
    queryFn: () => storeApi.connections(storeId),
    refetchInterval: poll ? 3000 : false, // while a /link code waits to be sent
  });
}

// --- Changes -------------------------------------------------------------------

/** After a product or stock change: the list, that product, and the numbers. */
function useRefreshProducts(storeId: string) {
  const queryClient = useQueryClient();
  return (product?: Product) => {
    if (product) queryClient.setQueryData(keys.product(storeId, product.id), product);
    void queryClient.invalidateQueries({ queryKey: keys.products(storeId) });
    void queryClient.invalidateQueries({ queryKey: [...keys.store(storeId), 'analytics'] });
  };
}

export function useCreateProduct(storeId: string) {
  const refresh = useRefreshProducts(storeId);
  return useMutation({
    mutationFn: ({ fields, variants }: { fields: ProductFields; variants?: GridRow[] }) =>
      storeApi.createProduct(storeId, fields, variants),
    onSuccess: (product) => refresh(product),
  });
}

export function useUpdateProduct(storeId: string, productId: string) {
  const refresh = useRefreshProducts(storeId);
  return useMutation({
    mutationFn: (fields: ProductFields) => storeApi.updateProduct(storeId, productId, fields),
    onSuccess: (product) => refresh(product),
  });
}

export function useSaveGrid(storeId: string, productId: string) {
  const refresh = useRefreshProducts(storeId);
  return useMutation({
    mutationFn: ({ rows, remove }: { rows: GridRow[]; remove: string[] }) =>
      storeApi.saveGrid(storeId, productId, rows, remove),
    onSuccess: (result) => refresh(result.product),
  });
}

/** Quick stock: several variants set at once. */
export function useSetStock(storeId: string, productId: string) {
  const refresh = useRefreshProducts(storeId);
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (changes: { variantId: string; stock: number }[]) => {
      for (const change of changes)
        await storeApi.setStock(storeId, change.variantId, change.stock);
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: keys.product(storeId, productId) });
      refresh();
    },
  });
}

export function useTakeOffSale(storeId: string, productId: string) {
  const refresh = useRefreshProducts(storeId);
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => storeApi.takeOffSale(storeId, productId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.product(storeId, productId) });
      refresh();
    },
  });
}

export function usePublish(storeId: string, productId: string) {
  return useMutation({ mutationFn: () => storeApi.publish(storeId, productId) });
}

export function useUploadPhoto(storeId: string) {
  return useMutation({
    mutationFn: ({ file, name }: { file: Blob; name: string }) =>
      storeApi.uploadPhoto(storeId, file, name),
  });
}

export function useSaveSettings(storeId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (settings: Partial<StoreSettings>) => storeApi.saveSettings(storeId, settings),
    onSuccess: (saved) => {
      queryClient.setQueryData(keys.settings(storeId), saved);
      // /me carries the staff discount limit and the payment methods.
      void queryClient.invalidateQueries({ queryKey: keys.me(storeId) });
    },
  });
}

export function useExportOrders(storeId: string) {
  return useMutation({
    mutationFn: ({ month, calendar }: { month: string; calendar: ExportCalendar }) =>
      storeApi.exportOrders(storeId, month, calendar),
  });
}

export function useLinkCode(storeId: string) {
  return useMutation({ mutationFn: () => storeApi.linkCode(storeId) });
}

export function useChangeBot(storeId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (token: string) => storeApi.changeBot(storeId, token),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: keys.me(storeId) }),
  });
}
