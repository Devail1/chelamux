// What jsdom lacks and Radix's popper and pointer handling call, a fetch stub that records
// every call, and the page state each test starts from.
import { act, cleanup, fireEvent } from '@testing-library/react';
import { afterEach, beforeEach, vi } from 'vitest';
import { agents } from '../../../shared/store/agents.ts';
import { orchestrator } from '../../../shared/store/orchestrator.ts';
import { pinnedPanes } from '../../../shared/store/wall.ts';
import { closeShareDialog, shares } from '../../share/index.ts';

globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
};
Element.prototype.hasPointerCapture ??= () => false;
Element.prototype.releasePointerCapture ??= () => {};
Element.prototype.scrollIntoView ??= () => {};

/** A phone (≤768px) or a desktop, for useMediaQuery. */
export function viewport(phone: boolean) {
  vi.stubGlobal('matchMedia', (q: string) => ({
    media: q,
    matches: phone && q.includes('max-width: 768px'),
    addEventListener() {},
    removeEventListener() {},
  }));
}

beforeEach(() => {
  viewport(false);
  localStorage.clear();
  agents.set([]);
  shares.set(new Map());
  pinnedPanes.set(new Set());
  orchestrator.set({ wid: null, name: null, state: 'unregistered', why: '', queued: 0 });
  document.body.className = '';
});

afterEach(() => {
  cleanup();
  closeShareDialog();   // its own root, outside RTL's
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

/** `x`, or a test failure naming what was missing. */
export function must<T>(x: T | null | undefined, what = 'element'): T {
  if (x === null || x === undefined) throw new Error(`missing ${what}`);
  return x;
}

export type Call = { url: string; method: string; body: unknown };

/** Stub fetch: `answer(url, method)` returns [status, JSON body], or throws. Records every call. */
export function stubFetch(answer: (url: string, method: string) => [number, unknown]) {
  const calls: Call[] = [];
  vi.stubGlobal('fetch', (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    try {
      const [status, body] = answer(url, method);
      return Promise.resolve({ ok: status < 400, status, json: () => (body instanceof Error ? Promise.reject(body) : Promise.resolve(body)) });
    } catch (e) {
      return Promise.reject(e instanceof Error ? e : new Error(String(e)));
    }
  });
  return calls;
}

/** Let fetches and their state updates land. */
export async function settle() {
  for (let i = 0; i < 4; i++) await act(() => new Promise((r) => setTimeout(r, 0)));
}

/** Open the header's ⋯ menu the way the keyboard does (Enter toggles, so an open one is
 * returned as it is); returns the menu. */
export function openMenu(root: ParentNode = document): HTMLElement {
  const trigger = must(root.querySelector('.gs-menu-btn'), 'menu trigger');
  if (trigger.getAttribute('aria-expanded') !== 'true') fireEvent.keyDown(trigger, { key: 'Enter' });
  return must(document.querySelector<HTMLElement>('.pane-overflow-menu'), 'open menu');
}
