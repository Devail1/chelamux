// CMX-434: the "Suggest names" dialog's pure half: which sessions it lists, its rows, the inline
// checks, and the apply (one rename and one title request per row, each reported on its own).
// No DOM; the requests go through the `post` it is handed.
import { type AgentRow, windowKind } from '../sidebar/model.ts';

export const NAME_MAX = 20;
export const TITLE_MAX = 40;
/** The dialog's name rule (chela/suggest_names.py `clean_name` proposes only these). */
export const NAME_RE = /^[a-z0-9-]{1,20}$/;
export const NAME_RULE = `1-${NAME_MAX} lowercase letters, digits or '-'`;

/** One row of POST /api/agents/suggest-names (chela/suggest_names.py `suggest`). */
export interface Suggestion {
  wid: string;
  name: string;
  manual_name: boolean;
  title: string | null;
  title_pending: string | null;
  content: boolean;
  note: string | null;
  suggested_name: string;
  suggested_title: string;
}

/** A row as the dialog edits it. */
export interface Row {
  wid: string;
  name: string;
  manual: boolean;
  title: string;
  /** A title already queued for this session (it waits for idle). */
  pending: string | null;
  note: string | null;
  newName: string;
  newTitle: string;
  checked: boolean;
}

/** The sessions the dialog asks about: live Claude sessions, without dispatched runs and
 * judges unless `includeAll`. A plain shell (no Claude) is never listed. */
export function candidateWids(agents: readonly AgentRow[], includeAll: boolean): string[] {
  return agents
    .filter(a => !!a.window_id && a.claude_running === true)
    .filter(a => includeAll || !['dispatched', 'judge'].includes(windowKind(a)))
    .map(a => a.window_id as string);
}

/** The editable rows. A row whose name a human chose (manual) is pre-unchecked: applying
 * would overwrite that choice. So is a row the suggestion would not change. */
export function initialRows(s: readonly Suggestion[]): Row[] {
  return s.map(x => {
    const title = x.title ?? '';
    const same = x.suggested_name === x.name && x.suggested_title === title;
    return {
      wid: x.wid, name: x.name, manual: x.manual_name, title, pending: x.title_pending, note: x.note,
      newName: x.suggested_name, newTitle: x.suggested_title, checked: !x.manual_name && !same,
    };
  });
}

/** Whether applying row `r` changes its name / its title. */
export const nameChanges = (r: Row) => r.newName !== r.name;
export const titleChanges = (r: Row) => r.newTitle.trim() !== '' && r.newTitle.trim() !== r.title;

export interface Problems { name?: string; title?: string }

/**
 * Why each checked row must not be applied yet, by wid (a row with none is absent). A changed
 * name breaks the rule, or equals another window's CURRENT name (renames apply one at a time,
 * and the rename API refuses a name another window holds) or another checked row's new name.
 * A title longer than TITLE_MAX is refused (an empty one is simply not applied).
 */
export function rowProblems(rows: readonly Row[], windows: readonly AgentRow[]): Record<string, Problems> {
  const out: Record<string, Problems> = {};
  const renaming = rows.filter(r => r.checked && nameChanges(r));
  for (const r of rows) {
    if (!r.checked) continue;
    const p: Problems = {};
    if (nameChanges(r)) {
      if (!NAME_RE.test(r.newName)) p.name = `Name must be ${NAME_RULE}`;
      else if (windows.some(w => w.window_id !== r.wid && w.name === r.newName)
        || renaming.some(o => o.wid !== r.wid && o.newName === r.newName)) {
        p.name = `Another window is named ${r.newName}`;
      }
    }
    if (r.newTitle.trim().length > TITLE_MAX) p.title = `Title must be at most ${TITLE_MAX} characters`;
    if (p.name || p.title) out[r.wid] = p;
  }
  return out;
}

/** What applying one row did: each half is absent when it did not change. */
export interface Outcome {
  name?: { ok: true } | { ok: false; error: string };
  title?: { state: 'applied' | 'pending'; reason?: string | null } | { state: 'refused'; reason: string };
}

export type Post = (path: string, body: unknown) => Promise<unknown>;

const enc = (wid: string) => encodeURIComponent(wid);

/**
 * Apply the checked rows, one at a time (a rename then a title, per row), and report each half
 * of each row on its own: one failure never stops or hides the others. Names go through the
 * rename API (which marks them manual); titles through the title API (typed only into an idle
 * Claude session, else queued). A request that throws reads as a failure, never as success.
 */
export async function applyRows(rows: readonly Row[], post: Post): Promise<Record<string, Outcome>> {
  const out: Record<string, Outcome> = {};
  for (const r of rows) {
    if (!r.checked) continue;
    const o: Outcome = {};
    if (nameChanges(r)) {
      try {
        const res = (await post(`/api/agents/${enc(r.wid)}/rename`, { name: r.newName })) as { ok?: boolean; error?: string } | null;
        o.name = res?.ok === true ? { ok: true } : { ok: false, error: res?.error || 'rename failed' };
      } catch {
        o.name = { ok: false, error: 'rename request failed' };
      }
    }
    if (titleChanges(r)) {
      try {
        const res = (await post(`/api/agents/${enc(r.wid)}/title`, { title: r.newTitle.trim() })) as { state?: string; reason?: string | null } | null;
        o.title = res?.state === 'applied' || res?.state === 'pending'
          ? { state: res.state, reason: res.reason ?? null }
          : { state: 'refused', reason: res?.reason || 'title failed' };
      } catch {
        o.title = { state: 'refused', reason: 'title request failed' };
      }
    }
    out[r.wid] = o;
  }
  return out;
}

/** One line saying what applying a row did. */
export function outcomeText(o: Outcome | undefined): string {
  if (!o) return '';
  const parts: string[] = [];
  if (o.name) parts.push(o.name.ok ? 'Renamed' : `Name failed: ${o.name.error}`);
  if (o.title) {
    parts.push(o.title.state === 'applied' ? 'Title set'
      : o.title.state === 'pending' ? `Title pending (${o.title.reason || 'waits for idle'})`
        : `Title failed: ${o.title.reason}`);
  }
  return parts.join(' · ') || 'Nothing to change';
}

/** Whether any half of an outcome failed. */
export const outcomeFailed = (o: Outcome | undefined) =>
  !!o && ((o.name && !o.name.ok) || o.title?.state === 'refused');
