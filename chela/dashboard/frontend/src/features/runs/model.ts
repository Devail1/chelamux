// Run state → how it LOOKS (pure). Moved from static/js/runstate.js by CMX-231 (CMX-212
// P10); kanban.js and taskmodal.js import it from here.
//
// A pure module, because this mapping is exactly the kind that rots silently: CMX-68 added
// two run states to the dispatcher and the badge table was never updated, so `needs_human`
// (the ONE state that means a human must look at this run) rendered in the same grey as an
// unknown status. A pure module has a test, and the test goes red when the next state is
// added without a badge.
import type { OpenTask, Run, TaskProgress, Workflow } from '../../shared/store/work.ts';

// The severity ladder, loudest last:
//   badge-on          running / claimed: an agent is working
//   badge-awaiting    awaiting_review: a PR is waiting on a REVIEWER
//   badge-rework      changes_requested: sent back; an agent is going back in (live work)
//   badge-off         failed: it died; the dispatcher may retry it
//   badge-needs-human needs_human: the rework loop hit its cap and STOPPED. Nothing moves
//                     until a person moves it. Loudest badge.
const BADGE_CLASS: Record<string, string> = {
  claimed: 'badge-on',
  running: 'badge-on',
  awaiting_review: 'badge-awaiting',
  changes_requested: 'badge-rework',
  needs_human: 'badge-needs-human',
  failed: 'badge-off',
  // Grey ON PURPOSE, and with a class of its own: a shipped run is the quietest thing on the
  // board. It must not SHARE the unknown-status grey, or "we styled this deliberately" and
  // "we forgot this one" become the same pixel.
  done: 'badge-done',
  // A PR a human closed WITHOUT merging (CMX-265): terminal like `done`, but NOT shipped.
  // Its own class, so "shipped" and "rejected" never look alike.
  closed: 'badge-closed',
};

// The fallback is for an UNKNOWN status only. ⛔ Never let a real run state fall through to
// it: "we have no idea what this is" and "a human must look at this" must not look the same.
export const UNKNOWN_BADGE = 'badge-priority-low';

export function runStatusBadgeClass(status: string | null | undefined): string {
  return (status && BADGE_CLASS[status]) || UNKNOWN_BADGE;
}

// Every status the dispatcher can put on a run row (chela/dispatcher.py: ACTIVE_STATUSES +
// REVIEW_STATUSES + done/closed/failed). The test asserts each one has a badge.
export const RUN_STATUSES = Object.keys(BADGE_CLASS);

// 🗂️✖️ CMX-406. The one-line note a run's card (and runs-table row) carries under its title.
// A run a human closed with `chela close --reason` reads "Closed: <reason>", NOT its old
// `last_error` (the misleading "tmux window disappeared" line the close exists to replace).
// `closed` tells the renderer to style it as a note, not as an error. null: nothing to show.
export function runCardNote(run: Run | null | undefined): { text: string; closed: boolean } | null {
  const reason = run && run.status === 'closed' ? String(run.close_reason || '').trim() : '';
  if (reason) return { text: `Closed: ${reason}`, closed: true };
  const err = run && run.last_error ? String(run.last_error) : '';
  return err ? { text: err, closed: false } : null;
}

// 📝 CMX-52. The agent's own `chela task-finished --handoff` note, as one display line, or
// null (no handoff, or a blank one). Agent-authored, so it is flattened: control characters
// dropped and every run of whitespace collapsed to one space. The renderer still escapes it.
export function runHandoffNote(run: Run | null | undefined): string | null {
  const raw = run && typeof run.handoff === 'string' ? run.handoff : '';
  // eslint-disable-next-line no-control-regex -- stripping control characters is the point
  const text = raw.replace(/[\u0000-\u001f\u007f-\u009f]/g, ' ').replace(/\s+/g, ' ').trim();
  return text || null;
}

// 🗂️🔁 CMX-68. A `closed` run is never claimed again, so a closed run whose task the tracker
// still calls READY is a SILENT stall. The server sets `closed_run_stall` on both the closed
// run's card and the open task's card; this is the line they show (the renderer adds the
// warning icon), or null.
export function runStallNote(card: Run | null | undefined): string | null {
  const msg = card && card.closed_run_stall ? String(card.closed_run_stall).trim() : '';
  return msg || null;
}

