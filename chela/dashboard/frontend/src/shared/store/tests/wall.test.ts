// CMX-242: the Wall's shared state (shared/store/wall.ts): what the toolbar, the dock and the
// pane header write and the legacy grid reads, each persisted per browser.
import { beforeEach, expect, test, vi } from 'vitest';
import {
  DEFAULT_PRESET, minimizedPanes, orderedWids, paneOrderKey, pinnedPanes, setMinimized, setPinned,
  setWallAuto, setWallFocus, setWallLocked, setWallPreset, wallLayout,
} from '../wall.ts';

beforeEach(() => {
  localStorage.clear();
  wallLayout.set({ preset: DEFAULT_PRESET, locked: false, auto: false, focus: false });
  minimizedPanes.set(new Set());
  pinnedPanes.set(new Set());
});

test('the default preset is two columns', () => {
  expect(DEFAULT_PRESET).toEqual({ cols: 2, rows: 1 });
});

test('a preset is stored, and is a new object on every choice (the grid re-tidies on it)', () => {
  const before = wallLayout.get();
  setWallPreset({ cols: 3, rows: 2 });
  expect(wallLayout.get().preset).toEqual({ cols: 3, rows: 2 });
  expect(localStorage.getItem('pc_wall_preset')).toBe('{"cols":3,"rows":2}');
  const p = wallLayout.get().preset;
  setWallPreset({ cols: 3, rows: 2 });
  expect(wallLayout.get().preset).not.toBe(p);
  expect(wallLayout.get().locked).toBe(before.locked);
});

test('lock is stored as 1/0', () => {
  setWallLocked(true);
  expect(wallLayout.get().locked).toBe(true);
  expect(localStorage.getItem('pc_wall_locked')).toBe('1');
  setWallLocked(false);
  expect(localStorage.getItem('pc_wall_locked')).toBe('0');
});

test('auto-arrange and Focus are exclusive: turning one ON turns the other off, stored too', () => {
  setWallFocus(true);
  setWallAuto(true);
  expect(wallLayout.get()).toMatchObject({ auto: true, focus: false });
  expect([localStorage.getItem('pc_wall_auto'), localStorage.getItem('pc_wall_focus')]).toEqual(['1', '0']);
  setWallFocus(true);
  expect(wallLayout.get()).toMatchObject({ auto: false, focus: true });
  expect([localStorage.getItem('pc_wall_auto'), localStorage.getItem('pc_wall_focus')]).toEqual(['0', '1']);
});

test('turning one OFF leaves the other as it is', () => {
  setWallAuto(true);
  setWallFocus(false);
  expect(wallLayout.get()).toMatchObject({ auto: true, focus: false });
  setWallAuto(false);
  setWallFocus(true);
  setWallAuto(false);
  expect(wallLayout.get()).toMatchObject({ auto: false, focus: true });
});

test('minimized panes are a stored set; an unchanged one publishes nothing', () => {
  setMinimized('@1', true);
  setMinimized('@2', true);
  expect([...minimizedPanes.get()]).toEqual(['@1', '@2']);
  expect(JSON.parse(localStorage.getItem('pc_wall_minimized') ?? '[]')).toEqual(['@1', '@2']);
  const same = minimizedPanes.get();
  setMinimized('@1', true);
  expect(minimizedPanes.get()).toBe(same);
  setMinimized('@1', false);
  expect([...minimizedPanes.get()]).toEqual(['@2']);
});

test('pins are a stored set too', () => {
  setPinned('@4', true);
  expect(pinnedPanes.get().has('@4')).toBe(true);
  expect(JSON.parse(localStorage.getItem('pc_wall_pinned') ?? '[]')).toEqual(['@4']);
  setPinned('@4', false);
  expect(pinnedPanes.get().has('@4')).toBe(false);
});

test('pc_pane_order puts the panes it lists first, in its order; the rest keep theirs', () => {
  expect(orderedWids(['@3', '@1', '@2'])).toEqual(['@3', '@1', '@2']);
  localStorage.setItem('pc_pane_order', JSON.stringify(['@2', '@3']));
  expect(paneOrderKey()('@3')).toBe(1);
  expect(paneOrderKey()('@1')).toBe(1e9);
  expect(orderedWids(['@3', '@1', '@4', '@2'])).toEqual(['@2', '@3', '@1', '@4']);
  localStorage.setItem('pc_pane_order', 'not json');
  expect(paneOrderKey()('@2')).toBe(1e9);
  localStorage.setItem('pc_pane_order', '{"@2": 0}');
  expect(paneOrderKey()('@2')).toBe(1e9);
});

test('a blocked storage still holds the state for this page', () => {
  const set = Storage.prototype.setItem;
  Storage.prototype.setItem = () => { throw new Error('quota'); };
  try {
    setMinimized('@9', true);
    setWallPreset({ cols: 1, rows: 1 });
    expect(minimizedPanes.get().has('@9')).toBe(true);
    expect(wallLayout.get().preset).toEqual({ cols: 1, rows: 1 });
  } finally {
    Storage.prototype.setItem = set;
  }
});

test('a reload reads the stored state back; an unreadable one starts from the defaults', async () => {
  localStorage.setItem('pc_wall_preset', '{"cols":3,"rows":2}');
  localStorage.setItem('pc_wall_locked', '1');
  localStorage.setItem('pc_wall_focus', '1');
  localStorage.setItem('pc_wall_minimized', '["@1", 7]');
  vi.resetModules();
  let w = await import('../wall.ts');
  expect(w.wallLayout.get()).toEqual({ preset: { cols: 3, rows: 2 }, locked: true, auto: false, focus: true });
  expect([...w.minimizedPanes.get()]).toEqual(['@1']);   // a non-id entry is dropped
  for (const [k, v] of [['pc_wall_preset', '{"cols":"3"}'], ['pc_wall_minimized', 'nope'], ['pc_wall_pinned', '{"a":1}']]) {
    localStorage.setItem(k as string, v as string);
  }
  vi.resetModules();
  w = await import('../wall.ts');
  expect(w.wallLayout.get().preset).toEqual({ cols: 2, rows: 1 });
  expect(w.minimizedPanes.get().size).toBe(0);
  expect(w.pinnedPanes.get().size).toBe(0);
  for (const bad of ['not json', '{"cols":0,"rows":1}', '{"cols":2,"rows":-1}']) {
    localStorage.setItem('pc_wall_preset', bad);
    vi.resetModules();
    w = await import('../wall.ts');
    expect(w.wallLayout.get().preset, bad).toEqual({ cols: 2, rows: 1 });
  }
  localStorage.setItem('pc_wall_preset', '{"cols":1,"rows":1}');   // the smallest real one
  vi.resetModules();
  w = await import('../wall.ts');
  expect(w.wallLayout.get().preset).toEqual({ cols: 1, rows: 1 });
}, 20_000);   // re-imports the module graph: slow on a loaded machine
