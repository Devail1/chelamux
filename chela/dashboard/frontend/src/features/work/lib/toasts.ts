// The Work feature's floating toasts: a run's state change (SSE `runs`) and board-level notes
// that belong to no single card. WorkToasts.tsx draws them in the kit's toast stack.
import { createStore } from '../../../shared/store/index.ts';

export type ToastItem = { id: number; title: string; href?: string };

export const toasts = createStore<ToastItem[]>([]);
let next = 0;

export function showToast(title: string, href?: string): void {
  toasts.set([...toasts.get(), { id: ++next, title, ...(href ? { href } : {}) }]);
}

export function dismissToast(id: number): void {
  toasts.set(toasts.get().filter((t) => t.id !== id));
}
