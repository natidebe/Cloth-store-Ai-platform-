import type { Language } from '@/state/preferences';

/** Phase 14: plans end on a day in Addis Ababa (UTC+3), like the server counts. */
const ADDIS_MS = 3 * 60 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;
export const GRACE_DAYS = 3;
export const PLAN_PRICES = { basic: 4500, pro: 9000 } as const;
export const PLAN_MONTHS = 3;

function addisDay(date: Date): number {
  return Math.floor((date.getTime() + ADDIS_MS) / DAY_MS);
}

/** Whole days from today to the end (0: ends today, negative: ended that many days ago). */
export function daysLeft(endsAt: string, now: Date = new Date()): number {
  return addisDay(new Date(endsAt)) - addisDay(now);
}

/** "Oct 11" / Amharic month names; `plusDays` for the pause day after the grace. */
export function planDay(endsAt: string, language: Language, plusDays = 0): string {
  const date = new Date(new Date(endsAt).getTime() + plusDays * DAY_MS);
  return date.toLocaleDateString(language === 'am' ? 'am-ET' : 'en-US', {
    month: 'short',
    day: 'numeric',
    timeZone: 'Africa/Addis_Ababa',
  });
}

/** The new end after a payment: months more from the current end, or from now if it ended. */
export function nextEnd(
  endsAt: string | null,
  months = PLAN_MONTHS,
  now: Date = new Date(),
): string {
  const start = endsAt && new Date(endsAt) > now ? new Date(endsAt) : now;
  const end = new Date(start);
  end.setMonth(end.getMonth() + months);
  return end.toISOString();
}

export type PlanState =
  | { kind: 'notStarted' }
  | { kind: 'ok' | 'soon'; days: number }
  | { kind: 'today' }
  | { kind: 'grace'; days: number }
  | { kind: 'paused' };

/** Where a shop stands: soon = 7 days or fewer left; grace = ended, not paused yet. */
export function planState(
  endsAt: string | null,
  suspendedReason: string | null,
  now: Date = new Date(),
): PlanState {
  if (suspendedReason === 'unpaid') return { kind: 'paused' };
  if (!endsAt) return { kind: 'notStarted' };
  const days = daysLeft(endsAt, now);
  if (days > 7) return { kind: 'ok', days };
  if (days > 0) return { kind: 'soon', days };
  if (days === 0) return { kind: 'today' };
  return { kind: 'grace', days };
}
