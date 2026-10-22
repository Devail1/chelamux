// A fresh pane's /term/<wid>/ 404s until agent-terminals.sh assigns it a ttyd port (~12 s), so
// it draws a spinner that polls /api/term/ready (capped), then swaps its iframe in place: live
// siblings never reload. A pane already in the port map draws its iframe at once (resolveReady).
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

/** A failed probe is not ready, never ready. */
const checkReady = (wid: string): Promise<boolean> =>
  api('/api/term/ready?agent=' + encodeURIComponent(wid)).then((r) => Boolean((r as { ready?: unknown } | null)?.ready), () => false);

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
  clearInterval(pollers.get(wid));
  pollers.delete(wid);
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
    const last = ++tries >= READY_MAX_TRIES;
    void checkReady(wid).then((ok) => {
      if (pollers.get(wid) !== h) return; // stopped while the probe was in flight
      if (ok) markReady(wid);
      else if (last) { stopPoll(wid); put(wid, 'retry'); }
    });
  }, READY_POLL_MS);
  pollers.set(wid, h);
  return () => { if (pollers.get(wid) === h) stopPoll(wid); };
}

/** The retry link: back to the spinner, whose poll starts again. */
export const retryReady = (wid: string): void => put(wid, 'pending');

/** The pane is gone: a re-spawn under the same wid must re-probe its port. */
export function forgetReady(wid: string): void {
  stopPoll(wid);
  put(wid, null);
}
