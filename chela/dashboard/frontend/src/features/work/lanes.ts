// ---------------------------------------------------------------------------
// KANBAN LANE MODEL: the pure status → lane mapping for the Work board's 6 Jira-style lanes
// (static/js/kanbanlanemodel.js until CMX-233). No DOM, no fetch, no imports.
//
// Several lanes hold more than one status, so a card's own status pill (board.ts's
// STATUS_CHIPS) is what tells two cards sharing a lane apart: claimed vs running in In
// Progress, awaiting vs changes-requested vs needs-human vs failed in Review.
//
// ⛔ FAILED rides in Review, not its own lane (Liav's call, 2026-07-25): a failed run needs
// the orchestrator to act on it, exactly like an awaiting_review PR does. Its card still gets
// an unmistakable "failed" pill (a word; colour is a secondary cue, Liav is red-weak).
//
// ⛔ ARCHIVED is its own lane, separate from Done (Liav's call, CMX-265 round 2, PR #334): a
// `closed` run (a PR a human closed WITHOUT merging) must never share a lane with merged
// work, so "how many of these shipped?" stays answerable by reading Done alone.
// ---------------------------------------------------------------------------

// The 6 lanes, left to right.
export const KANBAN_LANES = ['backlog', 'todo', 'in_progress', 'review', 'done', 'archived'] as const;
export type Lane = (typeof KANBAN_LANES)[number];

export const KANBAN_LANE_LABELS: Record<Lane, string> = {
  backlog: 'Backlog',
  todo: 'To Do',
  in_progress: 'In Progress',
  review: 'Review',
  done: 'Done',
  archived: 'Archived',
};

// Every status the board renders, mapped to the lane that holds it. changes_requested /
// needs_human are the rework loop's two other states (CMX-68): they ride in Review.
const STATUS_LANE: Record<string, Lane> = {
  backlog: 'backlog',
  // A PARKED (`<!-- blocked: ... -->`) TODO.md bullet: real tracked work, not claimable.
  // Rides in Backlog with its own card style rather than vanishing (Liav, 2026-08-12).
  parked: 'backlog',
  open: 'todo',
  claimed: 'in_progress',
  running: 'in_progress',
  awaiting_review: 'review',
  changes_requested: 'review',
  needs_human: 'review',
  failed: 'review',
  done: 'done',
  // A PR closed WITHOUT merging (CMX-265): terminal like `done`, never a member of it.
  closed: 'archived',
};

// Status → lane. An UNKNOWN status (a future dispatcher status, a bug, a typo) falls back to
// 'review', the lane the orchestrator already checks for "needs action", so an anomaly stays
// VISIBLE. ⛔ Never change this fallback to drop or hide unknown statuses.
export function laneOf(status: string | null | undefined): Lane {
  return (status && Object.hasOwn(STATUS_LANE, status) ? STATUS_LANE[status] : undefined) ?? 'review';
}
