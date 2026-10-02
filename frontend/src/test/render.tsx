import { QueryClient } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { App } from '@/app/App';

/** Open the whole app at `path` (under /app), like Telegram would. */
export function renderApp(path: string) {
  window.history.pushState({}, '', `/app${path}`);
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } },
  });
  const user = userEvent.setup();
  return { user, queryClient, ...render(<App queryClient={queryClient} />) };
}
