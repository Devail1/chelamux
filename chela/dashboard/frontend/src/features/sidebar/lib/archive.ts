// The archive (CMX-75): SERVER state. chela/sidebar_archive.py, behind /api/sidebar/archive:
// `hidden` (row keys hidden only: a settled dispatched run) and `sessions` (CLOSED human
// sessions, each with its resume record). Until the first answer the archive is UNKNOWN
// (null, not empty), so nothing acts on an archive this page has not seen.
import { api } from '../../../shared/api/index.ts';
import type { SessionRecord } from '../model.ts';
import { isObj, lsJson, lsRemove, postJson } from './page.ts';

// Pre-CMX-75 the archive lived in this browser; what it still holds is imported ONCE.
const LEGACY_KEY = 'chela_sb_archived';

let arch: { hidden: string[] | null; sessions: SessionRecord[] | null; loaded: boolean } =
  { hidden: null, sessions: null, loaded: false };

export const archivedKeys = (): string[] => arch.hidden ?? [];
export const archivedSessions = (): SessionRecord[] => arch.sessions ?? [];

export function setArchive(st: unknown): void {
  if (!isObj(st)) return;
  arch = {
    hidden: Array.isArray(st.hidden) ? st.hidden.filter((k): k is string => typeof k === 'string') : [],
    sessions: Array.isArray(st.sessions)
      ? (st.sessions as SessionRecord[]).filter(r => isObj(r) && typeof r.key === 'string') : [],
    loaded: true,
  };
}

// Those keys only ever meant HIDE, and the server imports them as hidden: a page load never
// closes a window.
let migrating = false;
async function migrateArchive() {
  const old = lsJson(LEGACY_KEY);
  if (migrating || !Array.isArray(old) || !old.length) return;
  migrating = true;
  try {
    const res = await postJson('/api/sidebar/archive/migrate', { keys: old });
    if (isObj(res) && res.ok) {
      setArchive(res.state);
      lsRemove(LEGACY_KEY);
    }
  } catch { /* the next refresh retries */ }
  migrating = false;
}

export async function refreshArchive(): Promise<void> {
  try {
    const st = await api('/api/sidebar/archive');
    if (isObj(st) && Array.isArray(st.hidden)) setArchive(st);
  } catch { /* transient: keep the last answer */ }
  await migrateArchive();
}

// Hidden rows that woke up or whose window is gone leave the archive, so a woken row is not
// re-hidden when it settles and a recycled @N never inherits an old row's flag. Never before
// the first answer, and one request in flight at a time.
let pruning = false;
export function pruneHidden(stillHidden: string[]): void {
  if (!arch.loaded || pruning || !arch.hidden) return;
  const keep = new Set(stillHidden);
  const prune = arch.hidden.filter(k => !keep.has(k));
  if (!prune.length) return;
  pruning = true;
  arch = { ...arch, hidden: arch.hidden.filter(k => keep.has(k)) };
  postJson('/api/sidebar/unarchive', { keys: prune })
    .then(res => { if (isObj(res) && res.state) setArchive(res.state); })
    .catch(() => {})
    .finally(() => { pruning = false; });
}
