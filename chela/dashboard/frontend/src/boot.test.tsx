// CMX-248 (islands.test.tsx until then): what the entry does, mountPage() then start(). The
// page's polls, its event stream and the share state are mocked: start() is pinned by what it
// calls, the shell by what it draws.
import { act, cleanup } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { mountPage, REFRESH_MS, start } from './boot.ts';
import { startShares } from './features/share/index.ts';
import { BOOT } from './shared/api/index.ts';
import { openEvents } from './shared/events/index.ts';
import { refresh, setViewTicks, startScheduler } from './shared/scheduler/index.ts';
import { view } from './shared/store/view.ts';

vi.mock('./shared/scheduler/index.ts', async (orig) => ({
  ...(await orig<object>()), refresh: vi.fn(() => Promise.resolve()), setViewTicks: vi.fn(), startScheduler: vi.fn(),
}));
vi.mock('./shared/events/index.ts', async (orig) => ({ ...(await orig<object>()), openEvents: vi.fn() }));
vi.mock('./features/share/index.ts', async (orig) => ({ ...(await orig<object>()), startShares: vi.fn() }));

let unmount = () => {};
beforeEach(() => {
  vi.stubGlobal('fetch', () => new Promise(() => {}));
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  Reflect.deleteProperty(window, 'chelaBridge');
  Reflect.deleteProperty(window, 'chela');
  vi.clearAllMocks();
});
afterEach(() => {
  act(() => unmount());
  unmount = () => {};
  cleanup();
  document.body.innerHTML = '';
  localStorage.clear();
  view.set('work');
  vi.unstubAllGlobals();
});

function page(html = '<div id="root"></div>') {
  document.body.innerHTML = html;
  const root = mountPage(window);
  unmount = () => root?.unmount();
  return root;
}

test('mountPage installs the bridge, the saved theme and the page keys, and draws the shell into #root', async () => {
  localStorage.setItem('chela_theme', 'nord');
  expect(page()).not.toBeNull();
  expect(window.chelaBridge?.agents.get()).toEqual([]);
  expect(document.body.dataset.theme).toBe('nord');
  expect(document.querySelector('#root > .app > aside.sidebar')).not.toBeNull();
  const { closePalette, palette } = await import('./features/palette/index.ts');
  act(() => {
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true, bubbles: true }));
  });
  expect(palette.get().open).toBe(true);
  act(() => closePalette());
});

test('window.chela is exactly the panes\' contract (FRONTEND_PORT_PLAN.md §3, §6.3)', async () => {
  page();
  const chela = (window as unknown as { chela: { openPalette(): void; toggleSettings(): void } }).chela;
  expect(Object.keys(chela).sort()).toEqual(['openPalette', 'toggleSettings']);
  const { settings, toggleSettings } = await import('./features/settings/index.ts');
  const { closePalette, palette } = await import('./features/palette/index.ts');
  act(() => chela.toggleSettings());
  expect(settings.get().open).toBe(true);
  act(() => toggleSettings());
  act(() => chela.openPalette());
  expect(palette.get().open).toBe(true);
  act(() => closePalette());
});

test('a page without #root still gets the bridge, the theme and the contract, and no shell', () => {
  localStorage.setItem('chela_theme', 'nord');
  expect(page('')).toBeNull();
  expect([!!window.chelaBridge, document.body.dataset.theme, document.body.innerHTML]).toEqual([true, 'nord', '']);
  expect(Object.keys((window as unknown as { chela: object }).chela).sort()).toEqual(['openPalette', 'toggleSettings']);
});

test('start: the registry\'s view ticks, the nav, the shares, the Wall as the first view, the loop and the stream', () => {
  page();
  start();
  const find = vi.mocked(setViewTicks).mock.calls[0]?.[0];
  expect([typeof find?.('terminals'), find?.('work'), find?.('nope')]).toEqual(['function', null, null]);
  expect(window.chelaBridge?.nav.get().items.map((v) => v.id)).toEqual(['terminals', 'work']);
  expect(vi.mocked(startShares)).toHaveBeenCalledTimes(1);
  expect([view.get(), document.querySelector('.panel.active')?.id]).toEqual(['terminals', 'panel-terminals']);
  expect(vi.mocked(refresh)).toHaveBeenCalledTimes(1); // selectView's, the boot tick
  expect(vi.mocked(startScheduler).mock.calls).toEqual([[30000]]);
  expect(REFRESH_MS).toBe(30000);
  expect(vi.mocked(openEvents)).toHaveBeenCalledTimes(1);
});

test('start with terminals off: Work stays the view, and the boot tick runs on its own', () => {
  const was = BOOT.terminalsEnabled;
  BOOT.terminalsEnabled = false;
  try {
    page();
    start();
    expect([view.get(), document.querySelector('.panel.active')?.id]).toEqual(['work', 'panel-work']);
    expect(vi.mocked(refresh)).toHaveBeenCalledTimes(1);
  } finally {
    BOOT.terminalsEnabled = was;
  }
});
