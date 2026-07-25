// ---------------------------------------------------------------------------
// KANBAN LANE MODEL — pure status→lane mapping for the Work board's 5
// Jira-style lanes. No DOM, no fetch, no imports: laneOf() is a straight
// function of a status string, so it is directly unit-testable
// (tests/kanban_lane_model.test.mjs) without a jsdom fixture — same split
// wallmodel.js/taskmodalmodel.js draw for their own surfaces, taken one step
// further here because this file needs no DOM-touching import at all.
//
// The board used to be 7 columns, one per status (kanban.js's old KANBAN_COLS).
// It is now 5 lanes; several lanes hold more than one status, so a card's own
// status pill (kanban.js's STATUS_CHIPS / _kCard) is what tells two cards
// sharing a lane apart — claimed vs running in In Progress, awaiting vs
// changes-requested vs needs-human vs failed in Review.
//
// ⛔ FAILED rides in Review, not its own lane (Liav's call, 2026-07-25): a
// failed run needs the orchestrator to act on it, exactly like an
// awaiting_review PR does — it is not "done", and hiding it in a low-traffic
// corner would bury the one state that most needs eyes. Its card still gets
// an unmistakable red "failed" pill (glyph + word — colour is a secondary
// cue, never the only one; Liav is red-weak).
// ---------------------------------------------------------------------------

// The 5 lanes, left to right.
export const KANBAN_LANES = ['backlog', 'todo', 'in_progress', 'review', 'done'];

export const KANBAN_LANE_LABELS = {
    backlog: 'Backlog',
    todo: 'To Do',
    in_progress: 'In Progress',
    review: 'Review',
    done: 'Done',
};

// Every status the board currently renders, mapped to the lane that holds it.
// changes_requested / needs_human are the rework loop's two other states
// (CMX-68) — they ride in Review alongside awaiting_review and failed, never
// in a lane of their own.
const STATUS_LANE = {
    backlog: 'backlog',
    open: 'todo',
    claimed: 'in_progress',
    running: 'in_progress',
    awaiting_review: 'review',
    changes_requested: 'review',
    needs_human: 'review',
    failed: 'review',
    done: 'done',
};

// Status → lane key. An UNKNOWN status (one STATUS_LANE has never seen — a
// future dispatcher status, a bug, a typo) falls back to 'review', not
// 'backlog' or 'done': Review is the lane the orchestrator already checks for
// "needs action", so an anomaly stays VISIBLE there instead of silently
// vanishing into a lane nobody re-opens. ⛔ Never change this fallback to
// drop/hide unknown statuses — see tests/kanban_lane_model.test.mjs's
// fallback guard.
export function laneOf(status) {
    return STATUS_LANE[status] || 'review';
}
