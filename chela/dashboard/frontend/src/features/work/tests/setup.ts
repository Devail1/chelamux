// What jsdom lacks and Radix (the task modal, the toasts) and useMediaQuery call.
import { cleanup } from '@testing-library/react';
import { afterEach, beforeEach, vi } from 'vitest';

globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
};
Element.prototype.hasPointerCapture ??= () => false;
Element.prototype.releasePointerCapture ??= () => {};

/** What `(max-width: 768px)` answers: the phone layout's switch. */
export const viewport = { phone: false };

beforeEach(() => {
  localStorage.clear();
  viewport.phone = false;
  window.matchMedia = ((q: string) => ({ media: q, matches: viewport.phone, addEventListener() {}, removeEventListener() {} })) as unknown as typeof window.matchMedia;
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** `x`, or a test failure naming what was missing. */
export function must<T>(x: T | null | undefined, what = 'element'): T {
  if (x === null || x === undefined) throw new Error(`missing ${what}`);
  return x;
}

export type Posted = { url: string; method?: string; body: unknown };

/** Stub fetch: every call recorded; `reply` answers by URL. */
export function stubFetch(reply: (url: string) => { status?: number; body?: unknown } | Promise<never> = () => ({ body: { ok: true } })) {
  const calls: Posted[] = [];
  vi.stubGlobal('fetch', async (url: string, init: RequestInit = {}) => {
    calls.push({ url, method: init.method, body: init.body ? JSON.parse(String(init.body)) : undefined });
    const r = await reply(url);
    const status = r.status ?? 200;
    return { ok: status < 400, status, json: () => Promise.resolve(r.body) };
  });
  return calls;
}
