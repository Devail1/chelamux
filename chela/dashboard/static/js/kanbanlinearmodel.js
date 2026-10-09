// ---------------------------------------------------------------------------
// 🗂️📐 CMX-23 — TRACKER COLUMNS for the Work board. Pure (no DOM, no fetch, no imports),
// so tests/kanban_linear_model.test.mjs drives it directly — same split as
// kanbanlanemodel.js, whose 6 chela lanes stay the fallback for every other tracker kind.
//
// With `tracker: kind: linear` the board's columns ARE the team's workflow states, in
// Linear's own order (the API hands them over as `tracker_columns`), so the two boards can
// never disagree again: before this, four cards sat in chela's Review lane marked
// "failed" while Linear showed them in Todo / In Progress, and Linear's In Review was empty
// with their PRs open (Liav, 2026-10-07).
//
// chela's run state (running, judging, rework N, failed, needs a human, …) is a BADGE on
// the card (kanban.js's STATUS_CHIPS + runStateBadges below), never a column: a failed run
// shows a red "failed" pill INSIDE In Progress / In Review. Canceled-type columns
// (Canceled, Duplicate) are `hidden`, as Linear collapses them.
// ---------------------------------------------------------------------------

// Where a card goes when the tracker did not report its state (a finished / archived
// issue has left the open read, a run predates the field): its run status → the state
// TYPE that status means, and which of that type's columns (first or last — In Progress
// is the first `started` state, In Review the last).
const STATUS_FALLBACK = {
    backlog: ['backlog', 'first'],
    parked: ['backlog', 'first'],
    open: ['unstarted', 'first'],
    claimed: ['started', 'first'],
    running: ['started', 'first'],
    failed: ['started', 'first'],
    awaiting_review: ['started', 'last'],
    // A rework round is back in the agent's hands → In Progress, as the dispatcher writes it
    // (`_TRACKER_IN_PROGRESS_STATUSES`, chela/dispatcher.py).
    changes_requested: ['started', 'first'],
    needs_human: ['started', 'last'],
    done: ['completed', 'first'],
    closed: ['canceled', 'first'],
};

// The id of the pseudo-column a card lands in when NOTHING maps it — never dropped.
export const UNMAPPED_KEY = 'tr-unmapped';

// The columns for the workflows on screen, or null when any of them has none (a
// markdown/gh_issues tracker, or a failed state read) — the caller then falls back to
// chela's own lanes. Several Linear workflows: the first one's order, with any state the
// others add appended once.
export function trackerColumns(workflows) {
    if (!Array.isArray(workflows) || !workflows.length) return null;
    const out = [];
    const seen = new Set();
    for (const wf of workflows) {
        const cols = wf && wf.tracker_columns;
        if (!Array.isArray(cols) || !cols.length) return null;
        for (const c of cols) {
            if (!c || typeof c.name !== 'string' || !c.name) continue;
            const k = c.name.toLowerCase();
            if (seen.has(k)) continue;
            seen.add(k);
            out.push({ name: c.name, type: String(c.type || ''), hidden: !!c.hidden });
        }
    }
    return out.length ? out : null;
}

export function columnKey(name) {
    return 'tr-' + String(name).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
}

// The column (one of `columns`) a card belongs in, or null when none fits.
export function columnOf(card, columns) {
    const state = card && typeof card.tracker_state === 'string' ? card.tracker_state.toLowerCase() : '';
    if (state) {
        const hit = columns.find(c => c.name.toLowerCase() === state);
        if (hit) return hit;
    }
    const fb = STATUS_FALLBACK[card && card.status];
    if (!fb) return null;
    let [type, which] = fb;
    // A died run whose PR is open was in review — it stays there.
    if (card.status === 'failed' && card.pr_url) which = 'last';
    const ofType = columns.filter(c => c.type === type);
    if (!ofType.length) return null;
    return which === 'last' ? ofType[ofType.length - 1] : ofType[0];
}

// `cards` grouped into the VISIBLE columns, in column order: `[{key, label, cards}]`.
// A card in a hidden column (Canceled / Duplicate) is counted in `hiddenCount`, not shown;
// a card no column fits is shown in a trailing "Unmapped" column — never dropped.
export function trackerBoard(columns, cards) {
    const byKey = new Map();
    const board = [];
    for (const c of columns) {
        if (c.hidden) continue;
        const col = { key: columnKey(c.name), label: c.name, cards: [] };
        byKey.set(c.name.toLowerCase(), col);
        board.push(col);
    }
    let hiddenCount = 0;
    const unmapped = [];
    for (const card of cards) {
        const col = columnOf(card, columns);
        if (col === null) { unmapped.push(card); continue; }
        if (col.hidden) { hiddenCount += 1; continue; }
        byKey.get(col.name.toLowerCase()).cards.push(card);
    }
    if (unmapped.length) board.push({ key: UNMAPPED_KEY, label: 'Unmapped', cards: unmapped });
    return { columns: board, hiddenCount };
}

// The run-state badges a card carries ON TOP of its status pill — the states that used to
// need a column of their own, or were not visible at all. Each is a word + a glyph
// (colour is never the only signal — Liav is red-weak).
export function runStateBadges(card) {
    const out = [];
    if (!card) return out;
    // ⚖️ CMX-40: the detached battery's own progress when it has one ("⚖️ testing · 3/6 · 15m"),
    // and a battery that died without a verdict says so — never a bare "judging".
    const battery = card.judge_battery;
    if (card.judge_state === 'running' && battery && battery.state === 'died') {
        out.push({ label: battery.label, cls: 'st-judge-died' });
    } else if (card.judge_state === 'running') {
        out.push({ label: battery && battery.state === 'testing' ? battery.label : '⚖️ judging', cls: 'st-judging' });
    }
    if (card.judge_state === 'blocked_race') out.push({ label: '🧊 blocked race', cls: 'st-blocked-race' });
    const rework = Number(card.rework_count) || 0;
    if (rework > 0) out.push({ label: `🔁 rework ${rework}`, cls: 'st-rework' });
    return out;
}
