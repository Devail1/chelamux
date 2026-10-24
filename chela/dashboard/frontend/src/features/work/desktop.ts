// ---------------------------------------------------------------------------
// CMX-316: the desktop Work view's model (pure; Liav's approved mockup, 10-23). The header's
// subtitle, which columns collapse to rails at the board's edges and which of them the viewer
// left expanded, the columns the viewer hid, and the Rows table's cells and sort.
//
// ⚠️ Colour is never the signal (Liav is red-weak): every cell is a WORD.
// ---------------------------------------------------------------------------
import type { WorkPayload } from '../../shared/store/work.ts';
import { ciKind } from '../taskmodal/model.ts';
import type { Card, Column } from './board.ts';
import { cardIds, cardState, parkFlag, totalCounts } from './phone.ts';

export type KeyStorage = Pick<Storage, 'getItem' | 'setItem'>;

// --- the header ----------------------------------------------------------------------------------

/** Under the project: "3 running · 14 queued · dispatch on" (or paused, or agents paused);
 * just "Work" before the first poll. */
export function headSubtitle(data: WorkPayload | null | undefined, filter: readonly string[]): string {
  if (!data) return 'Work';
  const c = totalCounts(data, filter);
  const state = data.fleet_pause ? 'agents paused' : data.dispatch_hold ? 'dispatch paused' : 'dispatch on';
  return `${c.running} running · ${c.queued} queued · ${state}`;
}

// --- the board's rails ---------------------------------------------------------------------------

export type Side = 'left' | 'right';

/** Where a column collapses to a rail: Backlog at the left edge; Done, Archived and a
 * tracker's Completed / Canceled / Duplicate at the right. Everything else is a fixed column
 * (null). A Linear column is matched by its whole key (`tr-<name>`; the board keeps no state
 * type), so "Backlog grooming" or "Incomplete" stays a column. */
export function railSide(key: string): Side | null {
  if (/^(tr-)?backlog$/.test(key)) return 'left';
  if (/^(tr-)?(done|completed?|archived|cancell?ed|duplicate)$/.test(key)) return 'right';
  return null;
}

/** The board's columns, split by where they sit: each group keeps the board's order. */
export function arrange<C extends { key: string }>(cols: readonly C[]): { left: C[]; middle: C[]; right: C[] } {
  const out = { left: [] as C[], middle: [] as C[], right: [] as C[] };
  for (const c of cols) out[railSide(c.key) ?? 'middle'].push(c);
  return out;
}

export const RAILS_KEY = 'chela_work_rails';

/** A board view's name for its remembered rails: the projects it shows, in any order. */
export function viewKey(filter: readonly string[]): string {
  return filter.length ? [...filter].sort().join('\n') : '*';
}

function readRails(storage: KeyStorage): Record<string, string[]> {
  const parsed: unknown = JSON.parse(storage.getItem(RAILS_KEY) || '{}');
  return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? (parsed as Record<string, string[]>) : {};
}

/** The rails `view` left expanded. Anything unreadable (no storage, junk JSON) is none: every
 * rail collapsed, never a crash. */
export function loadRails(storage: KeyStorage, view: string): string[] {
  try {
    const v: unknown = readRails(storage)[view];
    return Array.isArray(v) ? v.filter((k): k is string => typeof k === 'string') : [];
  } catch {
    return [];
  }
}

/** Persist `view`'s expanded rails next to every other view's; false when storage refused. */
export function saveRails(storage: KeyStorage, view: string, expanded: readonly string[]): boolean {
  try {
    let all: Record<string, string[]> = {};
    try {
      all = readRails(storage);
    } catch {
      /* junk is replaced */
    }
    storage.setItem(RAILS_KEY, JSON.stringify({ ...all, [view]: [...expanded] }));
    return true;
  } catch {
    return false;
  }
}

// --- the columns the viewer hid (⋯ › Column options) -----------------------------------------------

export const HIDDEN_COLS_KEY = 'chela_work_hidden_columns';

/** The column keys the viewer hid on the desktop board; anything unreadable is none. */
export function loadHiddenCols(storage: Pick<Storage, 'getItem'>): string[] {
  try {
    const v: unknown = JSON.parse(storage.getItem(HIDDEN_COLS_KEY) || '[]');
    return Array.isArray(v) ? v.filter((k): k is string => typeof k === 'string') : [];
  } catch {
    return [];
  }
}

/** `key` hidden if shown, shown if hidden. */
export function toggleHidden(hidden: readonly string[], key: string): string[] {
  return hidden.includes(key) ? hidden.filter((k) => k !== key) : [...hidden, key];
}

/** The desktop board draws every column but the ones the viewer hid: a Backlog or Done with
 * nothing in it is a 44px rail, and an empty middle column says "Nothing here". */
