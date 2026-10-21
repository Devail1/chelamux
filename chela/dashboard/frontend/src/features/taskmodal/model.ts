// ---------------------------------------------------------------------------
// TASK-DETAIL MODAL MODEL: pure helpers for the brief pane + review timeline.
// No DOM: lib/TaskModal.tsx owns the render (the Jira-like "issue" view a kanban
// card click opens); this file only turns raw task/run fields into what the
// modal displays, so both are directly unit-testable. Legacy kanban.js and
// dispatcher.js import it too. (static/js/taskmodalmodel.js until CMX-228.)
// ---------------------------------------------------------------------------

/**
 * What the modal renders: a run dict (chela.dispatcher.list_runs' full column set), an open
 * task (`{id, title, file, raw, body, ...}`, no run yet) or a backlog item (no id at all).
 * Every field is optional for exactly that reason.
 */
export interface TaskItem {
  task_id?: string;
  id?: string;
  title?: string;
  status?: string;
  brief?: string | null;
  body?: string | null;
  raw?: string | null;
  workflow_path?: string;
  project_key?: string;
  task_number?: number | null;
  branch_name?: string;
  model?: string;
  pr_url?: string;
  pr_state?: string;
  pr_checks?: string;
  attempt?: number | null;
  rework_count?: number;
  judge_state?: string;
  judge_detail?: string;
  risk?: unknown;
  risk_reason?: string;
  tracker_url?: unknown;
  review_history?: string | null;
  last_error?: string;
  /** ISO text from the runs table; a decision payload may carry epoch seconds. */
  started_at?: string | number;
  ended_at?: string | number;
}

/** One round of `review_history`, as the Review tab shows it. */
export interface Round {
  round: number | null;
  at: string | null;
  state: string;
  detail: string;
  visible: number;
  heldOut: number;
}

// Which field actually has the task's brief, in priority order:
//   1. `item.brief`  — a run's persisted brief (dispatcher._spawn copies it at
//      claim time from the task's `body`, falling back to `raw`/title — see
//      chela.dispatcher._task_brief), so it's already the richest text
//      available once a task has been claimed.
//   2. `item.body`   — an open (never-claimed) task's FULL multi-line brief
//      (chela.sources.markdown._task_body: title + its dedented OBJECTIVE/
//      BOUNDARIES/GUARDS/VERIFY continuation), when the source captured one.
//   3. `item.raw`    — the bare bullet line / issue URL, for a one-line task
//      or a source with no notion of a continuation (gh_issues).
// `null`/`undefined`/`''` are all treated as "not present" at every step, and
// no field existing at all (a backlog item, a legacy pre-migration run row)
// resolves to `null` — never throws on a sparse/partial item.
export function briefSource(item: TaskItem | null | undefined): string | null {
  if (!item) return null;
  for (const v of [item.brief, item.body, item.raw]) {
    if (v != null && v !== '') return v;
  }
  return null;
}

// The modal header's display title. ⛔ Display-only — never touches the
// PARSED `title` field: a task's id is the hash of that title (chela.sources.
// markdown._task_id), so mutating it anywhere would orphan in-flight runs.
// Our briefs' first line is the whole bullet, e.g.
// "**📥 Move the Decisions log … (Liav, 2026-07-24).** Design is SETTLED…" —
// showing that raw is both long and full of literal `**`. When the title
// opens with a bold span, the concise title IS that span's inner text, so
// this returns just that; anything without a leading bold span (a plain
// one-line task title) is returned unchanged. Pure: the modal renders the
// result as inline markdown, so any remaining markdown renders instead of
// showing as literal text.
export function displayTitle(rawTitle: string | null | undefined): string {
  if (!rawTitle) return '';
  const m = String(rawTitle).match(/^\*\*([^*]+)\*\*/);
  return m?.[1] ?? rawTitle;
}

// review_history is a JSON TEXT column (chela.dispatcher.reviews_of's JS-side
// counterpart) — a list of {round, at, body, verdict} written oldest-first by
// request_changes()/reopen. Never throws: null, '', or malformed/non-list/
// non-object JSON all resolve to "no history yet" ([]), never a crash that
// would blank the whole modal over one bad row.
export function timelineSteps(reviewHistoryJson: string | null | undefined): Round[] {
  if (!reviewHistoryJson) return [];
  let parsed: unknown;
  try {
    parsed = JSON.parse(reviewHistoryJson);
  } catch {
    return [];
  }
  if (!Array.isArray(parsed)) return [];
  return parsed
    .filter((r): r is Record<string, unknown> => !!r && typeof r === 'object' && !Array.isArray(r))
    .map((r) => ({
      round: r.round != null ? Number(r.round) : null,
      at: typeof r.at === 'string' && r.at ? r.at : null,
      state: typeof r.verdict === 'string' && r.verdict ? r.verdict : 'review',
      detail: typeof r.body === 'string' ? r.body : '',
      // CMX-122: a round's survivor counts, for its collapsed one-line summary. Visible
      // = the structured `mutations` the judge stored; held-out ones are never stored,
      // only COUNTED in the body's `### 🙈 N held-out guard(s)` line (judge._held_out_section).
      visible: Array.isArray(r.mutations) ? r.mutations.length : 0,
      heldOut: heldOutCount(r.body),
    }));
}

