// ---------------------------------------------------------------------------
// CMX-315: the phone Work view's model (pure). The title bar's project and counts, the
// project switcher's rows, the Rows layout's group order, and the compact card's lines.
//
// ⚠️ Colour is never the signal (Liav is red-weak): every flag is a WORD, and `warn` (orange)
// only marks the real warnings: risk high, a rework round, CI failing.
// ---------------------------------------------------------------------------
import { ageStr } from '../../shared/format/index.ts';
import type { IconName } from '../../shared/icons/index.ts';
import type { Run, Workflow, WorkPayload } from '../../shared/store/work.ts';
import { prLabel, runCardNote, runDisplayId, runHandoffNote, runStallNote } from '../runs/model.ts';
import { ciKind, wfShort } from '../taskmodal/model.ts';
import { STATUS_CHIPS, inFilter, type Card, type Column } from './board.ts';

// --- the title bar and the switcher -----------------------------------------------------------

export type Counts = { running: number; queued: number; needsYou: number };

/** A workflow's live counts: runs in flight, tasks waiting to be claimed, and runs that wait
 * on a human (`needs_human`, wherever the server filed it). */
export function projectCounts(wf: Workflow): Counts {
  const runs: Run[] = [...(wf.active_runs || []), ...(wf.awaiting_review_runs || []), ...(wf.recent_runs || [])];
  return {
    running: (wf.active_runs || []).length,
    queued: (wf.open_tasks || []).length,
    needsYou: runs.filter((r) => r?.status === 'needs_human').length,
  };
}

/** The counts of every workflow the filter keeps. */
export function totalCounts(data: WorkPayload | null | undefined, filter: readonly string[]): Counts {
  const out = { running: 0, queued: 0, needsYou: 0 };
  for (const wf of data?.workflows || []) {
    if (!inFilter(filter, wf.path)) continue;
    const c = projectCounts(wf);
    out.running += c.running;
    out.queued += c.queued;
    out.needsYou += c.needsYou;
  }
  return out;
}

/** "2 running · 15 queued", or "nothing queued" when both are zero. */
export function countsLine(c: Counts): string {
  return c.running || c.queued ? `${c.running} running · ${c.queued} queued` : 'nothing queued';
}

/** The bar's subtitle: the view, then the counts once a poll has landed, then a held queue. */
export function barSubtitle(data: WorkPayload | null | undefined, filter: readonly string[]): string {
  if (!data) return 'Work';
  const c = totalCounts(data, filter);
  const parts = ['Work', `${c.running} running`, `${c.queued} queued`];
  if (data.fleet_pause) parts.push('agents paused');
  else if (data.dispatch_hold) parts.push('dispatch paused');
  return parts.join(' · ');
}

/** The bar's title: the one project picked, "All projects", or how many a desktop
 * multi-select kept. */
export function projectTitle(filter: readonly string[]): string {
  if (!filter.length) return 'All projects';
  if (filter.length === 1) return wfShort(filter[0]) || '?';
  return `${filter.length} projects`;
}

/** The switcher shows a search field past this many projects. */
export const SEARCH_AFTER = 6;
export const RECENT_KEY = 'chela_work_recent_projects';
const RECENT_MAX = 20;

/** The viewer's recently picked projects, newest first; anything unreadable is none. */
export function loadRecent(storage: Pick<Storage, 'getItem'>): string[] {
  try {
    const v: unknown = JSON.parse(storage.getItem(RECENT_KEY) || '[]');
    return Array.isArray(v) ? v.filter((p): p is string => typeof p === 'string') : [];
  } catch {
    return [];
  }
}

/** `path` moved to the front of `recent`, capped. */
export function touchRecent(recent: readonly string[], path: string): string[] {
  return [path, ...recent.filter((p) => p !== path)].slice(0, RECENT_MAX);
}

export type ProjectRow = { path: string; name: string; counts: string; needsYou: number };

/** The switcher's project rows: recently picked first (newest first), then the rest in the
 * payload's order; with a query, only the names that contain it (any case). */
export function projectRows(data: WorkPayload | null | undefined, recent: readonly string[], query = ''): ProjectRow[] {
  const q = query.trim().toLowerCase();
  const rank = (p: string) => {
    const i = recent.indexOf(p);
    return i < 0 ? recent.length : i;
  };
  const wfs = (data?.workflows || []).map((wf, i) => ({ wf, i }));
  wfs.sort((a, b) => rank(a.wf.path) - rank(b.wf.path) || a.i - b.i);
  return wfs
    .map(({ wf }) => {
      const c = projectCounts(wf);
      return { path: wf.path, name: wfShort(wf.path) || wf.path, counts: countsLine(c), needsYou: c.needsYou };
    })
    .filter((r) => !q || r.name.toLowerCase().includes(q));
}

// --- the Rows layout ---------------------------------------------------------------------------