export function shownColumns(cols: readonly Column[], hidden: readonly string[]): Column[] {
  return cols.filter((c) => !hidden.includes(c.key));
}

// --- the Rows table --------------------------------------------------------------------------------

export type SortKey = 'id' | 'title' | 'state' | 'pr' | 'ci' | 'risk' | 'age';
export type Sort = { key: SortKey; dir: 'asc' | 'desc' } | null;

export const TABLE_COLUMNS: { key: SortKey; label: string }[] = [
  { key: 'id', label: 'ID' },
  { key: 'title', label: 'Title' },
  { key: 'state', label: 'State' },
  { key: 'pr', label: 'PR' },
  { key: 'ci', label: 'CI' },
  { key: 'risk', label: 'Risk' },
  { key: 'age', label: 'Age' },
];

/** Seconds since the card last moved (it ended, else it started); null with neither. */
export function ageSeconds(card: Card, now: number): number | null {
  const iso = card.ended_at || card.started_at;
  if (!iso) return null;
  const t = new Date(iso).getTime();
  return Number.isNaN(t) ? null : Math.max(0, (now - t) / 1000);
}

/** `45s`, `5m`, `3h`, `2d` (whole units elapsed, so the unit never reads `60m`); '' with no age. */
export function shortAge(seconds: number | null): string {
  if (seconds === null) return '';
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86400)}d`;
}

const CI_WORD = { passing: 'passing', failing: 'failing', pending: 'pending', none: 'no CI', unknown: 'unknown' } as const;
const RISKS = ['high', 'normal', 'low'];

export type Cells = { id: string; title: string; state: string; stateTitle?: string; pr: string; ci: string; ciWarn: boolean; risk: string; riskWarn: boolean; age: string };

/** A table row's words. CI only with a PR; risk only when the issue carries a known one. */
export function rowCells(card: Card, now: number): Cells {
  const { id, pr } = cardIds(card);
  const ci = card.pr_url ? ciKind(card.pr_checks ?? undefined) : null;
  const park = card.park ? parkFlag(card.park, now / 1000) : null;   // `now` is ms; a park's `since` is s
  const risk = typeof card.risk === 'string' && RISKS.includes(card.risk.toLowerCase()) ? card.risk.toLowerCase() : '';
  return {
    id,
    title: card.title || '',
    // CMX-318: a held run's state cell is its park flag (dim, the reason in full as its title)
    state: park ? park.label : cardState(card).label,
    stateTitle: park?.title,
    pr,
    ci: ci ? CI_WORD[ci] : '',
    ciWarn: ci === 'failing',
    risk,
    riskWarn: risk === 'high',
    age: shortAge(ageSeconds(card, now)),
  };
}

// What sorts first ascending: the worst CI, the highest risk; a blank is always last.
const CI_RANK: Record<string, number> = { failing: 0, pending: 1, unknown: 2, passing: 3, 'no CI': 4 };
const RISK_RANK: Record<string, number> = { high: 0, normal: 1, low: 2 };

function sortValue(card: Card, key: SortKey, now: number): string | number | null {
  const c = rowCells(card, now);
  switch (key) {
    case 'pr': {
      const n = /^#(\d+)$/.exec(c.pr);
      return n ? Number(n[1]) : c.pr || null;
    }
    case 'ci':
      return c.ci ? (CI_RANK[c.ci] ?? 2) : null;
    case 'risk':
      return c.risk ? (RISK_RANK[c.risk] ?? 1) : null;
    case 'age':
      return ageSeconds(card, now);
    default:
      return c[key] || null;
  }
}

const collator = new Intl.Collator('en', { numeric: true, sensitivity: 'base' });

/** `cards` sorted by `sort` (a stable sort; blanks last in either direction), or as they came
 * with no sort. */
export function sortCards(cards: readonly Card[], sort: Sort, now: number): Card[] {
  if (!sort) return [...cards];
  const sign = sort.dir === 'asc' ? 1 : -1;
  return cards
    .map((card, i) => ({ card, i, v: sortValue(card, sort.key, now) }))
    .sort((a, b) => {
      if (a.v === null || b.v === null) return a.v === b.v ? a.i - b.i : a.v === null ? 1 : -1;
      const d = typeof a.v === 'number' && typeof b.v === 'number' ? a.v - b.v : collator.compare(String(a.v), String(b.v));
      return d ? sign * d : a.i - b.i;
    })
    .map(({ card }) => card);
}

/** A header click: a new column sorts ascending, the same one flips, a third click clears. */
export function nextSort(sort: Sort, key: SortKey): Sort {
  if (!sort || sort.key !== key) return { key, dir: 'asc' };
  return sort.dir === 'asc' ? { key, dir: 'desc' } : null;
}
