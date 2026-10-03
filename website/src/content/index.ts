import { createContext, useContext } from 'react';

import { am } from './am';
import { en, type Content, type Lang } from './en';

export type { Content, Lang };

export const contents: Record<Lang, Content> = { am, en };

/** Each language is its own page: Amharic at /, English at /en/. */
export const pathOf: Record<Lang, string> = { am: '/', en: '/en/' };

export function langOfPath(pathname: string): Lang {
  return pathname.startsWith('/en') ? 'en' : 'am';
}

export const LangContext = createContext<Lang>('am');

export function useLang(): Lang {
  return useContext(LangContext);
}

export function useContent(): Content {
  return contents[useLang()];
}
