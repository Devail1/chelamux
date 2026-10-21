// CMX-221: the ⌘K palette, the shortcuts sheet and the page keys, ported from
// tests/palette_settings.test.mjs (CMX-401), shortcuts.test.mjs (CMX-121) and
// settings_shortcut.test.mjs's document half (CMX-385). The wall-side rows (open panes first,
// "@N", share) are driven through the real wall in tests/wallnav, window_id and share_dialog.
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, expect, test, vi } from 'vitest';
import { agents } from '../../../shared/store/agents.ts';
import { launcher } from '../../../shared/store/launcher.ts';
import { Settings, closeSettings, openSettings, settings } from '../../settings/index.ts';
import { Palette, Shortcuts, closePalette, closeShortcuts, installPalette, openPalette, palette, paletteRows, setPaletteHost, shortcuts, sidebarJumpInput, type PaletteHost } from '../index.ts';
import { fuzzyScore, rankItems, type PaletteItem } from '../model.ts';

globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
};
Element.prototype.scrollIntoView ??= () => {};

const host = {
  views: () => [{ id: 'terminals', label: 'Wall' }, { id: 'work', label: 'Work' }],
  label: (a: { name?: string; title?: string }) => String(a.title ?? a.name),
  dotColor: (a: { s?: string }) => a.s ?? 'grey',
  selectView: vi.fn(), selectAgent: vi.fn(), showAgentDetail: vi.fn(),
  resolveWid: (q: string, wids: string[]) => (wids.includes(q.trim()) ? q.trim() : null),
  wall: {
    rendered: () => ['@2', '@1', '@9'], minimized: () => new Set(['@9']), ordered: (w: string[]) => w.slice().sort(),
    visible: vi.fn(() => true), showWall: vi.fn(), focus: vi.fn(),
  },
} satisfies PaletteHost;

let uninstall = () => {};
beforeAll(() => setPaletteHost(host));
beforeEach(() => {
  vi.clearAllMocks();
  agents.set([{ name: 'alpha', window_id: '@1', title: 'Alpha' }, { name: 'bravo', window_id: '@2', s: 'yellow' }, { name: 'charlie', window_id: '@3' }]);
  launcher.set({ favorites: [{ path: '/p/fav', label: 'fav' }], recent: [{ path: '/p/fav' }, { path: '/p/rec' }, null] });
  vi.stubGlobal('fetch', () => Promise.resolve({ json: () => Promise.resolve({}) }));
  uninstall = installPalette(window);
  render(<><Settings /><Palette /><Shortcuts /></>);
});
afterEach(() => {
  act(() => { closePalette(); closeShortcuts(); closeSettings(); });
  uninstall();
  cleanup();
  vi.unstubAllGlobals();
});

const key = (init: KeyboardEventInit, target: Element | Document = document.activeElement ?? document) => {
  const ev = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init });
  act(() => { target.dispatchEvent(ev); });
  return ev;
};
const input = () => document.getElementById('palette-input') as HTMLInputElement;
const items = () => [...document.querySelectorAll<HTMLElement>('#palette-list .palette-item')];
const titles = () => items().map((r) => r.querySelector('.pi-title')?.textContent);
const subs = () => items().map((r) => r.querySelector('.pi-sub')?.textContent);
const row = (title: string) => {
  const r = items().find((x) => x.querySelector('.pi-title')?.textContent === title);
  if (!r) throw new Error(`no row "${title}" in ${JSON.stringify(titles())}`);
  return r;
};
const item = (i: number) => {
  const r = items()[i];
  if (!r) throw new Error(`no row ${i}`);
  return r;
};
const typeQ = (q: string) => fireEvent.change(input(), { target: { value: q } });
const open = () => act(() => openPalette());

test('opened empty: the live panes first (wall order, attention first), a divider, then views, sessions, shares, launches, actions', () => {
  open();
  expect(screen.getByRole('dialog', { name: 'Command palette' })).toBe(document.getElementById('palette'));
  expect(document.activeElement).toBe(input());
  expect(titles().slice(0, 2)).toEqual(['bravo', 'Alpha']); // @9 is minimized; bravo waits on you
  expect(subs().slice(0, 2)).toEqual(['open pane · waiting', 'open pane · idle']);
  const list = [...(document.getElementById('palette-list')?.children ?? [])];
  expect(list[2]?.className).toBe('palette-divider');
  expect(titles().slice(2)).toEqual(['Wall', 'Work', 'charlie', 'Share Alpha', 'Share bravo', 'Share charlie', 'Launch fav', 'Launch /p/rec',
    'New shell window', 'New task', 'Add scheduled task', 'Keyboard shortcuts', 'Settings']);
  expect(subs()[4]).toBe('session · idle · @3');
  expect(items()[0]?.querySelector('.term-status-dot')?.className).toBe('term-status-dot waiting');
  expect(row('Wall').querySelector('.pi-glyph svg')).not.toBeNull();
});

