// ---------------------------------------------------------------------------
// The Work board's model (pure): the /api/dispatcher payload → the columns and cards the
// board draws. Ported from static/js/kanban.js by CMX-233 (CMX-212 P12-P14).
//
// flatten() builds per-status buckets across every workflow; boardColumns() groups them into
// chela's 6 lanes (lanes.ts) or, when every workflow on screen has tracker states, into
// Linear's own columns (linear.ts). A lane can hold more than one status, so every non-backlog
// card also carries its own status pill (STATUS_CHIPS).
// ---------------------------------------------------------------------------
import type { IconName } from '../../shared/icons/index.ts';
import type { Run, WorkPayload } from '../../shared/store/work.ts';
import type { TaskItem } from '../taskmodal/model.ts';
import { KANBAN_LANES, KANBAN_LANE_LABELS, laneOf, type Lane } from './lanes.ts';
import { columnKey, sampledLabel, trackerBoard, trackerColumns, type BoardColumn } from './linear.ts';

/** One card: a run row, an open task, a parked bullet or a backlog bullet, flattened. */
export type Card = TaskItem &
  Omit<Run, keyof TaskItem> & {
    status: string;
    section?: string;
    file?: string;
    line_number?: number;
    reason?: string;
  };

export type Column = BoardColumn<Card>;

/** Done and Archived show at most this many cards each, newest first. */
export const DONE_LIMIT = 20;

const BUCKETS = ['backlog', 'parked', 'open', 'claimed', 'running', 'awaiting_review', 'failed', 'done', 'closed'] as const;
type Bucket = (typeof BUCKETS)[number];

// The WITHIN-lane order for lanes that combine buckets: Backlog = BACKLOG.md ideas, then
// parked bullets; In Progress = running, then claimed; Review = the review loop's cards (each
// keeps its own status), then failed. The LANE each bucket lands in is laneOf()'s.
export const BUCKET_ORDER: readonly Bucket[] = ['backlog', 'parked', 'open', 'running', 'claimed', 'awaiting_review', 'failed', 'done', 'closed'];

const pick = <T extends string>(v: string | null | undefined, allowed: readonly T[], fallback: T): T =>
  (allowed as readonly string[]).includes(v ?? '') ? (v as T) : fallback;

/** The payload's cards in per-status buckets. */
export function flatten(data: WorkPayload | null | undefined): Record<Bucket, Card[]> {
  const buckets = Object.fromEntries(BUCKETS.map((k) => [k, [] as Card[]])) as Record<Bucket, Card[]>;
  for (const wf of data?.workflows || []) {
    // Open, backlog and parked items carry no workflow_path of their own: the API gives it
    // per workflow, and every card needs its workflow chip.
    const from = { workflow_path: wf.path, project_key: wf.project_key || undefined };
    for (const b of wf.backlog_items || []) {
      buckets.backlog.push({ status: 'backlog', title: b.text, section: b.section, file: b.file, ...from });
    }
    for (const t of wf.parked_tasks || []) {
      buckets.parked.push({ status: 'parked', task_id: t.id, title: t.title, file: t.file, line_number: t.line_number, raw: t.raw, reason: t.reason, ...from });
    }
    for (const t of wf.open_tasks || []) {
      buckets.open.push({
        status: 'open',
        task_id: t.id,
        title: t.title ?? undefined,
        file: t.file ?? undefined,
        line_number: t.line_number ?? undefined,
        // `body` is an un-dispatched task's richest brief, `raw` its fallback (the modal reads both).
        raw: t.raw,
        body: t.body,
        tracker_url: t.tracker_url || null, // CMX-5: the Linear issue URL
        tracker_state: t.tracker_state || null, // CMX-168: the column linear.ts places it by
        closed_run_stall: t.closed_run_stall || null, // CMX-68: a closed run holds this task
        ...from,
      });
    }
    // A run row names its own workflow; the one it was listed under is the fallback.
    const asCard = (r: Run & { workflow_path?: string }, status: string) => ({ ...r, status, workflow_path: r.workflow_path || wf.path }) as Card;
    for (const r of wf.active_runs || []) {
      const status = pick(r.status, ['claimed', 'running'], 'claimed');
      buckets[status].push(asCard(r, status));
    }
    // The column holds the whole review loop; each card KEEPS ITS OWN STATUS, or a PR that
    // just failed review would get a Merge button.
    for (const r of wf.awaiting_review_runs || []) buckets.awaiting_review.push(asCard(r, r.status || 'awaiting_review'));
    for (const r of wf.recent_runs || []) {
      const status = pick(r.status, ['done', 'failed', 'closed'], 'done');
      buckets[status].push(asCard(r, status));
    }
  }
  // Most recent first for the run columns; Open keeps source order.
  const by = (k: 'started_at' | 'ended_at') => (a: Card, b: Card) => String(b[k] || '').localeCompare(String(a[k] || ''));
  buckets.claimed.sort(by('started_at'));
  buckets.running.sort(by('started_at'));
  buckets.awaiting_review.sort(by('ended_at'));
  buckets.failed.sort(by('ended_at'));
  buckets.done = buckets.done.sort(by('ended_at')).slice(0, DONE_LIMIT);
  buckets.closed = buckets.closed.sort(by('ended_at')).slice(0, DONE_LIMIT);
  return buckets;
}

