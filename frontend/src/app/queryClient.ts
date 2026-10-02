import { QueryClient } from '@tanstack/react-query';

import { ApiError } from '@/api/client';

/** Don't retry what won't change by retrying (login, permission, not found, refused). */
function shouldRetry(failures: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return failures < 2;
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 20_000,
        retry: shouldRetry,
        refetchOnWindowFocus: true, // coming back to the Mini App shows fresh numbers
      },
      mutations: { retry: false },
    },
  });
}
