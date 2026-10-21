// What jsdom lacks and Radix's popper and pointer handling call.
import { cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';

globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
};
Element.prototype.hasPointerCapture ??= () => false;
Element.prototype.releasePointerCapture ??= () => {};
Element.prototype.scrollIntoView ??= () => {};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

/** `x`, or a test failure naming what was missing. */
export function must<T>(x: T | null | undefined, what = 'element'): T {
  if (x === null || x === undefined) throw new Error(`missing ${what}`);
  return x;
}

export type Call = { url: string; init?: RequestInit };

/** Stub fetch: `answer(url, init)` returns the JSON body, or throws. Records every call. */
export function stubFetch(answer: (url: string, init?: RequestInit) => unknown) {
  const calls: Call[] = [];
  vi.stubGlobal('fetch', (url: string, init?: RequestInit) => {
    calls.push(init ? { url, init } : { url });
    try {
      const body = answer(url, init);
      return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
    } catch (e) {
      return Promise.reject(e instanceof Error ? e : new Error(String(e)));
    }
  });
  return calls;
}
