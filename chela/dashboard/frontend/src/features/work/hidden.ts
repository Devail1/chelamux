// ---------------------------------------------------------------------------
// CMX-136: the Work board's empty columns. Pure (no DOM, no imports;
// static/js/kanbanhiddenmodel.js until CMX-233).
//
// On a phone an EMPTY column is not drawn (no swiped page, no Rows group) unless the viewer
// pinned it to "always show" (per viewer, localStorage; every storage failure falls back to
// the default: never a crash, never a blank board). CMX-316 retired the desktop's "Hidden
// columns" panel, which wrote the pins: the desktop board draws every column but the ones
// hidden in ⋯ › Column options (desktop.ts).
// ---------------------------------------------------------------------------

export const COL_PREFS_KEY = 'chela_kanban_colprefs';

export type ColPrefs = Record<string, 'show'>;
export type KeyStorage = Pick<Storage, 'getItem' | 'setItem'>;

/** `{colKey: 'show'}`: a column the viewer pinned open. Anything else (missing key,
 * unreadable storage, junk JSON, a non-object) is the default: hide when empty. */
export function loadColPrefs(storage: KeyStorage): ColPrefs {
  try {
    const parsed: unknown = JSON.parse(storage.getItem(COL_PREFS_KEY) || '{}');
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    const out: ColPrefs = {};
    for (const [k, v] of Object.entries(parsed)) if (v === 'show') out[k] = 'show';
    return out;
  } catch {
    return {};
  }
}

/** `cols` (board order) → `{visible, hidden}`, both in that order. A column is hidden only
 * when it holds NO card and the viewer has not pinned it. */
export function splitColumns<C extends { key: string; cards: unknown[] }>(cols: C[], prefs: ColPrefs): { visible: C[]; hidden: C[] } {
  const visible: C[] = [];
  const hidden: C[] = [];
  for (const c of cols) {
    if (c.cards.length === 0 && prefs[c.key] !== 'show') hidden.push(c);
    else visible.push(c);
  }
  return { visible, hidden };
}
