// The Wall's display mode, as terminals.js renders it: 'single' (one pane) or 'wall' (the
// grid); null until its first render. The keybar island reads it (CMX-237).
import { createStore } from '../bridge/index.ts';

export const termMode = createStore<string | null>(null);

function loadWidSet(key: string): ReadonlySet<string> {
  try {
    const v: unknown = JSON.parse(localStorage.getItem(key) || '[]');
    return new Set(Array.isArray(v) ? v.filter((w): w is string => typeof w === 'string') : []);
  } catch {
    return new Set();
  }
}

function save(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private mode: the state still holds for this page */
  }
}

function toggleIn(set: ReadonlySet<string>, wid: string, on: boolean): ReadonlySet<string> {
  const next = new Set(set);
  if (on) next.add(wid);
  else next.delete(wid);
  return next;
}

// The panes pinned in place (CMX-108): a grid preset reflows every other pane around them.
// Persisted per browser under pc_wall_pinned (CMX-241: from terminals.js). The pane header's
// Pin row writes it; the legacy grid code reads it.
const PINNED_KEY = 'pc_wall_pinned';

export const pinnedPanes = createStore<ReadonlySet<string>>(loadWidSet(PINNED_KEY));

export function setPinned(wid: string, on: boolean): void {
  if (pinnedPanes.get().has(wid) === on) return;
  const next = toggleIn(pinnedPanes.get(), wid, on);
  save(PINNED_KEY, JSON.stringify([...next]));
  pinnedPanes.set(next);
}

// The panes minimized to the dock (CMX-242: from terminals.js). A minimized tile leaves the
// grid but stays in the page, hidden, so its terminal never reloads; the dock draws a chip for
// it. Persisted under pc_wall_minimized so the dock survives a reload.
const MINIMIZED_KEY = 'pc_wall_minimized';

export const minimizedPanes = createStore<ReadonlySet<string>>(loadWidSet(MINIMIZED_KEY));

export function setMinimized(wid: string, on: boolean): void {
  if (minimizedPanes.get().has(wid) === on) return;
  const next = toggleIn(minimizedPanes.get(), wid, on);
  save(MINIMIZED_KEY, JSON.stringify([...next]));
  minimizedPanes.set(next);
}

// pc_pane_order: a manual order of window ids. Panes it lists come first, in its order; the
// rest keep their discovery order after them. The grid presets and the dock's tiebreak follow it.
/** A window's place in pc_pane_order (1e9 when unlisted), read once for a whole sort. */
export function paneOrderKey(): (wid: string) => number {
  let list: unknown;
  try {
    list = JSON.parse(localStorage.getItem('pc_pane_order') || '[]');
  } catch {
    list = [];
  }
  const order = Array.isArray(list) ? list : [];
  return (wid) => {
    const i = order.indexOf(wid);
    return i === -1 ? 1e9 : i;
  };
}

export function orderedWids(wids: readonly string[]): string[] {
  const key = paneOrderKey();
  return wids.slice().sort((a, b) => key(a) - key(b) || wids.indexOf(a) - wids.indexOf(b));
}

// The Wall's layout, as the layout toolbar sets it (CMX-242, from terminals.js): the fill
// preset, lock (a drag swaps two panes, resize off), and the two modes that compute the layout
// themselves, auto-arrange and Focus. The legacy grid code reads it and applies each change.
export type WallPreset = { cols: number; rows: number };
export type WallLayout = { preset: WallPreset; locked: boolean; auto: boolean; focus: boolean };

/** The default fill: two columns, the standard terminal layout. */
export const DEFAULT_PRESET: WallPreset = { cols: 2, rows: 1 };

function loadPreset(): WallPreset {
  try {
    const p = JSON.parse(localStorage.getItem('pc_wall_preset') || 'null') as Partial<WallPreset> | null;
    // at least one column and one row: a 0 or negative stored value would divide the wall by it
    if (p && Number.isInteger(p.cols) && Number.isInteger(p.rows) && (p.cols as number) >= 1 && (p.rows as number) >= 1) {
      return { cols: p.cols as number, rows: p.rows as number };
    }
  } catch {
    /* unreadable: the default */
  }
  return DEFAULT_PRESET;
}

function loadFlag(key: string): boolean {
  try {
    return localStorage.getItem(key) === '1';
  } catch {
    return false;
  }
}

export const wallLayout = createStore<WallLayout>({
  preset: loadPreset(),
  locked: loadFlag('pc_wall_locked'),
  auto: loadFlag('pc_wall_auto'),
  focus: loadFlag('pc_wall_focus'),
});

export function setWallPreset(preset: WallPreset): void {
  save('pc_wall_preset', JSON.stringify(preset));
  wallLayout.set({ ...wallLayout.get(), preset: { cols: preset.cols, rows: preset.rows } });
}

export function setWallLocked(on: boolean): void {
  save('pc_wall_locked', on ? '1' : '0');
  wallLayout.set({ ...wallLayout.get(), locked: on });
}

// Auto-arrange and Focus both OWN the layout, so turning one on turns the other off.
export function setWallAuto(on: boolean): void {
  const cur = wallLayout.get();
  save('pc_wall_auto', on ? '1' : '0');
  if (on && cur.focus) save('pc_wall_focus', '0');
  wallLayout.set({ ...cur, auto: on, focus: on ? false : cur.focus });
}

export function setWallFocus(on: boolean): void {
  const cur = wallLayout.get();
  save('pc_wall_focus', on ? '1' : '0');
  if (on && cur.auto) save('pc_wall_auto', '0');
  wallLayout.set({ ...cur, focus: on, auto: on ? false : cur.auto });
}
