import type { OptionLabel, ShopType } from '@/api/types';
import type { Language } from '@/state/preferences';

/**
 * The shop's words for a product's two options (Phase 13): "color" and
 * "size" for clothing, "color" and "storage" for electronics… from the server
 * (/me), with the owner's renames. Texts use them as {{opt1}} {{opt2}}
 * (in a sentence), {{Opt1}} {{Opt2}} (at the start), {{plural1}} {{plural2}},
 * and an example value for each ({{example1}} {{example2}}).
 */
export type ShopWords = {
  opt1: string;
  opt2: string;
  Opt1: string;
  Opt2: string;
  plural1: string;
  plural2: string;
  Plural1: string;
  Plural2: string;
  example1: string;
  example2: string;
};

const EXAMPLES: Record<ShopType, Record<Language, [string, string]>> = {
  clothing: { en: ['Black', 'M or 42'], am: ['ጥቁር', 'M ወይም 42'] },
  electronics: { en: ['Black', '128GB'], am: ['ጥቁር', '128GB'] },
  cosmetics: { en: ['Rose', '50 ml'], am: ['ሮዝ', '50 ml'] },
  general: { en: ['Classic', 'Large'], am: ['ክላሲክ', 'ትልቅ'] },
};

/** "Storage" → "storage" in a sentence; "RAM" stays (as the server does). */
export function lowerWord(text: string): string {
  return text.length > 1 &&
    text[0] === text[0]!.toUpperCase() &&
    text.slice(1) === text.slice(1).toLowerCase()
    ? text[0]!.toLowerCase() + text.slice(1)
    : text;
}

export function shopWords(
  shop: { shop_type: ShopType; option1: OptionLabel; option2: OptionLabel },
  language: Language,
): ShopWords {
  const [example1, example2] = (EXAMPLES[shop.shop_type] ?? EXAMPLES.clothing)[language];
  if (language === 'am') {
    const { am: one } = shop.option1;
    const { am: two } = shop.option2;
    return {
      opt1: one,
      opt2: two,
      Opt1: one,
      Opt2: two,
      plural1: one,
      plural2: two,
      Plural1: one,
      Plural2: two,
      example1,
      example2,
    };
  }
  return {
    opt1: lowerWord(shop.option1.en),
    opt2: lowerWord(shop.option2.en),
    Opt1: shop.option1.en,
    Opt2: shop.option2.en,
    plural1: lowerWord(shop.option1.plural),
    plural2: lowerWord(shop.option2.plural),
    Plural1: shop.option1.plural,
    Plural2: shop.option2.plural,
    example1,
    example2,
  };
}
