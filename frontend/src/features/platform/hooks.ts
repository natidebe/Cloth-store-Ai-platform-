import { usePlatformMe } from '@/api/platformQueries';
import type { PlatformMe, StoreCard } from '@/api/types';
import { useBackButton } from '@/app/hooks';

/** Who I am in the platform bot. Only used under PlatformShell, which waits for it. */
export function usePlatform(): PlatformMe {
  const { data } = usePlatformMe();
  if (!data) throw new Error('usePlatform before it loaded');
  return data;
}

export function statusTone(status: StoreCard['status']): 'warn' | 'success' | 'danger' {
  return status === 'pending' ? 'warn' : status === 'active' ? 'success' : 'danger';
}

/** Back from create/admin to the platform home. */
export function useBackToPlatform() {
  useBackButton('/platform');
}
