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
//
// The rework loop (CMX-68) adds the other two ends of the review cycle, through this
// same edge-trigger rather than a second poller: changes_requested (the reviewer sent
// the PR back — the agent is about to be re-spawned in its own worktree) and
// needs_human (the loop hit CHELA_MAX_REWORKS and STOPPED, which is the one a human
// must not miss). Each carries a word, not just an icon: hue is never the only signal.
export const RUN_TOAST_KINDS = {
    awaiting_review: { icon: '🔍', text: 'awaiting review' },
    changes_requested: { icon: '🔁', text: 'changes requested — reworking' },
    needs_human: { icon: '🛑', text: 'NEEDS A HUMAN — rework cap reached' },
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
