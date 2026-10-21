// CMX-95: what happens after an update or restart POSTs /api/update/apply (Settings > Update,
// the banner's Restart). A `started` reply is followed by a bounded poll of /api/update/status,
// so a click always ends in an outcome ("Restarted ...", the error, a reload, or "couldn't
// confirm"), never an endless spinner. Moved from static/js/updateflow.js by CMX-219 (CMX-212
// P06); legacy Settings imports it until P16.
import { api } from '../../shared/api/index.ts';

export const POLL_MS = 2000;
/** How long a page follows one click; past it, "couldn't confirm": unknown is never success. */
export const FOLLOW_TIMEOUT_MS = 180000;

/** The parts of /api/update/status this reads. */
export type UpdateStatus = {
  ok?: boolean;
  stale_ok?: boolean;
  stale_services?: unknown;
  behind?: number;
  boot?: string;
  applying?: boolean;
  last_result?: { run?: unknown; ok?: boolean; step?: string; error?: string; restarted?: string[]; behind_before?: number } | null;
};

export type StaleOffer = { names: string[]; key: string; text: string; label: string };

/**
 * What the banner offers for an /api/update/status payload, or null for nothing. Only an
 * affirmative, readable "these services run old code" offers. A missing payload, `ok:false`,
 * or a freshness check that could not answer (`stale_ok` not true) is UNKNOWN: it offers
 * nothing, and it is not "nothing stale" either.
 */
export function staleOffer(st: UpdateStatus | null | undefined): StaleOffer | null {
  if (!st || st.ok !== true || st.stale_ok !== true) return null;
  const names = Array.isArray(st.stale_services) ? (st.stale_services as string[]) : [];
  if (!names.length) return null;
  const one = names.length === 1;
  return {
    names,
    key: names.join(','),
    text: `${names.length} service${one ? '' : 's'} run${one ? 's' : ''} old code`,
    label: (st.behind ?? 0) > 0 ? 'Update & restart' : 'Restart',
  };
}

function doneText(r: NonNullable<UpdateStatus['last_result']>): string {
  const restarted = (r.restarted || []).slice().sort();
  const what = restarted.length ? `Restarted ${restarted.join(', ')}` : 'Done. No running service needed a restart';
  return (r.behind_before ?? 0) > 0 ? `Pulled ${r.behind_before} commit(s). ${what}` : what;
}

export type Step = { state: 'polling' | 'done' | 'failed' | 'timeout' | 'reload'; text: string };
export type FollowCtx = { run: unknown; boot: unknown; label: string; startedAt: number };

/**
 * One step of the post-click state machine: polling → done | failed | timeout, or reload when
 * THIS dashboard restarted (`boot` changed: nothing is left to report the outcome). `st` is the
 * latest /api/update/status (null if unreachable).
 */
export function updateOutcome(st: UpdateStatus | null, ctx: FollowCtx, now: number): Step {
  if (st && st.boot && ctx.boot && st.boot !== ctx.boot) {
    return { state: 'reload', text: 'The dashboard restarted, reloading…' };
  }
  const r = st?.last_result;
  if (st && st.applying === false && r && r.run === ctx.run) {
    if (r.ok === true) return { state: 'done', text: doneText(r) };
    return { state: 'failed', text: `Update failed at ${r.step}: ${r.error || 'no detail'}` };
  }
  if (now - ctx.startedAt >= FOLLOW_TIMEOUT_MS) {
    return { state: 'timeout', text: "Couldn't confirm the restart. Check `pm2 ls`." };
  }
  return { state: 'polling', text: `${ctx.label}…` };
}

export type FollowDeps = {
  fetchStatus?: () => Promise<unknown>;
  sleep?: (ms: number) => Promise<unknown>;
  now?: () => number;
  reload?: () => void;
};

/** Follow a `started` /api/update/apply response to its outcome, calling onStep on every
 * poll. Resolves with the final step. Every dependency is injectable for the tests. */
export async function followUpdate(
  resp: { detail?: string; run?: unknown; boot?: unknown },
  onStep: (s: Step) => void,
  {
    fetchStatus = () => api('/api/update/status'),
    sleep = (ms) => new Promise((r) => setTimeout(r, ms)),
    now = () => Date.now(),
    reload = () => window.location.reload(),
  }: FollowDeps = {},
): Promise<Step> {
  const what = resp.detail || 'restarting';
  const ctx: FollowCtx = { run: resp.run, boot: resp.boot, startedAt: now(), label: what.charAt(0).toUpperCase() + what.slice(1) };
  for (;;) {
    await sleep(POLL_MS);
    let st: UpdateStatus | null = null;
    try {
      st = (await fetchStatus()) as UpdateStatus | null;
    } catch {
      st = null; // down mid-restart
    }
    const step = updateOutcome(st, ctx, now());
    onStep(step);
    if (step.state === 'reload') {
      reload();
      return step;
    }
    if (step.state !== 'polling') return step;
  }
}
