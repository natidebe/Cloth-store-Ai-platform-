import { createContext, useContext } from 'react';

import { am } from './am';
import { en, type Content, type Lang } from './en';

export type { Content, Lang };

export const contents: Record<Lang, Content> = { am, en };

/** Each language is its own page: English at / (the default), Amharic at /am/. */
export const pathOf: Record<Lang, string> = { en: '/', am: '/am/' };

export function langOfPath(pathname: string): Lang {
  return pathname.startsWith('/am') ? 'am' : 'en';
}

export const LangContext = createContext<Lang>('en');

export function useLang(): Lang {
  return useContext(LangContext);
}

export function useContent(): Content {
  return contents[useLang()];
}
