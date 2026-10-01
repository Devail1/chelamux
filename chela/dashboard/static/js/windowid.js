// ---------------------------------------------------------------------------
// WINDOW ID — the tmux window id (`@N`) as the dashboard shows and searches it
// (CMX-417). The orchestrator, `chela peek @N`, inbox notices and peer messages
// all address an agent by `@N`; the pane footer, the sidebar row and the ⌘K
// palette show the SAME id the dashboard already holds for each window (the
// one `chela status` prints) so the operator can tell which pane is `@32`.
//
// ⚠️ An address, not an identity: tmux renumbers windows after a restart, so
// nothing here persists it — it is read off the live window list every render.
//
// Pure: no DOM, no imports, so tests/window_id.test.mjs runs it under plain
// `node --test`.
// ---------------------------------------------------------------------------

const _WID = /^@\d+$/;

// A palette query that IS a window id ("@32", surrounding spaces allowed) →
// that id; anything else (a name, "@", "@3x", "32") → null.
export function windowIdQuery(q) {
    const s = String(q == null ? '' : q).trim();
    return _WID.test(s) ? s : null;
}

// The open window `q` names exactly, out of the dashboard's known window ids —
// null when `q` is not an `@N` query or no such window is open. Exact, never
// a prefix: `@3` must reach @3, not @32.
export function resolveWindowId(q, wids) {
    const wid = windowIdQuery(q);
    if (!wid) return null;
    return (wids || []).includes(wid) ? wid : null;
}
