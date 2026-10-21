// The approvals list every copy of the pill and the sheet read: one /api/overrides read per
// tick (the refresh loop) and per SSE `overrides` frame.
import { BASE_PATH, api } from '../../../shared/api/index.ts';
import { onEvent } from '../../../shared/events/index.ts';
import { registerLane } from '../../../shared/scheduler/index.ts';
import { createStore } from '../../../shared/store/index.ts';
import { notifyEnabled, whatText, type Request } from '../model.ts';

export type Approvals = {
  rows: Request[];
  /** A listing has succeeded at least once on this page. */
  loaded: boolean;
  /** The last refresh failed: `rows` is the last KNOWN list, not "nothing". */
  stale: boolean;
  /** The server itself reported an error (not just a dead fetch). */
  staleLoud: boolean;
};

export const approvals = createStore<Approvals>({ rows: [], loaded: false, stale: false, staleLoud: false });

/** ids already known: null until the first listing primes it, so a reload re-announces nothing. */
let seen: Set<string> | null = null;

type NotificationCtor = { permission: string; new (title: string, opts: { body: string; tag: string }): unknown };

// A browser notification for each request not seen before. Every failure (no API, permission
// not granted, a throwing constructor) is silent.
function notifyNew(rows: Request[]) {
  const fresh = seen === null ? [] : rows.filter((r) => !seen?.has(r.id));
  seen = new Set(rows.map((r) => r.id));
  if (!fresh.length || !notifyEnabled()) return;
  const N = (window as unknown as { Notification?: NotificationCtor }).Notification;
  if (!N || N.permission !== 'granted') return;
  for (const r of fresh) {
    try {
      new N('chela: approval waiting', { body: whatText(r), tag: `chela-approval-${r.id}` });
    } catch {
      /* unsupported in this context (e.g. needs a service worker) */
    }
  }
}

export async function tickApprovals(): Promise<void> {
  let data: { requests?: unknown; error?: unknown } | null;
  try {
    data = (await api('/api/overrides')) as typeof data;
  } catch {
    data = null;
  }
  const prev = approvals.get();
  if (!data || data.error || !Array.isArray(data.requests)) {
    // A failed or error reply keeps the last KNOWN rows and says so: never "nothing waiting".
    approvals.set({ ...prev, stale: true, staleLoud: !!data?.error });
    return;
  }
  const rows = data.requests as Request[];
  notifyNew(rows);
  approvals.set({ rows, loaded: true, stale: false, staleLoud: false });
}

/** POST a decision: the SAME decide path as the /override/<id> confirm page, which re-checks
 * head / CI / mergeable / expiry live. An approve carries `confirm: true` (the server refuses
 * one without it). Resolves to the reason when it was refused. */
export async function decide(id: string, action: 'approve' | 'deny'): Promise<string | null> {
  try {
    const res = await fetch(`${BASE_PATH}/api/override/${encodeURIComponent(id)}/${action}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(action === 'approve' ? { confirm: true } : {}),
    });
    const resp = (await res.json()) as { ok?: boolean; detail?: string; error?: string; state?: string } | null;
    if (resp?.ok) return null;
    return resp?.detail || resp?.error || resp?.state || `Could not ${action} the request`;
  } catch {
    return `Could not ${action} the request`;
  }
}

/** Test seam: a fresh page (nothing listed, nothing seen). */
export function resetApprovals() {
  seen = null;
  approvals.set({ rows: [], loaded: false, stale: false, staleLoud: false });
}

// CMX-103: a pill on every view; the SSE `overrides` frame (the pending requests changed)
// refetches the list at once.
registerLane('approvals', () => tickApprovals());
onEvent('overrides', () => {
  void tickApprovals();
});
