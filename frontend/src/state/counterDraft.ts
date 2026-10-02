import { create } from 'zustand';

import type { CounterSaleInput, CounterSaleResult } from '@/api/types';

/**
 * A counter sale while it's being made (Phase 12): the items with their
 * listed and agreed prices, how the customer pays, and a request id that
 * makes "Confirm" safe to press twice. Kept across the Items → Payment →
 * Done screens; a new sale starts fresh.
 */
export interface CounterLine {
  variantId: string;
  productName: string;
  label: string; // "Black · 42"
  color: string | null;
  photoUrl: string | null;
  quantity: number;
  listed: number; // the listed price (never changed)
  price: string; // the agreed price per item, as typed
  available: number; // what may be sold without touching an online hold
  stock: number;
  /** An online order holds it and staff chose to sell anyway (D55). */
  heldAccepted: boolean;
}

interface CounterDraft {
  lines: CounterLine[];
  requestId: string;
  paymentMethod: string;
  paymentNote: string;
  customerName: string;
  customerPhone: string;
  note: string;
  result: CounterSaleResult | null;

  addLine: (line: CounterLine) => void;
  updateLine: (variantId: string, patch: Partial<CounterLine>) => void;
  removeLine: (variantId: string) => void;
  set: (
    fields: Partial<
      Pick<
        CounterDraft,
        'paymentMethod' | 'paymentNote' | 'customerName' | 'customerPhone' | 'note'
      >
    >,
  ) => void;
  acceptHolds: () => void;
  finish: (result: CounterSaleResult) => void;
  reset: () => void;
  input: () => CounterSaleInput;
}

function newRequestId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  // Older webviews: an RFC 4122 v4 id from Math.random (only for de-duplication).
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
  });
}

const empty = () => ({
  lines: [] as CounterLine[],
  requestId: newRequestId(),
  paymentMethod: 'Cash',
  paymentNote: '',
  customerName: '',
  customerPhone: '',
  note: '',
  result: null,
});

export const useCounterDraft = create<CounterDraft>()((set, get) => ({
  ...empty(),

  addLine: (line) =>
    set((s) => {
      const existing = s.lines.find((l) => l.variantId === line.variantId);
      if (!existing) return { lines: [...s.lines, line] };
      // The same size again: one line, the quantities added up.
      return {
        lines: s.lines.map((l) =>
          l.variantId === line.variantId
            ? { ...l, quantity: Math.min(l.quantity + line.quantity, l.stock), price: line.price }
            : l,
        ),
      };
    }),

  updateLine: (variantId, patch) =>
    set((s) => ({
      lines: s.lines.map((l) => (l.variantId === variantId ? { ...l, ...patch } : l)),
    })),

  removeLine: (variantId) =>
    set((s) => ({ lines: s.lines.filter((l) => l.variantId !== variantId) })),

  set: (fields) => set(fields),

  acceptHolds: () => set((s) => ({ lines: s.lines.map((l) => ({ ...l, heldAccepted: true })) })),

  finish: (result) => set({ result }),

  reset: () => set(empty()),

  input: () => {
    const s = get();
    return {
      items: s.lines.map((l) => ({
        variant_id: l.variantId,
        quantity: l.quantity,
        price: priceOf(l).toFixed(2),
      })),
      payment_method: s.paymentMethod.trim() || 'Cash',
      payment_note: s.paymentNote.trim() || null,
      customer_name: s.customerName.trim() || null,
      customer_phone: s.customerPhone.trim() || null,
      note: s.note.trim() || null,
      allow_held: s.lines.some((l) => l.heldAccepted),
      request_id: s.requestId,
    };
  },
}));

/** The agreed price of one item (the listed one until something is typed). */
export function priceOf(line: CounterLine): number {
  const typed = Number.parseFloat(line.price);
  return Number.isFinite(typed) ? typed : line.listed;
}

export function totals(lines: CounterLine[]) {
  const listed = lines.reduce((sum, l) => sum + l.listed * l.quantity, 0);
  const paid = lines.reduce((sum, l) => sum + priceOf(l) * l.quantity, 0);
  return { listed, paid, discount: Math.max(0, listed - paid) };
}

/** The lowest price staff may agree (D53); the owner: 0. */
export function lowestPrice(listed: number, staffPercent: number | null): number {
  if (staffPercent === null) return 0;
  return Math.round(listed * (100 - staffPercent)) / 100;
}

export function percentOff(listed: number, paid: number): number {
  if (listed <= 0 || paid >= listed) return 0;
  return Math.round(((listed - paid) / listed) * 100);
}
