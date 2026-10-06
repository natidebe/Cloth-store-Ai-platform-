import type { Language } from '@/state/preferences';

/**
 * The Ethiopian calendar (ዓ.ም.), for the accountant's export (Phase 15c, D79).
 * The same conversion as the backend's app/agents/ethiopian.py: through the
 * Julian day number, with the Amete Mihret epoch.
 */
const EPOCH = 1723856;

export const MONTHS_AM = [
  'መስከረም',
  'ጥቅምት',
  'ኅዳር',
  'ታኅሣሥ',
  'ጥር',
  'የካቲት',
  'መጋቢት',
  'ሚያዝያ',
  'ግንቦት',
  'ሰኔ',
  'ሐምሌ',
  'ነሐሴ',
  'ጳጉሜ',
];
export const MONTHS_EN = [
  'Meskerem',
  'Tikimt',
  'Hidar',
  'Tahsas',
  'Tir',
  'Yekatit',
  'Megabit',
  'Miyazya',
  'Ginbot',
  'Sene',
  'Hamle',
  'Nehase',
  'Pagume',
];

/** [year, month 1–13, day] of a Gregorian date (year, month 1–12, day). */
export function toEthiopian(year: number, month: number, day: number): [number, number, number] {
  const a = Math.floor((14 - month) / 12);
  const y = year + 4800 - a;
  const m = month + 12 * a - 3;
  const jdn =
    day +
    Math.floor((153 * m + 2) / 5) +
    365 * y +
    Math.floor(y / 4) -
    Math.floor(y / 100) +
    Math.floor(y / 400) -
    32045;
  const r = (jdn - EPOCH) % 1461;
  const n = (r % 365) + 365 * Math.floor(r / 1460);
  const ethiopianYear =
    4 * Math.floor((jdn - EPOCH) / 1461) + Math.floor(r / 365) - Math.floor(r / 1460);
  return [ethiopianYear, Math.floor(n / 30) + 1, (n % 30) + 1];
}

/** Today in Addis Ababa, as [year, month, day] (Gregorian). */
function addisToday(now: Date): [number, number, number] {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Africa/Addis_Ababa',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(now);
  const part = (type: 'year' | 'month' | 'day') =>
    Number(parts.find((p) => p.type === type)?.value);
  return [part('year'), part('month'), part('day')];
}

/** This Ethiopian month and the ones before it, as "2019-01" (Pagume counts as Nehase). */
export function recentEthiopianMonths(now = new Date(), count = 4): string[] {
  const [year, month] = toEthiopian(...addisToday(now));
  const current = year * 12 + Math.min(month, 12) - 1;
  return Array.from({ length: count }, (_, back) => {
    const index = current - back;
    return `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, '0')}`;
  });
}

/** "መስከረም 2019" / "Meskerem 2019"; Nehase's export includes Pagume. */
export function ethiopianMonthName(month: string, language: Language): string {
  const [year, number] = month.split('-').map(Number);
  const names = language === 'am' ? MONTHS_AM : MONTHS_EN;
  const name = names[(number ?? 1) - 1] ?? '';
  return `${number === 12 ? `${name} + ${names[12]}` : name} ${year}`;
}
