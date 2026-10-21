// ---------------------------------------------------------------------------
// DECISIONS BADGE MODEL: pure unread-count/urgency math for the inbox button's
// unread badge. No DOM, no fetch, no localStorage: lib/inbox.ts owns persistence
// (the `chela.decisions.lastSeen` key) and the components render it; this file
// only ever answers "given the held events and a last-seen seq, what does the
// badge say", so the arithmetic is directly unit-testable without a DOM.
// (static/js/decisionsmodel.js until CMX-228, CMX-212 P07.)
//
// Unread = held decision events with a seq beyond what the popover has already
// shown you (`seq > lastSeenSeq`, strictly — `>=` would re-badge the event you
// just saw as still unread).
// ---------------------------------------------------------------------------

// Which decision kinds earn an urgency colour on the badge, not just a neutral
// count. 'bad' (vermillion) = something broke, was lost, or is stuck — needs a
// human now. 'warn' (orange) = a checkpoint that needs a human's judgement but
// nothing is broken. A type absent from this map (finished, completed_gone,
// inbox_self_healed, …) is informational — neutral count colour only. Colour
// is never the sole signal here either: the number itself is what carries the
// "you have unread decisions" fact; urgency is decoration on top of it.
import type { TaskItem } from '../taskmodal/model.ts';
import type { LogEvent } from './feed.ts';

export type Urgency = 'bad' | 'warn' | 'neutral';

export const DECISION_URGENCY: Record<string, Exclude<Urgency, 'neutral'>> = {
    run_failed: 'bad',
    died: 'bad',
    gone_unknown: 'bad',
    inbox_undeliverable: 'bad',
    watch_epoch_lost: 'bad',
    run_review: 'warn',
    run_needs_human: 'warn',
    run_changes_requested: 'warn',
    blocked: 'warn',
};

type Events = readonly (LogEvent | null | undefined)[] | null | undefined;

export function unseenEvents(events: Events, lastSeenSeq: number | null | undefined): LogEvent[] {
    const since = lastSeenSeq || 0;
    return (events || []).filter((e): e is LogEvent => ((e && e.seq) || 0) > since);
}

export function unreadCount(events: Events, lastSeenSeq: number | null | undefined): number {
    return unseenEvents(events, lastSeenSeq).length;
}

// Display cap — a fleet backlog is not a number worth reading past two digits.
export function formatUnreadCount(count: number): string {
    if (!count) return '';
    return count > 99 ? '99+' : String(count);
}

export function maxSeq(events: Events): number {
    return (events || []).reduce((m, e) => Math.max(m, (e && e.seq) || 0), 0);
}

// First-ever load (no stored lastSeen): seed to the CURRENT max held seq,
// never to 0 — 0 would badge the entire historical backlog as unread the
// moment the popover first ships to a browser that has never opened it.
export function seedLastSeen(events: Events): number {
    return maxSeq(events);
}

export function unreadUrgency(events: Events, lastSeenSeq: number | null | undefined): Urgency {
    const unseen = unseenEvents(events, lastSeenSeq);
    if (unseen.some(e => DECISION_URGENCY[e.type] === 'bad')) return 'bad';
    if (unseen.some(e => DECISION_URGENCY[e.type] === 'warn')) return 'warn';
    return 'neutral';
}

// ---------------------------------------------------------------------------
// TICKET CLICK-THROUGH: normalising a decision's payload into the shape
// the task modal's openTaskModal() already renders (CMX-178). chela/inbox.py
// writes the same underlying run facts under TWO different payload shapes:
//
//   * run_events (run_review/run_needs_human/run_changes_requested/run_failed)
//     keys the brief `title`, the state `run_status`, and carries
//     `branch_name`/`pr_url`/`pr_state`/`attempt`, plus on the two rework
//     kinds `rework_count`/`reviews`/`last_error`.
//   * _window_payload (finished/blocked/died/gone_unknown/completed_gone)
//     keys the SAME brief `task_title` instead, and has no branch_name/PR
//     detail at all (a window event, not a run one).
//
// Both normalise to one shape here so the inbox never has to know which
// kind it is looking at.
// ---------------------------------------------------------------------------

// `null` when the payload carries no task_id — nothing to click through to
// (a bare window/inbox-plumbing event: watch_epoch_lost, inbox_undeliverable,
// inbox_self_healed, or a gone/died/finished event for an ad-hoc window with
// no run row at all).
type Payload = Record<string, unknown>;
/** A payload field that is a string, else undefined. */
const str = (v: unknown) => (typeof v === 'string' ? v : undefined);
const num = (v: unknown) => (typeof v === 'number' ? v : undefined);
const when = (v: unknown) => (typeof v === 'string' || typeof v === 'number' ? v : undefined);

