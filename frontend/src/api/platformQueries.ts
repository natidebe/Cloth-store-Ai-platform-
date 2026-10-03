import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { platformApi } from './endpoints';
import type { Plan, ShopType } from './types';

export const platformKeys = {
  me: ['platform', 'me'] as const,
  adminStores: ['platform', 'admin', 'stores'] as const,
};

export function usePlatformMe() {
  return useQuery({ queryKey: platformKeys.me, queryFn: platformApi.me });
}

export function useCreateStore() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ name, token, shopType }: { name: string; token: string; shopType: ShopType }) =>
      platformApi.createStore(name, token, shopType),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: platformKeys.me }),
  });
}

export function useAdminStores(enabled: boolean) {
  return useQuery({
    queryKey: platformKeys.adminStores,
    queryFn: platformApi.adminStores,
    enabled,
  });
}

export function useStoreAdmin() {
  const queryClient = useQueryClient();
  const refresh = () => void queryClient.invalidateQueries({ queryKey: platformKeys.adminStores });
  return {
    approve: useMutation({ mutationFn: platformApi.approve, onSuccess: refresh }),
    suspend: useMutation({ mutationFn: platformApi.suspend, onSuccess: refresh }),
    setPlan: useMutation({
      mutationFn: ({ storeId, plan }: { storeId: string; plan: Plan }) =>
        platformApi.setPlan(storeId, plan),
      onSuccess: refresh,
    }),
  };
}
