import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router';

import { ApiError, NETWORK_ERROR } from '@/api/client';
import { useMe } from '@/api/queries';
import type { Me } from '@/api/types';
import { showBackButton } from '@/lib/telegram';
import { resolveLanguage, usePreferences, type Language } from '@/state/preferences';

/** The store in the URL (/app/s/<store id>/…). */
export function useStoreId(): string {
  const { storeId } = useParams<{ storeId: string }>();
  if (!storeId) throw new Error('useStoreId outside a store route');
  return storeId;
}

/** Who I am in this store. Only used under StoreShell, which waits for it. */
export function useStore(): Me & { storeId: string; isOwner: boolean } {
  const storeId = useStoreId();
  const { data } = useMe(storeId);
  if (!data) throw new Error('useStore before the store loaded');
  return { ...data, storeId, isOwner: data.role === 'owner' };
}

export function useLanguage(): Language {
  return resolveLanguage(usePreferences((state) => state.language));
}

/** Telegram's Back button on sub-screens: goes to `to`, or back in history. */
export function useBackButton(to?: string) {
  const navigate = useNavigate();
  useEffect(() => showBackButton(() => (to ? navigate(to) : navigate(-1))), [navigate, to]);
}

/** A message for the user from any error (the server's own words when it refused). */
export function useErrorText() {
  const { t } = useTranslation();
  return (error: unknown): string => {
    if (error instanceof ApiError) {
      if (error.status === NETWORK_ERROR) return t('access.errorBody');
      if (error.status === 422 || error.isRefused || error.isForbidden || error.status === 404) {
        return error.message;
      }
    }
    return t('access.errorTitle');
  };
}
