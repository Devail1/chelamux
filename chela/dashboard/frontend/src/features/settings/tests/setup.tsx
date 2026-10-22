// What the Settings tests share: jsdom's gaps for Radix, a fetch routed by path (every call
// recorded), the page's real style.css (so "visible" is the real cascade), and opening the modal.
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import STYLE_CSS from '../../../../../static/style.css?raw';
import { afterAll, afterEach, beforeAll, beforeEach, vi } from 'vitest';
import { Settings, closeSettings, openSettings, selectSettingsTab } from '../index.ts';

// Each test opens the whole modal (every tab renders, for the search): under a loaded machine
// (pytest -n auto runs this beside everything else) that outlasts Vitest's 5 s default.
vi.setConfig({ testTimeout: 30_000 });

globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
};
Element.prototype.hasPointerCapture ??= () => false;
Element.prototype.releasePointerCapture ??= () => {};
Element.prototype.scrollIntoView ??= () => {};

/** `x`, or a test failure naming what was missing. */
export function must<T>(x: T | null | undefined, what = 'element'): T {
  if (x === null || x === undefined) throw new Error(`missing ${what}`);
  return x;
}

export type Call = { path: string; url: string; method: string; body: unknown };
/** Every request, in order. */
export const calls: Call[] = [];
/** Per path (no query): a body, a function of the call returning one (or a promise), or an
 * Error to reject with. A path not listed answers `{}`. */
export const routes: Record<string, unknown> = {};

beforeAll(() => {
  const sheet = document.createElement('style');
  sheet.textContent = STYLE_CSS;
  document.head.append(sheet);
  afterAll(() => sheet.remove());
});

beforeEach(() => {
  calls.length = 0;
  for (const k of Object.keys(routes)) Reflect.deleteProperty(routes, k);
  localStorage.clear();
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const u = new URL(String(url), 'http://localhost/');
    const call = { path: u.pathname, url: u.pathname + u.search, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : null };
    calls.push(call);
    let r = routes[`${call.method} ${call.path}`] ?? routes[call.path];
    if (typeof r === 'function') r = await (r as (c: Call) => unknown)(call);
    if (r instanceof Error) throw r;
    return { ok: true, status: 200, json: () => Promise.resolve(r ?? {}) };
  });
});

afterEach(() => {
  act(() => closeSettings());
  act(() => selectSettingsTab('general'));
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** Let pending fetches settle and React commit. */
export async function settle(n = 3) {
  for (let i = 0; i < n; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

/** Render Settings, open it (on `tab`), and let the tabs' reads land. */
export async function openOn(tab?: string, focus?: string) {
  render(<Settings />);
  act(() => openSettings(focus));
  if (tab) act(() => selectSettingsTab(tab));
  await settle();
}

export const posts = (path: string) => calls.filter((c) => c.method === 'POST' && c.path === path).map((c) => c.body);

/** Visible = nothing from `el` up computes to display:none, and nothing is [hidden]. */
export function visible(el: Element | null): boolean {
  for (let n = el; n && n !== document.body; n = n.parentElement) {
    if (getComputedStyle(n).display === 'none' || (n as HTMLElement).hidden) return false;
  }
  return !!el;
}

/** The FieldRow a control (by id) sits in. */
export const rowOf = (id: string) => must(document.getElementById(id)?.closest('.field-row'), `row of #${id}`);

export const sectionTitled = (title: string) =>
  must([...document.querySelectorAll('#drawer-body .settings-section')].find((s) => s.querySelector('h4')?.textContent === title), `section ${title}`);

/** Choose `label` in a kit Select, by keyboard. */
export async function pick(id: string, label: string) {
  fireEvent.keyDown(must(document.getElementById(id), `#${id}`), { key: 'Enter' });
  fireEvent.click(screen.getByRole('option', { name: label }));
  await settle();
}

export async function type(el: Element, value: string) {
  fireEvent.change(el, { target: { value } });
  await settle(1);
}
