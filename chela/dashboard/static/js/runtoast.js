// Pure, DOM-free edge-trigger decision for dispatcher run-state toasts.
//
// The SSE `runs` frame fires on ANY run change (including a pr_state-only
// update while the status sits unchanged in awaiting_review). To avoid spamming
// a toast on every such poll, the caller keeps the last-seen status per run and
// asks runToastKind(prev, next): a toast fires ONLY when the status actually
// changes INTO a review state. Kept side-effect-free so it unit-tests cleanly
// (node --test tests/runtoast.test.mjs).

// Terminal review states that warrant a toast, in payload-status → display form.
// awaiting_review is the primary signal ("needs review"); done/failed ride along
// as secondary, informational transitions (mutable via the Notifications mute).
export const RUN_TOAST_KINDS = {
    awaiting_review: { icon: '🔍', text: 'awaiting review' },
    done: { icon: '✅', text: 'done' },
    failed: { icon: '⚠️', text: 'failed' },
};

// Returns the toast kind (a key of RUN_TOAST_KINDS) for a status transition, or
// null when no toast should fire. Edge-triggered: an unchanged status never
// toasts, and only transitions into a tracked review state qualify.
export function runToastKind(prevStatus, nextStatus) {
    if (prevStatus === nextStatus) return null;
    return Object.prototype.hasOwnProperty.call(RUN_TOAST_KINDS, nextStatus)
        ? nextStatus
        : null;
}
