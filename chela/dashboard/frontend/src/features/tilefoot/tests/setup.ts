// The page state each footer test starts from, and a fetch stub that records every call.
import { act, cleanup } from '@testing-library/react';
import { afterEach, beforeEach, vi } from 'vitest';
import { agents } from '../../../shared/store/agents.ts';
import { context } from '../../../shared/store/context.ts';
import { closeDiffModal } from '../../diffmodal/index.ts';
import { recapOpen } from '../index.ts';

beforeEach(() => {
  localStorage.clear();
  agents.set([]);
  context.set([]);
  recapOpen.set(new Set());
  document.body.className = '';
});

afterEach(() => {
  cleanup();
  closeDiffModal();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

export type Call = { url: string; method: string; body: unknown };

/** Stub fetch: every call answers `body` (200); returns the calls made. */
export function stubFetch(body: unknown = {}) {
  const calls: Call[] = [];
  vi.stubGlobal('fetch', (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined });
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
  });
  return calls;
}

/** Let fetches and their state updates land. */
export async function settle() {
  for (let i = 0; i < 4; i++) await act(() => new Promise((r) => setTimeout(r, 0)));
}

/** `x`, or a test failure naming what was missing. */
export function must<T>(x: T | null | undefined, what = 'element'): T {
  if (x === null || x === undefined) throw new Error(`missing ${what}`);
  return x;
}