test('typing drops the panes section and fuzzy-matches every row; a miss says "No matches"', () => {
  open();
  typeQ('wor');
  expect(document.querySelector('.palette-divider')).toBeNull();
  expect(titles()[0]).toBe('Work');
  typeQ('zzqq');
  expect(items()).toEqual([]);
  expect(document.querySelector('.palette-empty')?.textContent).toBe('No matches');
});

test('"@3" pins the exact window first, once; running it jumps (never toggles), switching to the wall', () => {
  host.wall.visible.mockReturnValue(false);
  open();
  typeQ('@3');
  expect(subs()[0]).toBe('window · @3');
  expect(subs().filter((s) => s?.endsWith('@3')).length).toBe(1);
  fireEvent.click(item(0));
  expect(host.wall.showWall).toHaveBeenCalledOnce();
  expect(host.wall.focus.mock.calls).toEqual([['@3']]);
  expect(host.selectAgent).not.toHaveBeenCalled();
  expect(palette.get().open).toBe(false);
});

test('a session row runs the row click (selectAgent); a view row its view; a share row shares that pane', async () => {
  open();
  typeQ('charlie');
  fireEvent.click(row('charlie'));
  expect(host.selectAgent.mock.calls).toEqual([['charlie']]);
  open();
  typeQ('work');
  fireEvent.click(row('Work'));
  expect(host.selectView.mock.calls).toEqual([['work']]);
  const fetches = vi.fn((...args: [string]) => Promise.resolve({ json: () => Promise.resolve(args.length ? {} : null) }));
  vi.stubGlobal('fetch', fetches);
  open();
  typeQ('share bravo');
  fireEvent.click(row('Share bravo'));
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  // the share feature's shareSession: it asks whether @2 is already shared, then offers the dialog
  expect(fetches.mock.calls.map(([u]) => String(u))).toContain('/api/term/%402/share-info');
});

test('the arrows move the selection (wrapping), the pointer moves it too, Enter runs the selected row', () => {
  open();
  typeQ('wall');
  const sel = () => items().findIndex((r) => r.classList.contains('sel'));
  expect(sel()).toBe(0);
  key({ key: 'ArrowUp' });
  expect(sel()).toBe(items().length - 1);
  key({ key: 'ArrowDown' });
  expect(sel()).toBe(0);
  fireEvent.mouseEnter(item(items().length - 1));
  expect(sel()).toBe(items().length - 1);
  key({ key: 'ArrowDown' });
  const ev = key({ key: 'Enter' });
  expect(ev.defaultPrevented).toBe(true);
  expect(host.selectView.mock.calls).toEqual([['terminals']]);
  expect(palette.get().open).toBe(false);
});

test('Ctrl+K toggles the palette (⌘K too), and Escape closes it', () => {
  const ev = key({ key: 'k', ctrlKey: true }, document);
  expect([palette.get().open, ev.defaultPrevented]).toEqual([true, true]);
  key({ key: 'K', ctrlKey: true }, document);
  expect(palette.get().open).toBe(false);
  key({ key: 'k', metaKey: true }, document);
  expect(palette.get().open).toBe(true);
  key({ key: 'Escape' });
  expect(palette.get().open).toBe(false);
});

test('installed once per page: a second install adds no second listener (Ctrl+K would toggle twice)', () => {
  const again = installPalette(window);
  key({ key: 'k', ctrlKey: true }, document);
  expect(palette.get().open).toBe(true);
  again();
});

test('Ctrl+, toggles Settings (⌘+, too); Shift, Alt or a bare "," do nothing', () => {
  const ev = key({ key: ',', ctrlKey: true }, document);
  expect([settings.get().open, ev.defaultPrevented]).toEqual([true, true]);
  key({ key: ',', ctrlKey: true }, document);
  expect(settings.get().open).toBe(false);
  key({ key: ',', metaKey: true }, document);
  expect(settings.get().open).toBe(true);
  act(() => closeSettings());
  for (const init of [{ ctrlKey: true, shiftKey: true }, { ctrlKey: true, altKey: true }, {}]) {
    const e = key({ key: ',', ...init }, document);
    expect([settings.get().open, e.defaultPrevented]).toEqual([false, false]);
  }
});

test('Escape with the palette over Settings closes only the palette: one Esc, one layer', () => {
  act(() => openSettings());
  open();
  key({ key: 'Escape' });
  expect([palette.get().open, settings.get().open]).toEqual([false, true]);
  key({ key: 'Escape' });
  expect(settings.get().open).toBe(false);
});

