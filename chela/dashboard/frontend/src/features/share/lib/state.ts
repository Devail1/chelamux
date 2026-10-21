// CMX-240 (CMX-212 P25): which windows this page believes are shared, with each share's ACCESS
// mode and the UNSANDBOXED override's expiry. Display only: the host enforces every gate. The
// pill, the sheet and the legacy pane header all read this one store. It is best effort
// (seeded from /api/agents and /api/term/shared); the sheet reconciles it against
// /api/term/shared whenever it opens, so the kill list is accurate when it matters.
import { createStore } from '../../../shared/store/index.ts';
import type { ShareMode } from './model.ts';

export type ShareEntry = { mode: ShareMode; expiresAt: number | null };
export type Shares = ReadonlyMap<string, ShareEntry>;

export const shares = createStore<Shares>(new Map());

/** A mode string from the server. Missing or unknown renders as View only, as it always has. */
export function asMode(m: unknown): ShareMode {
  return m === 'typing' || m === 'unsandboxed' ? m : 'view';
}

const sameEntry = (a: ShareEntry | undefined, b: ShareEntry | undefined) => a?.mode === b?.mode && a?.expiresAt === b?.expiresAt;

/** Publish a new map only when something changed: the agents poll seeds this every 4 s. */
function update(fn: (m: Map<string, ShareEntry>) => void) {
  const cur = shares.get();
  const next = new Map(cur);
  fn(next);
  if (next.size === cur.size && [...next].every(([w, e]) => sameEntry(e, cur.get(w)))) return;
  shares.set(next);
}

export const isShared = (wid: string) => shares.get().has(wid);
/** The share's mode, or null when `wid` is not shared. */
export const shareMode = (wid: string): ShareMode | null => shares.get().get(wid)?.mode ?? null;

export function noteShare(wid: string, mode: unknown, expiresAt?: number | null) {
  update((m) => m.set(wid, { mode: asMode(mode), expiresAt: expiresAt || null }));
}

/** Mark `wid` shared without knowing its mode (adopt-first): an entry already known is kept. */
export function markShared(wid: string) {
  update((m) => {
    if (!m.has(wid)) m.set(wid, { mode: 'view', expiresAt: null });
  });
}

export function dropShare(wid: string) {
  update((m) => m.delete(wid));
}

/** One /api/term/shared answer: wid -> {mode, expires_at, ...}. */
export type SharedMap = Record<string, { mode?: unknown; expires_at?: number | null } | null>;

/** Adopt /api/term/shared: `replace` makes it the whole set (it is the server's truth). */
export function applyShared(shared: SharedMap, replace: boolean) {
  update((m) => {
    if (replace) m.clear();
    for (const [w, v] of Object.entries(shared)) m.set(w, { mode: asMode(v?.mode), expiresAt: v?.expires_at || null });
  });
}

/** An /api/agents row, as far as sharing reads it. */
export type ShareAgent = { window_id?: string; shared?: unknown; share_mode?: unknown; share_epoch?: number | null };

/** Follow the agents poll: a row says whether its window is shared and in which mode. Its
 * expiry is not on the row, so a window still UNSANDBOXED keeps the one already known. */
export function seedFromAgents(agents: readonly ShareAgent[]) {
  update((m) => {
    for (const a of agents) {
      if (!a.window_id) continue;
      const mode = asMode(a.share_mode);
      if (a.shared) m.set(a.window_id, { mode, expiresAt: mode === 'unsandboxed' ? (m.get(a.window_id)?.expiresAt ?? null) : null });
      else m.delete(a.window_id);
    }
  });
}
