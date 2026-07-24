// ---------------------------------------------------------------------------
// WALL TILE MODEL — pure status-card math for the Wall redesign's slice 1 (the
// status-dense tile, docs/wall-redesign.md). No DOM, no fetch, no localStorage:
// every export here answers "given this /api/agents record (+ /api/agents/
// context's `cost_usd`), what does the tile say" as a straight function of its
// input, so it is directly unit-testable (tests/wall_tile.test.mjs) without a
// DOM fixture — same split decisionsmodel.js draws for the decisions badge.
// terminals.js (paneHead / _wallTileHTML / _ctxBarHTML / the action-bar
// renderer) is the only caller: it owns the DOM + the 4s poll, this file only
// ever answers the presentational question.
//
// wantsHuman(a) (util.js) is THE canonical "needs a human" predicate and is
// reused here rather than reinvented — but not via `import`: util.js reads
// `window` at module scope (e.g. `TERMINALS_ENABLED`), so it cannot load
// under a plain Node `--test` run (no DOM), which is exactly what keeps this
// model file unit-testable without a jsdom fixture. Every function below that
// needs it takes the caller's already-computed `wants = wantsHuman(agent)` as
// a parameter instead — terminals.js (the only caller) computes it once from
// the one real implementation and threads it through, so the predicate itself
// is never duplicated.
// ---------------------------------------------------------------------------

// ---- state pill -------------------------------------------------------
//
// A genuine per-agent progress/phase signal does not exist (docs/wall-
// redesign.md: "v1 uses used_pct+session_status+recap proxies... deferred").
// The tile spec's "finished/done signal" is one of those proxies: a window
// that is not busy, not blocked on a human, and carries an open PR reads as
// "finished — PR up, awaiting review" (the same PR the meta row's chip shows,
// see prChip below). No new backend field.
export function isFinished(agent, wants) {
    if (!agent) return false;
    if (wants) return false;
    if (agent.session_status === 'busy') return false;
    return !!(agent.pr && agent.pr.url);
}

// glyph + word ALWAYS carry the state — colour is decoration on top (Liav is
// red-weak). `cls` maps to the Okabe-Ito tokens in style.css (.gs-state-*).
//
// Precedence is load-bearing, in this exact order:
//   1. wants (wantsHuman)  — OUTRANKS session_status. A dispatched worker
//      sitting at a Bash/Edit permission gate still reports session_status
//      "busy" to `claude agents --json` (app.py::_needs_human's docstring) —
//      checking busy first would misread a gated pane as "working".
//   2. session_status === 'busy' → working.
//   3. isFinished → done.
//   4. else → idle.
export function tileState(agent, wants) {
    if (wants) return { glyph: '◆', word: 'needs you', cls: 'needs-you' };
    if (agent && agent.session_status === 'busy') return { glyph: '●', word: 'working', cls: 'working' };
    if (isFinished(agent, wants)) return { glyph: '✓', word: 'done', cls: 'done' };
    return { glyph: '○', word: 'idle', cls: 'idle' };
}

// ---- action-bar verb ----------------------------------------------------
//
// Which verb a wantsHuman pane's action bar shows, with NO new backend field:
// app.py::_needs_human ORs two sources — `session_status === "waiting"`
// (Claude's own view: it is waiting on a reply, i.e. a QUESTION) or, for a
// dispatched window only, a tmux-pane permission-gate probe (Claude's own
// view stays "busy" for that case — the gate lives only as pixels, per the
// same docstring). So the two fields the client already has tell us which
// case fired: `waiting` → Answer a question; `needs_human` true while NOT
// `waiting` → Approve the pane-probed permission gate.
export function actionVerb(agent, wants) {
    if (!wants) return null;
    return agent.session_status === 'waiting' ? 'Answer' : 'Approve';
}

// The action-bar's full shape: { kind, label } for a wantsHuman or finished
// pane, or null when the pane needs no action bar at all.
export function actionBarKind(agent, wants) {
    const verb = actionVerb(agent, wants);
    if (verb) return { kind: verb === 'Answer' ? 'answer' : 'approve', label: verb };
    if (isFinished(agent, wants)) return { kind: 'review', label: 'Review' };
    return null;
}

// ---- context bar level -----------------------------------------------------
//
// Matches the design doc's thresholds EXACTLY (docs/wall-redesign.md: "warn >
// 60 -> --ok-orange, danger > 80 -> --ok-vermillion") and the pre-existing
// _applyTermContext sev computation this replaces — `>`, not `>=`: the
// boundary values themselves (60, 80) read as 'ok'/'warn', not the next tier.
export function ctxLevel(usedPct) {
    if (usedPct == null) return 'ok';
    if (usedPct > 80) return 'bad';
    if (usedPct > 60) return 'warn';
    return 'ok';
}

// ---- null-field -> hidden-sentinel helpers ---------------------------------
//
// "Degrade quietly: a missing field (recap, pr, cost_usd null) hides that
// element, never blanks the tile" (docs/wall-redesign.md). Each helper
// returns exactly `null` (never "null"/"undefined" text, never a
// falsy-but-rendered value) when the field is absent, so the caller's
// `if (x) ...` hides the element cleanly.

// recap_ts rides along verbatim (an ISO string or null) — formatting/age math
// is the caller's job (ageStr/shortTime already live in util.js); this only
// decides presence.
export function recapView(agent) {
    if (!agent || !agent.recap) return null;
    return { text: agent.recap, tsTitle: agent.recap_ts || '' };
}

// The PR payload chela's transcript layer emits is `{url, number, repository,
// ts}` (chela/transcripts.py PRLink.to_dict) — there is no "state"/"status"
// field to show, so the chip is `#<number>` (or bare "PR" if number is
// absent), never a fabricated state word. `repository` rides along for the
// caller's tooltip.
export function prChip(pr) {
    if (!pr || !pr.url) return null;
    const label = pr.number != null ? ('#' + pr.number) : 'PR';
    return { label, url: pr.url, repository: pr.repository || '' };
}

// cost_usd of exactly 0 is a REAL value, not "missing" — only `== null` hides
// it. A naive `if (!costUsd)` would wrongly hide a genuine $0.00, and this is
// the guard that catches that mistake. Matches cost.js's `_fmtCost`.
export function costView(costUsd) {
    if (costUsd == null) return null;
    return '$' + Number(costUsd).toFixed(2);
}
