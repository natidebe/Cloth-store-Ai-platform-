import { create } from 'zustand';

import type { GridRow, Product } from '@/api/types';

/**
 * The "Stock and prices" grid while it's being edited: colors × sizes, the
 * stock and own price of each cell, which cell is selected, and what will be
 * removed. Nothing reaches the server until Save (one PUT of the whole grid).
 */
export interface Cell {
  id?: string; // an existing variant
  stock: number;
  price: string; // "" = the product's price
}

interface GridDraft {
  productId: string | null;
  colors: string[];
  sizes: string[];
  cells: Record<string, Cell>;
  selected: string | null; // cell key
  removed: Record<string, string>; // cell key -> variant id, deleted on Save
  dirty: boolean;

  load: (product: Product) => void;
  select: (key: string | null) => void;
  setStock: (key: string, stock: number) => void;
  setPrice: (key: string, price: string) => void;
  addColor: (color: string) => boolean;
  addSize: (size: string) => boolean;
  removeCell: (key: string) => void;
  rows: () => GridRow[];
  removedIds: () => string[];
}

export const cellKey = (color: string, size: string) => `${color}\u0000${size}`;
export const splitKey = (key: string) => key.split('\u0000') as [string, string];

const SIZE_ORDER = ['XXS', 'XS', 'S', 'M', 'L', 'XL', 'XXL', 'XXXL'];

/** S, M, L, XL in clothing order; shoe sizes as numbers; anything else after. */
export function sortSizes(sizes: string[]): string[] {
  const rank = (size: string) => {
    const known = SIZE_ORDER.indexOf(size.toUpperCase());
    if (known >= 0) return [0, known] as const;
    const number = Number.parseFloat(size);
    return Number.isNaN(number) ? ([2, 0] as const) : ([1, number] as const);
  };
  return [...sizes].sort((a, b) => {
    const [ga, va] = rank(a);
    const [gb, vb] = rank(b);
    return ga - gb || va - vb || a.localeCompare(b);
  });
}

const same = (a: string, b: string) => a.trim().toLowerCase() === b.trim().toLowerCase();

export const useGridDraft = create<GridDraft>()((set, get) => ({
  productId: null,
  colors: [],
  sizes: [],
  cells: {},
  selected: null,
  removed: {},
  dirty: false,

  load: (product) => {
    const cells: Record<string, Cell> = {};
    const colors: string[] = [];
    const sizes: string[] = [];
    for (const v of product.variants) {
      const color = v.color ?? '';
      const size = v.size ?? '';
      if (!colors.includes(color)) colors.push(color);
      if (!sizes.includes(size)) sizes.push(size);
      cells[cellKey(color, size)] = {
        id: v.id,
        stock: v.stock,
        price:
          v.price_override === null || v.price_override === undefined
            ? ''
            : String(v.price_override),
      };
    }
    const sortedSizes = sortSizes(sizes);
    const first =
      colors[0] !== undefined && sortedSizes[0] !== undefined
        ? cellKey(colors[0], sortedSizes[0])
        : null;
    set({
      productId: product.id,
      colors,
      sizes: sortedSizes,
      cells,
      selected: first && cells[first] ? first : null,
      removed: {},
      dirty: false,
    });
  },

  select: (key) => {
    if (key && !get().cells[key]) {
      // An empty cell: start a variant there (the same one again if it was
      // removed before saving).
      set((s) => {
        const { [key]: earlierId, ...removed } = s.removed;
        const cell: Cell = earlierId
          ? { id: earlierId, stock: 0, price: '' }
          : { stock: 0, price: '' };
        return { cells: { ...s.cells, [key]: cell }, removed, dirty: true };
      });
    }
    set({ selected: key });
  },

  setStock: (key, stock) =>
    set((s) => {
      const cell = s.cells[key];
      if (!cell) return s;
      return {
        cells: { ...s.cells, [key]: { ...cell, stock: Math.max(0, Math.min(100_000, stock)) } },
        dirty: true,
      };
    }),

  setPrice: (key, price) =>
    set((s) => {
      const cell = s.cells[key];
      if (!cell) return s;
      return { cells: { ...s.cells, [key]: { ...cell, price } }, dirty: true };
    }),

  addColor: (color) => {
    const name = color.trim();
    if (!name || get().colors.some((c) => same(c, name))) return false;
    set((s) => ({ colors: [...s.colors, name], dirty: true }));
    return true;
  },

  addSize: (size) => {
    const name = size.trim();
    if (!name || get().sizes.some((x) => same(x, name))) return false;
    set((s) => ({ sizes: sortSizes([...s.sizes, name]), dirty: true }));
    return true;
  },

  removeCell: (key) =>
    set((s) => {
      const cell = s.cells[key];
      if (!cell) return s;
      const cells = { ...s.cells };
      delete cells[key];
      return {
        cells,
        removed: cell.id ? { ...s.removed, [key]: cell.id } : s.removed,
        selected: s.selected === key ? null : s.selected,
        dirty: true,
      };
    }),

  rows: () =>
    Object.entries(get().cells).map(([key, cell]) => {
      const [color, size] = splitKey(key);
      return {
        ...(cell.id ? { id: cell.id } : {}),
        color: color || null,
        size: size || null,
        stock: cell.stock,
        price: cell.price.trim() === '' ? null : cell.price.trim(),
      };
    }),

  removedIds: () => Object.values(get().removed),
}));
