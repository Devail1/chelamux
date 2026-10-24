// ---------------------------------------------------------------------------
// DECISIONS LOG: the durable, owner-independent home for what the decisions
// inbox has ever said. Lives in a centered dialog opened off the sidebar head's
// inbox button (CMX-288 moved it off-anchor, after CMX-171's anchored popover
// was measured spanning the dashboard's full width and occluding the whole Wall
// behind it), not gated behind any nav tab (cmx-106 first shipped it inside the
// Personas panel, cmx-107 moved it into an always-visible sidebar section,
// CMX-171 out of the sidebar into a topbar popover). CMX-228 (CMX-212 P08)
// ported it from static/js/decisions.js to React: this file is its state, the
// components in this folder render it.
//
// This is deliberately NOT a second store. chela/inbox.py::tick() appends every
// event to event_log (chela/event_log.py) whether or not a live session is
// registered as the orchestrator — "the log is owner-independent" is already
// true of the write path (see inbox.tick's unconditional `event_log.from_inbox`
// loop). So this dialog is a FILTERED READ of the same /api/log endpoint (the
// Feed view used to read it too, before CMX-279 deleted that view), narrowed to
// the kinds chela/inbox.py actually queues/logs, plus an owner chip
// (shared/orchestrator): a decision is never lost here even when the
// chip reads "nobody": that IS this panel being the fallback home.
//
// Wiring (cmx-107): the first refresh() tick seeds it (unconditional, no
// tab-open/dialog-open needed) and it stays live off the SSE `log` delta
// (onEvent), plus its fallback lane each tick. Nothing here is gated on the
// active view. The button's dot mirrors the owner's state, so it is legible
// without opening the dialog.
//
// The cursor/drain contract is drainLog's (feed.ts): resume from `next_seq`,
// never `last_seq`; a rotted cursor comes back as a `gap`, rendered, not
// papered over.
//
// The button ALSO carries an unseen-count badge (model.ts): the dot says
// "how healthy is the owner", the badge says "how many decisions have you not
// looked at yet", and both are always on: neither is gated behind the dialog
// being open. `lastSeen` persists in localStorage (`chela.decisions.lastSeen`)
// so the badge survives a reload; opening the dialog is the only thing that
// advances it.
//
// CMX-178: two additions on top of the read-only feed above, both purely
// client-side over data already on the wire (no new endpoint, no server
// change):
//   1. SEARCH (inboxQuery): a substring filter
//      over the events already held, so an OLD decision the short list has
//      scrolled past is still findable.
//   2. CLICK-THROUGH (openDecisionTicket): a row click resolves the event's
//      task_id against /api/dispatcher (the SAME poll Work's board reads),
//      because the decision payload alone is missing exactly the fields the
//      ticket exists to show (brief/judge_state/pr_checks/body — see
//      model.ts's findDispatcherRun doc comment). Found → that
//      authoritative run/task object opens, identical to what a Work card
//      would show for it. Not found (aged out of the dispatcher's bounded
//      recent window) or the fetch fails → falls back to
//      model.ts's partialItemFromDecisionPayload, built from the
//      event's own run_*/_window_payload fields (task_id/title/branch_name/
//      pr_url/pr_state/attempt, and on rework kinds reviews/rework_count/
//      last_error — see chela/inbox.py), but visibly marked partial rather
//      than rendering "No brief recorded" as if the run genuinely had none.
// ---------------------------------------------------------------------------
import { api } from '../../../shared/api/index.ts';
import { onEvent } from '../../../shared/events/index.ts';
import { refreshOrchestratorStatus } from '../../../shared/orchestrator/index.ts';
import { registerLane } from '../../../shared/scheduler/index.ts';
import { createStore } from '../../../shared/store/index.ts';
import { openTaskModal } from '../../taskmodal/index.ts';
import { drainLog, type LogEvent, type LogGap } from '../feed.ts';
import { findDispatcherRun, itemFromDecisionPayload, maxSeq, partialItemFromDecisionPayload, seedLastSeen } from '../model.ts';

