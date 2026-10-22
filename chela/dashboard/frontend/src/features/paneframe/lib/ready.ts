// A freshly spawned pane's /term/<wid>/ 404s ("Unknown terminal") until agent-terminals.sh
// assigns it a ttyd port (~12 s). So a pane whose terminal is not ready yet draws a spinner
// placeholder and polls GET /api/term/ready every READY_POLL_MS (capped), then swaps in the
// iframe in place: no stage rebuild, so live sibling iframes never reload. A pane already in
// the port map draws its iframe at once (resolveReady, before the tiles are built).
import { api } from '../../../shared/api/index.ts';
import { createStore } from '../../../shared/bridge/index.ts';

/** 'pending': spinner + poll; 'retry': the poll gave up, a click restarts it. */
export type Readiness = 'ready' | 'pending' | 'retry';

/** wid → its readiness. A wid with no entry has not been probed and draws as pending. */
export const readiness = createStore<ReadonlyMap<string, Readiness>>(new Map());

export const READY_POLL_MS = 1500;
export const READY_MAX_TRIES = 20; // ~30 s, then offer a manual retry

const pollers = new Map<string, ReturnType<typeof setInterval>>();

function put(wid: string, r: Readiness | null): void {
  const cur = readiness.get();
  if ((cur.get(wid) ?? null) === r) return;
  const next = new Map(cur);
  if (r) next.set(wid, r);
  else next.delete(wid);
  readiness.set(next);
}

async function checkReady(wid: string): Promise<boolean> {
  try {
    const r = (await api('/api/term/ready?agent=' + encodeURIComponent(wid))) as { ready?: unknown } | null;
    return Boolean(r && r.ready);
  } catch {
    return false;
  }
}

export const isReady = (wid: string): boolean => readiness.get().get(wid) === 'ready';

/** Probe every wid not yet known ready, so the tiles about to be built draw no placeholder flash. */
export async function resolveReady(wids: readonly string[]): Promise<void> {
  await Promise.all(
    wids.map(async (w) => {
      if (!isReady(w) && (await checkReady(w))) put(w, 'ready');
    }),
  );
}

function stopPoll(wid: string): void {
  const h = pollers.get(wid);
  if (h !== undefined) {
    clearInterval(h);
    pollers.delete(wid);
  }
}

/** The terminal is up (its poll, or the `term-ready` push): its frame swaps in. */
export function markReady(wid: string): void {
  stopPoll(wid);
  put(wid, 'ready');
}

/** Poll `wid` until ready (or the cap); returns the stop. A drawn placeholder runs one. */
export function startReadyPoll(wid: string): () => void {
  stopPoll(wid);
  let tries = 0;
  const h = setInterval(() => {
    tries += 1;
    void checkReady(wid).then((ok) => {
      if (pollers.get(wid) !== h) return; // stopped while the probe was in flight
      if (ok) markReady(wid);
      else if (tries >= READY_MAX_TRIES) {
        stopPoll(wid);
        put(wid, 'retry');
      }
    });
  }, READY_POLL_MS);
  pollers.set(wid, h);
  return () => {
    if (pollers.get(wid) === h) stopPoll(wid);
  };
}

/** The retry link: back to the spinner, whose poll starts again. */
export function retryReady(wid: string): void {
  put(wid, 'pending');
}

/** The pane is gone: a re-spawn under the same wid must re-probe its port. */
export function forgetReady(wid: string): void {
  stopPoll(wid);
  put(wid, null);
}
