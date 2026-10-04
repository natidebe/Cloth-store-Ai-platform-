import type { Money } from '@/api/types';
import type { Language } from '@/state/preferences';

/** "3,500 ETB" / "3,500 ብር"; decimals only when there are any. */
export function money(value: Money | null | undefined, language: Language): string {
  if (value === null || value === undefined || value === '') return '—';
  const number = typeof value === 'number' ? value : Number(value);
  if (Number.isNaN(number)) return '—';
  const digits = Number.isInteger(number) ? 0 : 2;
  const amount = number.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: 2,
  });
  return `${amount} ${language === 'am' ? 'ብር' : 'ETB'}`;
}

/** "3,500 – 3,800 ETB" for a product whose variants cost different amounts. */
export function priceRange(min: Money | null, max: Money | null, language: Language): string {
  if (min === null) return '—';
  if (max === null || Number(min) === Number(max)) return money(min, language);
  return `${money(min, language).split(' ')[0]} – ${money(max, language)}`;
}

export function percent(rate: number): string {
  return `${Math.round(rate * 100)}%`;
}

/** "2 Oct, 14:05" in Addis Ababa time (the shop's time). */
export function orderTime(iso: string, language: Language): string {
  return new Date(iso).toLocaleString(language === 'am' ? 'am-ET' : 'en-GB', {
    timeZone: 'Africa/Addis_Ababa',
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/** "Mon 2" for a chart bar. */
export function dayLabel(day: string, language: Language): string {
  const date = new Date(`${day}T12:00:00+03:00`);
  return date.toLocaleDateString(language === 'am' ? 'am-ET' : 'en-GB', {
    timeZone: 'Africa/Addis_Ababa',
    weekday: 'short',
    day: 'numeric',
  });
}

/** "Black · M" (or just one of them). */
export function variantLabel(color: string | null, size: string | null): string {
  return [color, size].filter(Boolean).join(' · ');
}

/** Keywords are stored as "jacket, denim, ጃኬት". */
export function splitKeywords(value: string | null | undefined): string[] {
  return (value ?? '')
    .split(',')
    .map((k) => k.trim())
    .filter(Boolean);
}

export function joinKeywords(keywords: string[]): string | null {
  return keywords.length ? keywords.join(', ') : null;
}

export function capitalize(text: string): string {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
}

/** This month and the ones before it, as "2026-10", in Addis Ababa (Phase 15: the export). */
export function recentMonths(now = new Date(), count = 4): string[] {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Africa/Addis_Ababa',
    year: 'numeric',
    month: '2-digit',
  }).formatToParts(now);
  const part = (type: 'year' | 'month') => Number(parts.find((p) => p.type === type)?.value);
  const current = part('year') * 12 + part('month') - 1;
  return Array.from({ length: count }, (_, back) => {
    const index = current - back;
    return `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, '0')}`;
  });
}

/** "September 2026" for "2026-09". */
export function monthName(month: string, language: Language): string {
  return new Date(`${month}-15T12:00:00+03:00`).toLocaleDateString(
    language === 'am' ? 'am-ET' : 'en-GB',
    { month: 'long', year: 'numeric', timeZone: 'Africa/Addis_Ababa' },
  );
}