test('CMX-401: "sett" finds Settings, and running it opens Settings (closing the palette)', () => {
  open();
  typeQ('sett');
  fireEvent.click(row('Settings'));
  expect([settings.get().open, palette.get().open]).toEqual([true, false]);
});

test('running Settings while Settings is open leaves it open (open-only, not a toggle)', () => {
  act(() => openSettings());
  open();
  typeQ('settings');
  fireEvent.click(row('Settings'));
  expect(settings.get().open).toBe(true);
});

test('the sidebar "Jump to session" box drives the palette in place: it opens, filters by the box, and focus stays in the box', () => {
  const box = document.createElement('input');
  document.body.append(box);
  box.focus();
  box.value = 'settings';
  act(() => sidebarJumpInput(box));
  expect(palette.get()).toMatchObject({ open: true, query: 'settings', fromSidebar: true });
  expect(document.activeElement).toBe(box);
  expect(titles()).toEqual(['Settings']);
  box.value = 'settings a';
  act(() => sidebarJumpInput(box)); // a second keystroke keeps it open, re-filters
  expect(palette.get().query).toBe('settings a');
  expect(titles()[0]).toBe('Settings');
  key({ key: 'Enter' }, box); // the keys work off the palette being open, wherever focus is
  expect(settings.get().open).toBe(true);
  box.remove();
});

test('the jump box\'s handler is on window.chela, for index.html\'s inline oninput/onfocus', () => {
  expect((window as unknown as { chela: { sidebarJumpInput: unknown } }).chela.sidebarJumpInput).toBe(sidebarJumpInput);
});

// --- the shortcuts sheet (CMX-121) --------------------------------------------------------

test('"keyboard" finds the Keyboard shortcuts row; running it opens the sheet and closes the palette', () => {
  open();
  typeQ('keyboard');
  expect(shortcuts.get()).toBe(false);
  fireEvent.click(row('Keyboard shortcuts'));
  expect([shortcuts.get(), palette.get().open]).toEqual([true, false]);
  expect(screen.getByRole('dialog', { name: 'Keyboard shortcuts' })).toBe(document.getElementById('shortcuts-overlay'));
});

test('the sheet lists Ctrl/⌘+, for Settings and Ctrl/⌘+K for the palette, as keys', () => {
  act(() => { shortcuts.set(true); });
  const keysOf = (label: string) => {
    const r = [...document.querySelectorAll('.sc-row')].filter((x) => x.querySelector('.sc-lbl')?.textContent === label);
    expect(r.length).toBe(1);
    return [...(r[0]?.querySelectorAll('.sc-keys kbd') ?? [])].map((k) => k.textContent);
  };
  expect(keysOf('Open / close Settings')).toEqual(['Ctrl', '⌘', ',']);
  expect(keysOf('Command palette / switcher')).toEqual(['Ctrl', '⌘', 'K']);
});

test('Escape closes the sheet, and so does its close button', () => {
  act(() => { shortcuts.set(true); });
  key({ key: 'Escape' });
  expect(shortcuts.get()).toBe(false);
  act(() => { shortcuts.set(true); });
  fireEvent.click(screen.getByRole('button', { name: 'Close' }));
  expect(shortcuts.get()).toBe(false);
});

test('no global key opens the sheet (Alt+/ was dropped): palette-only', () => {
  key({ key: '/', altKey: true }, document);
  key({ key: '?', shiftKey: true }, document);
  expect(shortcuts.get()).toBe(false);
});

// --- the model ----------------------------------------------------------------------------

test('fuzzyScore: a subsequence matches, a word start scores higher, a miss is -1', () => {
  expect(fuzzyScore('', 'x')).toBe(0);
  expect(fuzzyScore('nw', 'New shell window')).toBeGreaterThan(fuzzyScore('nw', 'unwind'));
  expect(fuzzyScore('xyz', 'New shell')).toBe(-1);
});

test('rankItems: an exact window row leads once, the rest best first; a row\'s window id is scored on its own', () => {
  const it = (title: string, sub: string, wid?: string): PaletteItem => ({ title, sub, wid, run: () => {} });
  const long = it('a-very-long-session-name-that-goes-on-and-on', 'session · idle', '@32');
  const exact = it('alpha', 'window · @3', '@3');
  const ranked = rankItems('@3', [it('alpha', 'session · idle · @3', '@3'), long, it('Work', 'view')], exact);
  expect(ranked.map((r) => r.wid)).toEqual(['@3', '@32']);
});

test('with no host yet the palette has no rows (it never throws)', async () => {
  vi.resetModules();
  const fresh = await import('../index.ts');
  expect(fresh.paletteRows('')).toEqual({ items: [], panes: 0 });
  expect(paletteRows('').items.length).toBeGreaterThan(0);
});