/** The buckets regrouped into the 6 lanes: each bucket consumed exactly once, its sort kept. */
export function lanesFromBuckets(buckets: Record<Bucket, Card[]>): Record<Lane, Card[]> {
  const lanes = Object.fromEntries(KANBAN_LANES.map((k) => [k, [] as Card[]])) as Record<Lane, Card[]>;
  for (const key of BUCKET_ORDER) lanes[laneOf(key)].push(...buckets[key]);
  return lanes;
}

// --- The project filter (CMX-194: a multi-select) -------------------------------------------
// `selected` holds workflow paths; EMPTY means every project. Each pill toggles its own
// project; "All" clears the selection.

export function inFilter(selected: readonly string[], path: string | undefined): boolean {
  return !selected.length || selected.includes(path ?? '');
}

export function toggleProject(selected: readonly string[], path: string): string[] {
  return selected.includes(path) ? selected.filter((p) => p !== path) : [...selected, path];
}

/** Drop a selected project the payload no longer has, so a vanished one never empties the board. */
export function pruneFilter(selected: readonly string[], workflows: readonly string[]): string[] {
  const kept = selected.filter((p) => workflows.includes(p));
  return kept.length === selected.length ? (selected as string[]) : kept;
}

export const FILTER_KEY = 'chela_kanban_projects';

/** The viewer's saved selection; anything unreadable is "every project". */
export function loadFilter(storage: Pick<Storage, 'getItem'>): string[] {
  try {
    const v: unknown = JSON.parse(storage.getItem(FILTER_KEY) || '[]');
    return Array.isArray(v) ? v.filter((p): p is string => typeof p === 'string') : [];
  } catch {
    return [];
  }
}

/** The board's columns for `data` under the project filter. */
export function boardColumns(data: WorkPayload | null | undefined, selected: readonly string[]): Column[] {
  const buckets = flatten(data);
  const keep = (cards: Card[]) => cards.filter((c) => inFilter(selected, c.workflow_path));
  const shown = (data?.workflows || []).filter((wf) => inFilter(selected, wf.path));
  // CMX-193: the server sends at most 10 finished runs a workflow; Done is a sample then.
  const capped = shown.some((wf) => wf.recent_runs_capped);
  // CMX-23: when the workflows on screen have tracker states (Linear), the columns ARE those
  // states, in the tracker's order, and chela's run state is a badge on the card.
  const tracker = trackerColumns(shown);
  if (tracker) {
    const completed = new Set(tracker.filter((c) => c.type === 'completed').map((c) => columnKey(c.name)));
    const all = keep(BUCKET_ORDER.flatMap((k) => buckets[k]));
    return trackerBoard(tracker, all).columns.map((c) => (completed.has(c.key) ? { ...c, label: sampledLabel(c.label, capped) } : c));
  }
  const lanes = lanesFromBuckets(buckets);
  return KANBAN_LANES.map((k) => ({ key: k, label: k === 'done' ? sampledLabel(KANBAN_LANE_LABELS[k], capped) : KANBAN_LANE_LABELS[k], cards: keep(lanes[k]) }));
}

// --- A card's chips --------------------------------------------------------------------------

/** Every non-backlog card's status pill. The WORD carries the meaning; `cls` only adds a colour
 * (Liav is red-weak). `closed` (CMX-265) has its own word so it never reads as done. */
export const STATUS_CHIPS: Record<string, { label: string; cls: string; icon: IconName }> = {
  open: { label: 'open', cls: 'st-open', icon: 'statusOpen' },
  claimed: { label: 'claimed', cls: 'st-claimed', icon: 'claimed' },
  running: { label: 'running', cls: 'st-running', icon: 'running' },
  awaiting_review: { label: 'awaiting review', cls: 'st-awaiting', icon: 'awaitingReview' },
  changes_requested: { label: 'changes requested', cls: 'st-changes', icon: 'retry' },
  needs_human: { label: 'needs a human', cls: 'st-needs-human', icon: 'needsHuman' },
  failed: { label: 'failed', cls: 'st-failed', icon: 'close' },
  done: { label: 'done', cls: 'st-done', icon: 'done' },
  closed: { label: 'closed, not merged', cls: 'st-closed-unmerged', icon: 'closedUnmerged' },
};

/** The merge control on an Awaiting Review card with a PR: `merged` shows a badge, `button`
 * the Merge button, null nothing. ⛔ No button while CI is red, pending or unread: the server
 * refuses those merges too, but the orchestrator must not be ABLE to click one by accident
 * (on 2026-07-14 it did, and the base branch broke). */
export function mergeControl(card: Card): 'merged' | 'button' | null {
  if (card.status !== 'awaiting_review' || !card.pr_url || !card.task_id) return null;
  if (card.pr_state === 'merged') return 'merged';
  if (card.pr_state === 'closed') return null;
  return card.pr_checks === 'passing' || card.pr_checks === 'none' ? 'button' : null;
}

/** The branch chip, or `file:line` for a row with no branch. Under the project-key scheme the
 * branch is the lowercase display id, so it shows only when it differs from `displayId`. */
export function branchOrLine(card: Card, displayId: string): string {
  if (card.branch_name) return card.branch_name.toLowerCase() !== displayId.toLowerCase() ? card.branch_name : '';
  return card.file ? `${card.file.split('/').pop()}:${card.line_number}` : '';
}

/** The sidebar's Work badges: the same numbers as the board's Running and Review columns. */
export function workBadgeCounts(data: WorkPayload | null | undefined): { runs: number; prs: number } {
  let runs = 0;
  let prs = 0;
  for (const wf of data?.workflows || []) {
    runs += (wf.active_runs || []).length;
    prs += (wf.awaiting_review_runs || []).length;
  }
  return { runs, prs };
}
