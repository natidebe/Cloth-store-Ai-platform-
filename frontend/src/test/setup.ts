import '@testing-library/jest-dom/vitest';
import { cleanup, configure } from '@testing-library/react';
import { afterAll, afterEach, beforeAll } from 'vitest';

import '@/i18n';
import { usePreferences } from '@/state/preferences';
import { useToasts } from '@/state/toasts';

import { server } from './server';

// Screens load lazily (code splitting): give them time to appear on a busy machine.
configure({ asyncUtilTimeout: 5000 });

// Any request without a handler fails the test (no accidental real calls).
beforeAll(() => server.listen({ onUnhandledFrame: 'error' }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
  localStorage.clear();
  // Zustand stores live in memory: start every test fresh.
  usePreferences.setState({ language: null });
  useToasts.setState({ toasts: [] });
});
afterAll(() => server.close());
