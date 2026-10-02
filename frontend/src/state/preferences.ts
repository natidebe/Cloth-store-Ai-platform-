import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';

import { telegramLanguage } from '@/lib/telegram';

export type Language = 'en' | 'am';

interface Preferences {
  /** null: follow the Telegram app's language. */
  language: Language | null;
  setLanguage: (language: Language) => void;
}

/** App-wide choices that survive closing the app (this phone only). */
export const usePreferences = create<Preferences>()(
  persist(
    (set) => ({
      language: null,
      setLanguage: (language) => set({ language }),
    }),
    {
      name: 'store-dashboard-preferences',
      storage: createJSONStorage(() => localStorage),
    },
  ),
);

export function resolveLanguage(choice: Language | null): Language {
  if (choice) return choice;
  return telegramLanguage()?.startsWith('am') ? 'am' : 'en';
}
