import { create } from 'zustand';

/** Short messages at the bottom ("Saved", an error from the server). */
export interface Toast {
  id: number;
  text: string;
  tone: 'info' | 'error';
}

interface Toasts {
  toasts: Toast[];
  show: (text: string, tone?: Toast['tone']) => void;
  dismiss: (id: number) => void;
}

let nextId = 1;

export const useToasts = create<Toasts>()((set, get) => ({
  toasts: [],
  show: (text, tone = 'info') => {
    const id = nextId++;
    set((s) => ({ toasts: [...s.toasts.slice(-2), { id, text, tone }] }));
    window.setTimeout(() => get().dismiss(id), tone === 'error' ? 5000 : 2500);
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));

/** For code outside components (e.g. mutation callbacks). */
export const toast = {
  info: (text: string) => useToasts.getState().show(text, 'info'),
  error: (text: string) => useToasts.getState().show(text, 'error'),
};