export const LAST_SEEN_KEY = 'chela.decisions.lastSeen';

function loadLastSeen(): number | null {
  try {
    const raw = localStorage.getItem(LAST_SEEN_KEY);
    if (raw == null) return null;
    const n = Number(raw);
    return Number.isFinite(n) ? n : null;
  } catch {
    return null;
  }
}

function persistLastSeen(n: number) {
  try {
    localStorage.setItem(LAST_SEEN_KEY, String(n));
  } catch {
    /* storage unavailable: the badge just does not survive a reload */
  }
}

// Every kind chela/inbox.py ever queues or logs about a dispatch/watch outcome
// (see agent_events/run_events/_gone_event/_epoch_lost_event/_undeliverable).
// Deliberately NOT the Feed's tool-call/prompt firehose — this panel is
// decisions, not the whole log. CMX-328: keep every kind here a non-`hook.*` one.
// A read whose types are ALL non-hook is served from the core log
// (chela/event_log.py), which outlives the main log's rolls. One `hook.*` kind would
// send the whole query back to the current file, which the hook traffic rolls every
// few hours.
export const DECISION_TYPES = [
    'run_review', 'run_needs_human', 'run_changes_requested', 'run_failed',
    // CMX-197: the judge's own verdict on a run still sitting in `awaiting_review` —
    // the one thing that used to be structurally unnotifiable (a BLOCKED verdict
    // already surfaces via `run_changes_requested` above).
    'run_judge_clean', 'run_judge_cannot_verify',
    // CMX-239: the CAS-refused race for a BLOCKING verdict — the twin of
    // `run_judge_cannot_verify` above, but for a CONFIRMED finding (a guard survived
    // corruption) rather than an unknown. Also off the ordinary `run_changes_requested`
    // path, for the same reason: the run moved before the verdict could send it back.
    'run_judge_blocked_race',
    // CMX-358 (#480): a PR merged before a judge was ever SCHEDULED for it — the one
    // outcome that used to leave no trace at all. `judge_state` reaches this from
    // dispatcher.py's reconcile-to-done branch, not the judge itself; see chela/judge.py's
    // J_UNJUDGED_MERGED comment.
    'run_unjudged_merged',
    // CMX-86: the notices the orchestrator used to find only by hand — a judge that never
    // started, a run someone else reopened/closed, a red CI with its failing line, a PR gone
    // CONFLICTING, an agent gone quiet (or its completion marker unread), a hold that lapsed.
    'run_judge_not_started', 'run_reopened', 'run_was_closed', 'run_ci_red', 'run_ci_flake', 'run_pr_conflicting',
    'run_stuck', 'run_marker_unread', 'run_selfcheck_long', 'dispatch_hold_expired', 'dispatch_hold_released',
    'finished', 'blocked', 'died', 'gone_unknown', 'completed_gone',
    'watch_epoch_lost', 'inbox_undeliverable', 'inbox_self_healed',
];

const DECISIONS_LIMIT = 300; // per fetch
const DECISIONS_MAX = 500; // events kept in the browser
const CATCHUP_FETCHES = 8; // bounded drain, mirrors feed.ts's drainLog

export interface InboxState {
  events: LogEvent[];
  /** A gap the log reported: kept on screen once seen, never papered over. */
  gap: LogGap | null;
  /** true once the first real batch of events has landed. */
  loaded: boolean;
  /** The newest seq this browser has had the dialog open for. null = never seeded (a fresh
   * browser, or storage unavailable): seeded once the first batch lands, never eagerly to 0,
   * which would badge the whole historical backlog. */
  lastSeen: number | null;
}

export const inbox = createStore<InboxState>({ events: [], gap: null, loaded: false, lastSeen: loadLastSeen() });
/** Whether the dialog is open. */
export const inboxOpen = createStore(false);
// The search box: survives a close/reopen, deliberately (closing mid-search should not lose
// the filter). Client-side over the events already held: there is no server query.
export const inboxQuery = createStore('');

