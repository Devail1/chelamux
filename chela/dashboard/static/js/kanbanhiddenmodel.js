// ---------------------------------------------------------------------------
// 🙈 CMX-136 — the Work board's "Hidden columns" panel, like Linear's. Pure (no DOM, no
// imports): tests/kanban_hidden_columns.test.mjs drives it directly, and kanban.js renders
// from it.
//
// An EMPTY column is not drawn as a column by default: it is listed in a narrow panel to
// the right of the board instead, so the columns that hold cards share the freed width.
// A viewer can pin any column to "always show"; that choice is per viewer (localStorage)
// and every storage failure falls back to the default — never a crash, never a blank board.
// ---------------------------------------------------------------------------

export const COL_PREFS_KEY = 'chela_kanban_colprefs';

// `{colKey: 'show'}` — a column the viewer pinned open. Anything else (missing key,
// unreadable storage, junk JSON, a non-object) is the default: hide when empty.
export function loadColPrefs(storage) {
    try {
        const parsed = JSON.parse(storage.getItem(COL_PREFS_KEY) || '{}');
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
        const out = {};
        for (const [k, v] of Object.entries(parsed)) if (v === 'show') out[k] = 'show';
        return out;
    } catch (e) {
        return {};
    }
}

// Persist; false when storage refused (private window, quota, blocked site data).
export function saveColPrefs(storage, prefs) {
    try {
        storage.setItem(COL_PREFS_KEY, JSON.stringify(prefs));
        return true;
    } catch (e) {
        return false;
    }
}

// `cols` (`[{key, label, cards}]`, board order) → `{visible, hidden}`, both in that order.
// A column is hidden only when it holds NO card and the viewer has not pinned it.
export function splitColumns(cols, prefs) {
    const visible = [];
    const hidden = [];
    for (const c of cols) {
        if (c.cards.length === 0 && prefs[c.key] !== 'show') hidden.push(c);
        else visible.push(c);
    }
    return { visible, hidden };
}

// The phone-width one-liner: "Hidden: Backlog · Review · Archived".
export function hiddenSummary(hidden) {
    return hidden.length ? 'Hidden: ' + hidden.map(c => c.label).join(' · ') : '';
}
