// ---------------------------------------------------------------------------
// CMX-23: TRACKER COLUMNS for the Work board. Pure (no DOM, no fetch;
// static/js/kanbanlinearmodel.js until CMX-233). lanes.ts's 6 chela lanes stay the fallback
// for every other tracker kind.
//
// With `tracker: kind: linear` the board's columns ARE the team's workflow states, in Linear's
// own order (the API hands them over as `tracker_columns`), so the two boards can never
// disagree again: before this, four cards sat in chela's Review lane marked "failed" while
// Linear showed them in Todo / In Progress (Liav, 2026-10-07).
//
// chela's run state (running, rework N, failed, needs a human, ...) rides ON the card (the
// compact card's state and flags lines, phone.ts), never a column. The Canceled and Duplicate
// columns are `hidden`, as Linear collapses them: the API flags them.
// ---------------------------------------------------------------------------

export type TrackerColumn = { name: string; type: string; hidden: boolean };
export type BoardColumn<C> = { key: string; label: string; cards: C[] };

// Where a card goes when the tracker did not report its state (a finished issue has left the
// open read, a run predates the field): its run status → the state TYPE that status means,
// and which of that type's columns (In Progress is the first `started` state, In Review the
// last).
const STATUS_FALLBACK: Record<string, [string, 'first' | 'last']> = {
  backlog: ['backlog', 'first'],
  parked: ['backlog', 'first'],
  open: ['unstarted', 'first'],
  claimed: ['started', 'first'],
  running: ['started', 'first'],
  failed: ['started', 'first'],
  awaiting_review: ['started', 'last'],
  // A rework round is back in the agent's hands → In Progress, as the dispatcher writes it
  // (`_TRACKER_IN_PROGRESS_STATUSES`, chela/dispatcher.py).
  changes_requested: ['started', 'first'],
  needs_human: ['started', 'last'],
  done: ['completed', 'first'],
  closed: ['canceled', 'first'],
};

/** The pseudo-column a card lands in when NOTHING maps it: never dropped. */
export const UNMAPPED_KEY = 'tr-unmapped';

/**
 * The columns for the workflows on screen, or null when NONE of them has any (every one a
 * markdown/gh_issues tracker, or a failed state read): the caller then falls back to chela's
 * own lanes. Several Linear workflows: the first one's order, with any state the others add
 * appended once. CMX-193: a workflow WITHOUT states adds no columns; its cards land by
 * STATUS_FALLBACK's state types.
 */
export function trackerColumns(workflows: { tracker_columns?: unknown }[] | null | undefined): TrackerColumn[] | null {
  if (!Array.isArray(workflows) || !workflows.length) return null;
  const out: TrackerColumn[] = [];
  const seen = new Set<string>();
  for (const wf of workflows) {
    const cols: unknown = wf && wf.tracker_columns;
    if (!Array.isArray(cols)) continue;
    for (const c of cols as ({ name?: unknown; type?: unknown; hidden?: unknown } | null)[]) {
      if (!c || typeof c.name !== 'string' || !c.name) continue;
      const k = c.name.toLowerCase();
      if (seen.has(k)) continue;
      seen.add(k);
      out.push({ name: c.name, type: String(c.type || ''), hidden: !!c.hidden });
    }
  }
  return out.length ? out : null;
}

export function columnKey(name: string): string {
  return 'tr-' + String(name).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
}

type Placeable = { tracker_state?: string | null; status?: string | null; pr_url?: string | null };

/** The column (one of `columns`) a card belongs in, or null when none fits. */
export function columnOf(card: Placeable | null | undefined, columns: TrackerColumn[]): TrackerColumn | null {
  const state = card && typeof card.tracker_state === 'string' ? card.tracker_state.toLowerCase() : '';
  if (state) {
    const hit = columns.find((c) => c.name.toLowerCase() === state);
    if (hit) return hit;
  }
  const fb = card?.status && Object.hasOwn(STATUS_FALLBACK, card.status) ? STATUS_FALLBACK[card.status] : undefined;
  if (!fb) return null;
  const type = fb[0];
  // A died run whose PR is open was in review: it stays there.
  const which = card?.status === 'failed' && card.pr_url ? 'last' : fb[1];
  const ofType = columns.filter((c) => c.type === type);
  return (which === 'last' ? ofType[ofType.length - 1] : ofType[0]) ?? null;
}

/**
 * `cards` grouped into the VISIBLE columns, in column order. A card in a hidden column
 * (Canceled / Duplicate) is counted in `hiddenCount`, not shown; a card no column fits is
 * shown in a trailing "Unmapped" column: never dropped.
 */
export function trackerBoard<C extends Placeable>(columns: TrackerColumn[], cards: C[]): { columns: BoardColumn<C>[]; hiddenCount: number } {
  const byKey = new Map<string, BoardColumn<C>>();
  const board: BoardColumn<C>[] = [];
  for (const c of columns) {
    if (c.hidden) continue;
    const col = { key: columnKey(c.name), label: c.name, cards: [] as C[] };
    byKey.set(c.name.toLowerCase(), col);
    board.push(col);
  }
  let hiddenCount = 0;
  const unmapped: C[] = [];
  for (const card of cards) {
    const col = columnOf(card, columns);
    if (col === null) unmapped.push(card);
    else if (col.hidden) hiddenCount += 1;
    else byKey.get(col.name.toLowerCase())?.cards.push(card);
  }
  if (unmapped.length) board.push({ key: UNMAPPED_KEY, label: 'Unmapped', cards: unmapped });
  return { columns: board, hiddenCount };
}

/** CMX-193: the label of a column holding finished runs when the server's cap
 * (`recent_runs_capped`) cut some off: its count is the latest runs, not every one. */
export function sampledLabel(label: string, capped: boolean): string {
  return capped ? `${label} · latest` : label;
}
