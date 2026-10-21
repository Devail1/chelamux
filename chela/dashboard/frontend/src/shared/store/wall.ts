// The Wall's display mode, as terminals.js renders it: 'single' (one pane) or 'wall' (the
// grid); null until its first render. The keybar island reads it (CMX-237).
import { createStore } from '../bridge/index.ts';

export const termMode = createStore<string | null>(null);

// The panes pinned in place (CMX-108): a grid preset reflows every other pane around them.
// Persisted per browser under pc_wall_pinned (CMX-241: from terminals.js). The pane header's
// Pin row writes it; the legacy grid code reads it.
const PINNED_KEY = 'pc_wall_pinned';

function loadPinned(): ReadonlySet<string> {
  try {
    const v: unknown = JSON.parse(localStorage.getItem(PINNED_KEY) || '[]');
    return new Set(Array.isArray(v) ? v.filter((w): w is string => typeof w === 'string') : []);
  } catch {
    return new Set();
  }
}

export const pinnedPanes = createStore<ReadonlySet<string>>(loadPinned());

export function setPinned(wid: string, on: boolean): void {
  if (pinnedPanes.get().has(wid) === on) return;
  const next = new Set(pinnedPanes.get());
  if (on) next.add(wid);
  else next.delete(wid);
  try {
    localStorage.setItem(PINNED_KEY, JSON.stringify([...next]));
  } catch {
    /* private mode: the pin still holds for this page */
  }
  pinnedPanes.set(next);
}
