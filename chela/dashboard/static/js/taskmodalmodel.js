// ---------------------------------------------------------------------------
// TASK-DETAIL MODAL MODEL — pure helpers for the brief pane + review timeline.
// No DOM: taskmodal.js owns the render (the Jira-like "issue" view a kanban
// card click opens); this file only turns raw task/run fields into what the
// modal displays, so both are directly unit-testable
// (tests/taskmodal_model.test.mjs), same split resourcesmodel.js/runstate.js
// draw for their own surfaces.
// ---------------------------------------------------------------------------
import { knMd } from './knowledge';

// Which field actually has the task's brief, in priority order:
//   1. `item.brief`  — a run's persisted brief (dispatcher._spawn copies it at
//      claim time from the task's `body`, falling back to `raw`/title — see
//      chela.dispatcher._task_brief), so it's already the richest text
//      available once a task has been claimed.
//   2. `item.body`   — an open (never-claimed) task's FULL multi-line brief
//      (chela.sources.markdown._task_body: title + its dedented OBJECTIVE/
//      BOUNDARIES/GUARDS/VERIFY continuation), when the source captured one.
//   3. `item.raw`    — the bare bullet line / issue URL, for a one-line task
//      or a source with no notion of a continuation (gh_issues).
// `null`/`undefined`/`''` are all treated as "not present" at every step, and
// no field existing at all (a backlog item, a legacy pre-migration run row)
// resolves to `null` — never throws on a sparse/partial item.
export function briefSource(item) {
    if (!item) return null;
    for (const v of [item.brief, item.body, item.raw]) {
        if (v != null && v !== '') return v;
    }
    return null;
}

// The brief pane's markdown render. A thin wrapper around knMd — but a
// deliberate one: taskmodal.js's whole left pane depends on knMd's specific
// heading/list/code CONTRACT (it is NOT a full markdown renderer — no numbered
// lists, no nested lists), and this is what pins that contract down with its
// own test instead of only being exercised incidentally through a DOM
// assertion on the modal.
export function briefHtml(text) {
    if (!text) return '';
    return knMd(text);
}

// The modal header's display title. ⛔ Display-only — never touches the
// PARSED `title` field: a task's id is the hash of that title (chela.sources.
// markdown._task_id), so mutating it anywhere would orphan in-flight runs.
// Our briefs' first line is the whole bullet, e.g.
// "**📥 Move the Decisions log … (Liav, 2026-07-24).** Design is SETTLED…" —
// showing that raw is both long and full of literal `**`. When the title
// opens with a bold span, the concise title IS that span's inner text, so
// this returns just that; anything without a leading bold span (a plain
// one-line task title) is returned unchanged. Pure: taskmodal.js is
// responsible for running the result through knInline at render time so any
// remaining markdown/emoji renders instead of showing as literal text.
export function displayTitle(rawTitle) {
    if (!rawTitle) return rawTitle || '';
    const m = String(rawTitle).match(/^\*\*([^*]+)\*\*/);
    return m ? m[1] : rawTitle;
}

// review_history is a JSON TEXT column (chela.dispatcher.reviews_of's JS-side
// counterpart) — a list of {round, at, body, verdict} written oldest-first by
// request_changes()/reopen. Never throws: null, '', or malformed/non-list/
// non-object JSON all resolve to "no history yet" ([]), never a crash that
// would blank the whole modal over one bad row.
export function timelineSteps(reviewHistoryJson) {
    if (!reviewHistoryJson) return [];
    let parsed;
    try {
        parsed = JSON.parse(reviewHistoryJson);
    } catch (e) {
        return [];
    }
    if (!Array.isArray(parsed)) return [];
    return parsed
        .filter(r => r && typeof r === 'object' && !Array.isArray(r))
        .map(r => ({
            round: r.round != null ? r.round : null,
            at: r.at || null,
            state: r.verdict || 'review',
            detail: typeof r.body === 'string' ? r.body : '',
            // CMX-122: a round's survivor counts, for its collapsed one-line summary. Visible
            // = the structured `mutations` the judge stored; held-out ones are never stored,
            // only COUNTED in the body's `### 🙈 N held-out guard(s)` line (judge._held_out_section).
            visible: Array.isArray(r.mutations) ? r.mutations.length : 0,
            heldOut: heldOutCount(r.body),
        }));
}

export function heldOutCount(body) {
    const m = typeof body === 'string' && body.match(/^### 🙈 (\d+) held-out guard/m);
    return m ? Number(m[1]) : 0;
}

// CMX-122: which tab the modal opens on. Review when the run is waiting on a review
// (`awaiting_review` / `needs_human`) or its judge returned a blocking verdict — but only
// when there IS a round to read; an empty Review tab is never the landing page.
const _REVIEW_STATUSES = new Set(['awaiting_review', 'needs_human']);
const _BLOCKING_JUDGE = new Set(['blocked', 'blocked_race']);

export function defaultTab(item, nRounds) {
    if (!item || !nRounds) return 'brief';
    return _REVIEW_STATUSES.has(item.status) || _BLOCKING_JUDGE.has(item.judge_state) ? 'review' : 'brief';
}

// CMX-122: the modal header's tracker link — EXACTLY the URL the Linear adapter fetched
// (`tracker_url`, the same source kanban.js's _kTrackerLink reads, CMX-5/CMX-94), never
// built from a workspace slug. A markdown/TODO or gh_issues item has none → null (plain
// key). Only https is linked: the value lands in an href, so `javascript:` renders nothing.
export function trackerHref(item) {
    const url = item && item.tracker_url;
    return typeof url === 'string' && /^https:\/\//i.test(url) ? url : null;
}

// ⚖️🎚️ CMX-405 — a run's RISK level (chela.sources.RISK_LEVELS), as the word the Work
// card and the task modal show plus a colour modifier. The WORD carries the meaning (Liav is
// red-weak); the class only agrees with it. `null` for anything that is not a run carrying a
// known level (an open/backlog item, a pre-CMX-405 row) — render nothing, never a guess.
// `reason` is the run row's `risk_reason` (marker / label / inferred: … / default), the
// chip's tooltip, so an inferred `high` says why.
const _RISK_CLS = { high: 'risk-high', normal: 'risk-normal', low: 'risk-low' };

export function riskChip(item) {
    const level = item && typeof item.risk === 'string' ? item.risk.toLowerCase() : '';
    if (!_RISK_CLS[level]) return null;
    return { label: `risk: ${level}`, cls: _RISK_CLS[level], reason: item.risk_reason || '' };
}
