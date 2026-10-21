// The dispatcher run-state toasts' edge-trigger, pure. Moved from static/js/runtoast.js by
// CMX-219 (CMX-212 P06).
//
// The SSE `runs` frame fires on ANY run change (a pr_state-only update included). The caller
// keeps the last-seen status per run and asks runToastKind(prev, next): a toast fires ONLY
// when the status changes INTO one of these states. awaiting_review is the primary signal;
// done/failed ride along as informational. The rework loop (CMX-68) adds changes_requested
// (the PR was sent back, the agent re-spawns) and needs_human (the loop hit its cap and
// STOPPED, the one a human must not miss). Each carries a word: hue is never the only signal.
import type { IconName } from '../../shared/icons/index.ts';

export const RUN_TOAST_KINDS: Readonly<Record<string, { icon: IconName; text: string }>> = {
  awaiting_review: { icon: 'review', text: 'awaiting review' },
  changes_requested: { icon: 'rework', text: 'changes requested, reworking' },
  needs_human: { icon: 'needsHuman', text: 'NEEDS A HUMAN: rework cap reached' },
  done: { icon: 'done', text: 'done' },
  failed: { icon: 'warn', text: 'failed' },
  // A PR closed WITHOUT merging (CMX-265): never done's icon, that would announce a rejected
  // trial as shipped.
  closed: { icon: 'closedUnmerged', text: 'closed, not merged' },
};

/** The toast kind for a status transition, or null when none fires (edge-triggered). */
export function runToastKind(prevStatus: string | null | undefined, nextStatus: string | null | undefined): string | null {
  if (prevStatus === nextStatus || nextStatus == null) return null;
  return Object.prototype.hasOwnProperty.call(RUN_TOAST_KINDS, nextStatus) ? nextStatus : null;
}

/** The Notifications setting's mute (Settings > Notifications writes it). */
export const MUTE_KEY = 'chela_mute_run_toasts';