export function heldOutCount(body: unknown): number {
  const m = typeof body === 'string' ? body.match(/^### 🙈 (\d+) held-out guard/m) : null;
  return m ? Number(m[1]) : 0;
}

// CMX-122: which tab the modal opens on. Review when the run is waiting on a review
// (`awaiting_review` / `needs_human`) or its judge returned a blocking verdict — but only
// when there IS a round to read; an empty Review tab is never the landing page.
const _REVIEW_STATUSES = new Set(['awaiting_review', 'needs_human']);
const _BLOCKING_JUDGE = new Set(['blocked', 'blocked_race']);

export type TabName = 'brief' | 'review';

export function defaultTab(item: TaskItem | null | undefined, nRounds: number): TabName {
  if (!item || !nRounds) return 'brief';
  return _REVIEW_STATUSES.has(item.status ?? '') || _BLOCKING_JUDGE.has(item.judge_state ?? '') ? 'review' : 'brief';
}

// CMX-122: the modal header's tracker link — EXACTLY the URL the Linear adapter fetched
// (`tracker_url`, the same source kanban.js's _kTrackerLink reads, CMX-5/CMX-94), never
// built from a workspace slug. A markdown/TODO or gh_issues item has none → null (plain
// key). Only https is linked: the value lands in an href, so `javascript:` renders nothing.
export function trackerHref(item: TaskItem | null | undefined): string | null {
  const url = item?.tracker_url;
  return typeof url === 'string' && /^https:\/\//i.test(url) ? url : null;
}

// ⚖️🎚️ CMX-405 — a run's RISK level (chela.sources.RISK_LEVELS), as the word the Work
// card and the task modal show plus a colour modifier. The WORD carries the meaning (Liav is
// red-weak); the class only agrees with it. `null` for anything that is not a run carrying a
// known level (an open/backlog item, a pre-CMX-405 row) — render nothing, never a guess.
// `reason` is the run row's `risk_reason` (marker / label / inferred: … / default), the
// chip's tooltip, so an inferred `high` says why.
const _RISK_CLS: Record<string, string> = { high: 'risk-high', normal: 'risk-normal', low: 'risk-low' };

export function riskChip(item: TaskItem | null | undefined): { label: string; cls: string; reason: string } | null {
  const level = item && typeof item.risk === 'string' ? item.risk.toLowerCase() : '';
  const cls = Object.hasOwn(_RISK_CLS, level) ? _RISK_CLS[level] : undefined;
  if (!item || !cls) return null;
  return { label: `risk: ${level}`, cls, reason: item.risk_reason || '' };
}

// The header's task key. PROJECT_KEY-N is the primary identity for runs with a task_number;
// pre-migration rows fall back to the legacy short hash. (dispatcher.js's _runDisplayId,
// which kanban.js still reads, until P10 ports it.)
export function runDisplayId(item: TaskItem): string {
  if (item.project_key && item.task_number != null) return `${item.project_key}-${item.task_number}`;
  return item.task_id || '';
}

// A PR link's compact label: "#NNN" from a GitHub URL, "PR" for any other host.
export function prLabel(prUrl: string): string {
  const m = String(prUrl).match(/\/pull\/(\d+)(?:[/?#]|$)/);
  return m ? `#${m[1]}` : 'PR';
}

// Short workflow label: the workflow file's directory (kanban.js's _wfName).
export function wfShort(path: string | undefined): string {
  if (!path) return '';
  const parts = String(path).replace(/\/+$/, '').split('/');
  if (parts.length >= 2) return parts[parts.length - 2] || parts[parts.length - 1] || '';
  return parts[parts.length - 1] || path;
}

export function statusLabel(status: string | undefined): string {
  return String(status || '?').replace(/_/g, ' ');
}

// The CI chip: kanban.js's CI_CHIPS words and `.kanban-ci-chip`/`.ci-*` classes. The word
// carries the meaning; the icon and the colour only agree with it.
export type CiKind = 'passing' | 'failing' | 'pending' | 'none' | 'unknown';
export const CI_CHIP: Record<CiKind, { label: string; cls: string }> = {
  passing: { label: 'ci green', cls: 'ci-passing' },
  failing: { label: 'CI RED', cls: 'ci-failing' },
  pending: { label: 'ci pending', cls: 'ci-pending' },
  none: { label: 'no ci', cls: 'ci-none' },
  unknown: { label: 'ci unknown', cls: 'ci-unknown' },
};

export function ciKind(prChecks: string | undefined): CiKind {
  return prChecks && Object.hasOwn(CI_CHIP, prChecks) ? (prChecks as CiKind) : 'unknown';
}

// judge_state is one of chela.judge's J_* values (running/clean/blocked/cannot_verify/
// blocked_race): the existing severity-ladder badge classes, so a blocked judgement reads
// exactly as loud as a failed run, not a new colour.
export const JUDGE_BADGE: Record<string, string> = {
  clean: 'badge-on',
  blocked: 'badge-off',
  running: 'badge-awaiting',
  cannot_verify: 'badge-priority-low',
  // CMX-239: a CONFIRMED blocking finding the run row never recorded (the CAS-refused
  // race): as loud as an ordinary `blocked`, never `cannot_verify`'s low-priority tier.
  blocked_race: 'badge-off',
};

export function judgeBadgeClass(state: string): string {
  return Object.hasOwn(JUDGE_BADGE, state) ? (JUDGE_BADGE[state] as string) : 'badge-priority-low';
}

// "14:05 Oct 21": util.js's shortTime, for the side rows and round summaries.
export function shortTime(iso: string | number): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return `${d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false })} ${d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}`;
}
