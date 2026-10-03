import type { ShopType, ShopTypeInfo } from '@/api/types';
import { haptic } from '@/lib/telegram';
import type { Language } from '@/state/preferences';

import s from './shopTypes.module.css';

const ICONS: Record<ShopType, string> = {
  clothing: '👗',
  electronics: '📱',
  cosmetics: '💄',
  general: '🛍️',
};

/**
 * "What kind of shop?" (Phase 13): the four types, each with the words it
 * gives a product's options ("Color · Storage"). Used when a shop is created
 * and in Settings → Shop type & words.
 */
export function ShopTypeChoices({
  types,
  value,
  onChange,
  language,
  label,
}: {
  types: ShopTypeInfo[];
  value: ShopType | null;
  onChange: (type: ShopType) => void;
  language: Language;
  label: string;
}) {
  return (
    <div className={s.choices} role="radiogroup" aria-label={label}>
      {types.map((info) => {
        const on = info.type === value;
        return (
          <button
            key={info.type}
            type="button"
            role="radio"
            aria-checked={on}
            className={`${s.choice} ${on ? s.choiceOn : ''}`}
            onClick={() => {
              haptic.select();
              onChange(info.type);
            }}
          >
            <span className={s.icon} aria-hidden="true">
              {ICONS[info.type]}
            </span>
            <span className={s.main}>
              <span className={s.name}>{info.names[language]}</span>
              <span className={s.words}>
                {info.option1[language]} · {info.option2[language]}
              </span>
            </span>
            <span className={`${s.radio} ${on ? s.radioOn : ''}`} aria-hidden="true" />
          </button>
        );
      })}
    </div>
  );
}
