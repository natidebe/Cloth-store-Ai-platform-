import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

import am from './am';
import en from './en';

/** Amharic and English (D29: the user's choice; Settings → Language). */
export const resources = { en: { translation: en }, am: { translation: am } } as const;

void i18n.use(initReactI18next).init({
  resources,
  lng: 'en',
  fallbackLng: 'en',
  interpolation: { escapeValue: false }, // React escapes already
  returnNull: false,
});

export default i18n;

declare module 'i18next' {
  interface CustomTypeOptions {
    defaultNS: 'translation';
    resources: { translation: typeof en };
  }
}
