// --- Run state → how it LOOKS (pure; see runtoast.js for the same idea on toasts) ---
//
// Extracted from dispatcher.js because dispatcher.js cannot be imported outside a browser
// (its import graph touches `window` at module scope), and this mapping is exactly the kind
// that rots silently: CMX-68 added two run states to the dispatcher and the badge table was
// never updated, so `needs_human` — the ONE state that means a human must look at this run —
// rendered in the same grey as an unknown status. A pure module has a node test, and a node
// test goes red when the next state is added without a badge.

// The severity ladder, loudest last:
//   badge-on          running / claimed — an agent is working
//   badge-awaiting    awaiting_review   — a PR is waiting on a REVIEWER
//   badge-rework      changes_requested — sent back; an agent is going back in (live work)
//   badge-off         failed            — it died; the dispatcher may retry it
//   badge-needs-human needs_human       — the rework loop hit its cap and STOPPED. Nothing
//                                         moves until a person moves it. Loudest badge.
const BADGE_CLASS = {
    claimed: 'badge-on',
    running: 'badge-on',
    awaiting_review: 'badge-awaiting',
    changes_requested: 'badge-rework',
    needs_human: 'badge-needs-human',
    failed: 'badge-off',
    // Grey ON PURPOSE, and with a class of its own: a shipped run is the quietest thing on
    // the board. It must not SHARE the unknown-status grey, or "we styled this deliberately"
    // and "we forgot this one" become the same pixel — which is the bug this module exists
    // to prevent.
    done: 'badge-done',
};

// The fallback is for an UNKNOWN status only. ⛔ Never let a real run state fall through to
// it: "we have no idea what this is" and "a human must look at this" must not look the same.
export const UNKNOWN_BADGE = 'badge-priority-low';

export function runStatusBadgeClass(status) {
    return BADGE_CLASS[status] || UNKNOWN_BADGE;
}

// Every status the dispatcher can put on a run row (chela/dispatcher.py: ACTIVE_STATUSES +
// REVIEW_STATUSES + done/failed). The test asserts each one has a badge — that is the guard.
export const RUN_STATUSES = Object.keys(BADGE_CLASS);