let cursor: number | null = null;
let boot: string | null = null;
let inflight = false;

function fetchBatch({ after_seq, after_boot, limit }: { after_seq: number | null; after_boot: string | null; limit: number }) {
  const qs = new URLSearchParams({ limit: String(limit) });
  if (after_seq != null) qs.set('after_seq', String(after_seq));
  if (after_boot) qs.set('after_boot', after_boot);
  DECISION_TYPES.forEach((t) => qs.append('type', t));
  return api('/api/log?' + qs.toString());
}

/** Read the log from our own cursor (one in flight at a time). */
export async function refreshLog(): Promise<void> {
  if (inflight) return;
  inflight = true;
  try {
    const drained = await drainLog(fetchBatch, { cursor, boot, limit: DECISIONS_LIMIT, maxFetches: CATCHUP_FETCHES });
    const s = inbox.get();
    const held = drained.cleared ? [] : s.events; // a gap invalidated what we were holding
    const events = held.concat(drained.events).slice(-DECISIONS_MAX);
    let lastSeen = s.lastSeen;
    // Seeded once, from the first batch; and while the dialog is open its rows ARE being looked at.
    if (lastSeen == null || inboxOpen.get()) {
      lastSeen = lastSeen == null ? seedLastSeen(events) : maxSeq(events);
      persistLastSeen(lastSeen);
    }
    cursor = drained.cursor;
    boot = drained.boot;
    // and we keep SAYING so, on screen
    inbox.set({ events, gap: drained.gap ?? s.gap, loaded: true, lastSeen });
  } finally {
    inflight = false;
  }
}

/** A fresh read of both the owner and the log (opening the dialog). */
export async function tickDecisions(): Promise<void> {
  await Promise.all([refreshOrchestratorStatus(), refreshLog()]);
}
// The fallback poll under the SSE deltas: every refresh() tick, whatever view is on screen.
// Its first tick IS the seed: the cursor starts empty. (The owner has its own lane,
// shared/orchestrator.)
registerLane('decisions', () => refreshLog());

// The SSE `log` delta: the log's seq moved, so fetch from our own cursor (the frame itself
// carries no events). Every frame, view-independent: the button is always on screen.
onEvent('log', () => void refreshLog());

/** Advance the "seen" cursor to the newest held event. Only opening the dialog calls this,
 * never a render, so a badge clears only because a human actually looked. */
function markSeen() {
  const s = inbox.get();
  if (!s.loaded) return; // nothing held yet: the first batch seeds, never a 0 that badges the backlog
  const lastSeen = maxSeq(s.events);
  persistLastSeen(lastSeen);
  inbox.set({ ...s, lastSeen });
}

/** Open the dialog: marks every held event seen and ticks the log, so it never shows a
 * stale read the moment it appears. */
export function openDecisions(): void {
  inboxOpen.set(true);
  markSeen();
  void tickDecisions();
}

export function closeDecisions(): void {
  inboxOpen.set(false);
}

/**
 * A click-through row: resolve the event's task_id against the dispatcher's own view (one
 * /api/dispatcher read per click, not a poll), and open the SAME task modal a Work card
 * opens. The dispatcher object is the complete ticket, so it is tried FIRST; the
 * payload-only fallback (marked partial) opens when that lookup comes up empty or the read
 * fails. Never throws: every path opens something. The inbox closes itself, or it would be
 * left open behind the ticket.
 */
export async function openDecisionTicket(e: LogEvent): Promise<void> {
  const fallback = itemFromDecisionPayload(e);
  if (!fallback) return; // no task_id on this event: nothing to open
  let item = null;
  try {
    item = findDispatcherRun(await api('/api/dispatcher'), fallback.task_id);
  } catch {
    /* unreachable dispatcher: fall through to the partial ticket */
  }
  closeDecisions();
  openTaskModal(item || partialItemFromDecisionPayload(e));
}