// Rows reads top-down by what needs eyes now: work in flight, then review, then the queue,
// then the rest. A Linear column is ranked by its name (the board keeps no state type).
const RANKS: [RegExp, number][] = [
  [/progress/, 0],
  [/review/, 1],
  [/todo|ready/, 2],
  [/backlog/, 3],
  [/done|complete/, 5],
  [/archiv|cancel|duplicate/, 6],
];
function rankOf(key: string): number {
  return RANKS.find(([re]) => re.test(key))?.[1] ?? 4;
}

/** The Rows layout's groups: the columns holding cards, in reading order (a stable sort, so
 * two columns of one rank keep the board's order). */
export function rowGroups(columns: readonly Column[]): Column[] {
  return columns
    .map((c, i) => ({ c, i, r: rankOf(c.key) }))
    .filter(({ c }) => c.cards.length > 0)
    .sort((a, b) => a.r - b.r || a.i - b.i)
    .map(({ c }) => c);
}

// --- the compact card --------------------------------------------------------------------------

export type Flag = { label: string; warn: boolean; icon?: IconName };

const CI_FLAG: Record<ReturnType<typeof ciKind>, Flag> = {
  passing: { label: 'CI passing', warn: false, icon: 'ciPassing' },
  failing: { label: 'CI failing', warn: true, icon: 'ciFailing' },
  pending: { label: 'CI pending', warn: false, icon: 'ciPending' },
  none: { label: 'no CI', warn: false, icon: 'ciNone' },
  unknown: { label: 'CI unknown', warn: false, icon: 'ciUnknown' },
};
const RISKS = ['high', 'normal', 'low'];

/** The compact card's flags line, in order: rework, risk, CI (only with a PR). */
export function cardFlags(card: Card): Flag[] {
  const out: Flag[] = [];
  const rework = Number(card.rework_count) || 0;
  if (rework > 0) out.push({ label: `rework ${rework}`, warn: true, icon: 'rework' });
  const risk = typeof card.risk === 'string' ? card.risk.toLowerCase() : '';
  if (RISKS.includes(risk)) out.push({ label: `risk ${risk}`, warn: risk === 'high' });
  if (card.pr_url) out.push(CI_FLAG[ciKind(card.pr_checks ?? undefined)]);
  return out;
}

/** The card's state word and icon: its run status, or what a bullet is. */
export function cardState(card: Card): { label: string; icon: IconName } {
  if (card.status === 'backlog') return { label: 'backlog', icon: 'statusOpen' };
  if (card.status === 'parked') return { label: 'parked', icon: 'parked' };
  if (card.park) return card.park.kind === 'slot' ? { label: 'waiting', icon: 'slotWait' } : { label: 'parked', icon: 'pause' };
  const chip = Object.hasOwn(STATUS_CHIPS, card.status) ? STATUS_CHIPS[card.status] : undefined;
  return chip ? { label: chip.label, icon: chip.icon } : { label: card.status.replace(/_/g, ' '), icon: 'ciUnknown' };
}

/** A Rows row's ONE flag: every warning, or with none the state (a run's) and its CI. */
export function rowFlag(card: Card): Flag {
  const flags = cardFlags(card);
  const warns = flags.filter((f) => f.warn);
  if (warns.length) return { label: warns.map((f) => f.label).join(' · '), warn: true };
  const run = !['open', 'backlog', 'parked'].includes(card.status);
  const parts = [run ? cardState(card).label : '', ...flags.map((f) => f.label)].filter(Boolean);
  return { label: parts.join(' · '), warn: false };
}

/** The card's id (PROJECT-N, a short hash, or nothing for a bullet) and PR label. */
export function cardIds(card: Card): { id: string; pr: string } {
  return { id: runDisplayId(card), pr: card.pr_url ? prLabel(card.pr_url) : '' };
}

/** CMX-318: a held run's note — its reason, since when, and what it waits for. */
function parkNote(park: NonNullable<Card['park']>, now: number): string {
  const age = park.since == null ? 'since ?' : ageStr(now - park.since);
  return [park.reason, age, park.until ? `until ${park.until}` : '', park.by ? `by ${park.by}` : ''].filter(Boolean).join(' · ');
}

/** The one note a compact card shows (two lines at most): a failure or close reason first,
 * then a park (CMX-318: a held run is not a stall), then a stall, then the agent's handoff,
 * then a parked bullet's reason. */
export function cardNote(card: Card, now: number = Date.now() / 1000): { text: string; warn: boolean } | null {
  const note = runCardNote(card);
  if (note) return { text: note.text, warn: !note.closed };
  if (card.park) return { text: parkNote(card.park, now), warn: false };
  const stall = runStallNote(card);
  if (stall) return { text: stall, warn: true };
  const handoff = runHandoffNote(card);
  if (handoff) return { text: handoff, warn: false };
  return card.status === 'parked' && card.reason ? { text: card.reason, warn: false } : null;
}
