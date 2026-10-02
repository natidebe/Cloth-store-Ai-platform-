import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { RouterProvider } from 'react-router';

import { Toasts } from '@/components/ui';
import i18n from '@/i18n';
import { startTelegram } from '@/lib/telegram';
import { applyTheme } from '@/lib/theme';

import { useLanguage } from './hooks';
import { createQueryClient } from './queryClient';
import { createRouter } from './router';

function LanguageSync() {
  const language = useLanguage();
  useEffect(() => {
    void i18n.changeLanguage(language);
    document.documentElement.lang = language;
  }, [language]);
  return null;
}

export function App({ queryClient }: { queryClient?: QueryClient }) {
  const [client] = useState(() => queryClient ?? createQueryClient());
  const [router] = useState(createRouter);

  useEffect(() => {
    startTelegram();
    return applyTheme();
  }, []);

  return (
    <QueryClientProvider client={client}>
      <LanguageSync />
      <RouterProvider router={router} />
      <Toasts />
    </QueryClientProvider>
  );
}
