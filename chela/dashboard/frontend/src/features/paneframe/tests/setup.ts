// The page state each pane-frame test starts from, and a fetch stub routed by path.
import { cleanup } from '@testing-library/react';
import { afterEach, beforeEach, vi } from 'vitest';
import { agents } from '../../../shared/store/agents.ts';
import { minimizedPanes } from '../../../shared/store/wall.ts';
import { view } from '../../../shared/store/view.ts';
import { configurePaneFrames, forgetReady, readiness, stopTermTimer, suspendedPanes } from '../index.ts';

export const host = { activeWid: vi.fn(() => ''), tick: vi.fn(), frameLoaded: vi.fn() };
configurePaneFrames(host);

let vis: DocumentVisibilityState = 'visible';
Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => vis });
Object.defineProperty(document, 'hidden', { configurable: true, get: () => vis === 'hidden' });

/** Hide or show the tab, firing the same event a real browser does. */
export function setVisible(on: boolean): void {
  vis = on ? 'visible' : 'hidden';
  document.dispatchEvent(new Event('visibilitychange'));
}

beforeEach(() => {
  vis = 'visible';
  localStorage.clear();
  agents.set([]);
  view.set('terminals');
  minimizedPanes.set(new Set());
  suspendedPanes.set(new Set());
  for (const wid of [...readiness.get().keys()]) forgetReady(wid);
  host.activeWid.mockReturnValue('');
  host.tick.mockClear();
  host.frameLoaded.mockClear();
});

afterEach(() => {
  cleanup();
  stopTermTimer();
  document.body.innerHTML = '';
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

export type Call = { path: string; method: string; body: unknown };

/** Stub fetch: `route(path)` answers each call (200, JSON); returns the calls made. */
export function stubFetch(route: (path: string) => unknown = () => ({})) {
  const calls: Call[] = [];
  vi.stubGlobal('fetch', (url: string, init?: RequestInit) => {
    const raw = init?.body;
    calls.push({ path: String(url), method: init?.method ?? 'GET', body: typeof raw === 'string' ? JSON.parse(raw) : raw });
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(route(String(url))) });
  });
  return calls;
}

/** A stage holding one terminal slot per wid, as terminals.js draws them. */
export function stage(...wids: string[]): HTMLElement {
  const el = document.createElement('div');
  el.id = 'term-stage';
  el.innerHTML = wids
    .map((w) => `<div class="grid-stack-item-content" data-pane="${w}"><div class="island-slot" data-frame-for="${w}"></div></div>`)
    .join('');
  document.body.append(el);
  return el;
}

export const frame = (wid: string) => document.querySelector<HTMLIFrameElement>(`[data-pane="${wid}"] iframe.term-frame`);
export const pending = (wid: string) => document.querySelector<HTMLElement>(`[data-pane="${wid}"] .term-pending`);

export function must<T>(x: T | null | undefined, what = 'element'): T {
  if (x === null || x === undefined) throw new Error(`missing ${what}`);
  return x;
}
