// ---------------------------------------------------------------------------
// TASK-DETAIL MODAL MODEL — pure helpers for the brief pane + review timeline.
// No DOM: taskmodal.js owns the render (the Jira-like "issue" view a kanban
// card click opens); this file only turns raw task/run fields into what the
// modal displays, so both are directly unit-testable
// (tests/taskmodal_model.test.mjs), same split resourcesmodel.js/runstate.js
// draw for their own surfaces.
// ---------------------------------------------------------------------------
import { knMd } from './knowledge.js';

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
    return knMd(text, 'brief.md');
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
        }));
}