// The requeue action a card offers (`POST /api/dispatcher/runs/<id>/close`, requeue=true), or
// null: "Requeue" on a STALLED closed run or the open task it holds; "Close & requeue" on any
// run a human may walk away from. A merged PR offers nothing: that work shipped.
const REQUEUE_CLOSABLE = new Set(['claimed', 'running', 'awaiting_review', 'changes_requested', 'needs_human', 'failed']);
export function runCloseAction(card: Run | null | undefined): { label: string } | null {
  if (!card || !card.task_id || card.pr_state === 'merged') return null;
  if (card.status === 'closed' || card.status === 'open') {
    // The open task's card carries the same task_id as the closed run that holds it.
    return card.closed_run_stall ? { label: 'Requeue' } : null;
  }
  return REQUEUE_CLOSABLE.has(card.status ?? '') ? { label: 'Close & requeue' } : null;
}

// PROJECT_KEY-N is a run's identity when it has a task_number; a pre-migration row falls back
// to its short hash, so historical PRs stay linkable.
export function runDisplayId(r: Run): string {
  if (r.project_key && r.task_number != null) return `${r.project_key}-${r.task_number}`;
  return r.task_id || '';
}

/** A PR link's compact label: `#NNN` off a github URL, else 'PR'. */
export function prLabel(prUrl: string): string {
  const m = /\/pull\/(\d+)(?:[/?#]|$)/.exec(prUrl);
  return m ? `#${m[1]}` : 'PR';
}

// Progress inside a run's OWN task list (issue #462): `tasks` is null whenever the run could
// not be joined to a live session's task directory, and that renders NOTHING, never "0/0".
export function taskProgress(tasks: TaskProgress | null | undefined): { label: string; title: string } | null {
  if (!tasks || !tasks.total) return null;
  const lines = [`${tasks.done} of ${tasks.total} tasks done`];
  if (tasks.in_progress) lines.push(`In progress: ${tasks.in_progress.subject}`);
  for (const b of tasks.blocked || []) {
    const by = (b.blocked_by || []).join(', ');
    lines.push(by ? `"${b.subject}" blocked by ${by}` : `"${b.subject}" blocked`);
  }
  return { label: `${tasks.done}/${tasks.total}`, title: lines.join('\n') };
}

// CMX-122: a tracker link is EXACTLY the URL the Linear adapter fetched (`tracker_url`), never
// built from a workspace slug, and only https: the value lands in an href.
export function trackerHref(item: { tracker_url?: string | null } | null | undefined): string | null {
  const url = item && item.tracker_url;
  return typeof url === 'string' && /^https:\/\//i.test(url) ? url : null;
}

// ⚖️🎚️ CMX-405: a known risk level as the word the chip shows plus a colour class (the WORD
// carries the meaning; the class only agrees). null for no known level: never a guess.
const RISK_CLS: Record<string, string> = { high: 'risk-high', normal: 'risk-normal', low: 'risk-low' };
export function riskChip(item: { risk?: unknown; risk_reason?: string | null } | null | undefined) {
  const level = item && typeof item.risk === 'string' ? item.risk.toLowerCase() : '';
  const cls = RISK_CLS[level];
  return cls ? { label: `risk: ${level}`, cls, reason: (item && item.risk_reason) || '' } : null;
}

/**
 * A workflow's run buckets as the Runs segment shows them. `needs_human` is the state the
 * operator must act on: it gets its own table, and leaves whichever bucket the server filed
 * it in.
 */
export function runBuckets(wf: Workflow) {
  const isHuman = (r: Run) => r.status === 'needs_human';
  const all = (runs: Run[] | undefined) => (runs || []).filter(Boolean);
  const without = (runs: Run[] | undefined) => all(runs).filter((r) => !isHuman(r));
  return {
    needsHuman: [...all(wf.active_runs), ...all(wf.awaiting_review_runs), ...all(wf.recent_runs)].filter(isHuman),
    active: without(wf.active_runs),
    awaiting: without(wf.awaiting_review_runs),
    recent: without(wf.recent_runs),
  };
}

// Linear's open set is every non-terminal issue, so one flat list read as that many to-dos
// (Liav, 10-12). Split by the server's `group`: Ready, the started states, then Backlog.
export function linearGroups(open: OpenTask[]) {
  const ready = open.filter((t) => t.group === 'ready');
  const started = open.filter((t) => t.group === 'in_progress');
  return { ready, started, backlog: open.filter((t) => t.group !== 'ready' && t.group !== 'in_progress') };
}