export function itemFromDecisionPayload(event: LogEvent | null | undefined): TaskItem | null {
    const p: Payload = (event && event.payload) || {};
    if (!p.task_id) return null;
    const item: TaskItem = {
        task_id: String(p.task_id),
        title: str(p.title) || str(p.task_title) || '',
        status: str(p.run_status),
        branch_name: str(p.branch_name),
        pr_url: str(p.pr_url),
        pr_state: str(p.pr_state),
        attempt: num(p.attempt),
        rework_count: num(p.rework_count),
        last_error: str(p.last_error),
        started_at: when(p.started_at),
        ended_at: when(p.ended_at),
    };
    if (Array.isArray(p.reviews) && p.reviews.length) {
        // taskmodalmodel.timelineSteps expects `review_history` as the JSON
        // TEXT column the dispatcher actually stores, and JSON.parses it back
        // — the payload already carries it parsed (inbox.py's `_reviews`), so
        // round-trip through stringify rather than growing a second parser.
        try { item.review_history = JSON.stringify(p.reviews); } catch { /* ignore */ }
    }
    return item;
}

// ---------------------------------------------------------------------------
// SEARCH — find an OLD decision the popover's default 300-event/short-list
// view has scrolled past. Pure substring match, case-insensitive, over every
// field a human would actually search by: the rendered summary, the event
// kind, the owning window, and every identifying field across both payload
// shapes above.
// ---------------------------------------------------------------------------

function _norm(s: unknown): string { return String(s == null ? '' : s).toLowerCase(); }

export function matchesDecisionQuery(event: LogEvent | null | undefined, query: string): boolean {
    const q = _norm(query).trim();
    if (!q) return true;             // a blank query matches everything
    const p: Payload = (event && event.payload) || {};
    const fields = [
        event && event.summary, event && event.type, event && event.wid,
        p.task_id, p.title, p.task_title, p.branch_name, p.pr_url, p.window_name,
    ].filter(v => v != null);
    return _norm(fields.join(' \0 ')).includes(q);
}

export function filterDecisionEvents(events: readonly LogEvent[] | null | undefined, query: string): LogEvent[] {
    const q = _norm(query).trim();
    if (!q) return [...(events || [])];
    return (events || []).filter(e => matchesDecisionQuery(e, q));
}

// ---------------------------------------------------------------------------
// DISPATCHER RESOLUTION (CMX-178 rework) — a decision's payload only ever
// carries the handful of run fields chela/inbox.py chose to log at event time
// (see itemFromDecisionPayload's doc comment above): never `brief`,
// `judge_state`, `pr_checks`, or `body`, because the payload is a point-in-
// time SUMMARY of the run, not the run row itself. /api/dispatcher's run
// dicts (chela.dispatcher.list_runs' full column set, threaded through
// app.py's api_dispatcher) DO carry those, so a click-through must prefer
// that authoritative object over the payload-shaped one whenever the run is
// still inside the dispatcher's bounded recent/active/awaiting window — a
// ticket built purely from the payload is missing the exact columns the
// modal exists to show.
// ---------------------------------------------------------------------------

// Every run/task bucket a workflow entry in /api/dispatcher's response
// carries that a decision's task_id could resolve against. `open_tasks`
// entries key their id as `id` (chela.sources.Task.id, never claimed into a
// run yet); every run bucket keys it as `task_id` (the runs-table column) —
// two different field names for the same identity, so the search below
// checks both rather than assuming one.
const _RUN_BUCKETS = ['active_runs', 'awaiting_review_runs', 'recent_runs'] as const;

// Searches every workflow /api/dispatcher returned for the run/task behind a
// decision's task_id. Returns the exact object found (never a copy) so
// openTaskModal renders the SAME fields the Work board's card would, or
// `null` when nothing matches (aged out of the bounded recent-runs window,
// or a malformed/empty response) — callers fall back to the payload-derived
// item in that case, never throw.
type Workflow = Partial<Record<'open_tasks' | (typeof _RUN_BUCKETS)[number], (TaskItem | null)[]>>;

export function findDispatcherRun(dispatcherData: unknown, taskId: string | null | undefined): TaskItem | null {
    if (!taskId) return null;
    const workflows = ((dispatcherData as { workflows?: (Workflow | null)[] } | null)?.workflows) || [];
    for (const wf of workflows) {
        for (const t of (wf && wf.open_tasks) || []) {
            if (t && t.id === taskId) return t;
        }
        for (const bucket of _RUN_BUCKETS) {
            for (const r of (wf && wf[bucket]) || []) {
                if (r && r.task_id === taskId) return r;
            }
        }
    }
    return null;
}

// The honest note stamped onto a fallback ticket — shown in place of "No
// brief recorded for this task." (the task modal's Brief tab), which reads as
// a lie when the truth is "not loaded here", not "this task never had one".
export const DISPATCHER_AGED_OUT_NOTE =
    "This run has aged out of the dispatcher's recent window (or it could " +
    "not be reached). Showing only what this decision event recorded; " +
    'brief, judge state, and CI are unavailable here.';

// The fallback ticket built purely from the decision's own payload, same
// shape as itemFromDecisionPayload but visibly marked partial. briefSource()
// (taskmodal/model.ts) prefers `brief` > `body` > `raw`, and
// itemFromDecisionPayload never sets any of the three — so stamping `body`
// here is exactly what the brief pane already renders, with no change to
// the task modal needed.
export function partialItemFromDecisionPayload(event: LogEvent | null | undefined): TaskItem | null {
    const item = itemFromDecisionPayload(event);
    if (!item) return null;
    item.body = DISPATCHER_AGED_OUT_NOTE;
    return item;
}
