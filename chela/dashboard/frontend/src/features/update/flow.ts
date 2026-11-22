// CMX-95: what happens after an update or restart POSTs /api/update/apply (Settings > Update,
// the banner's Restart). A `started` reply is followed by a bounded poll of /api/update/status,
// so a click always ends in an outcome ("Restarted ...", the error, a reload, or "couldn't
// confirm"), never an endless spinner. Moved from static/js/updateflow.js by CMX-219 (CMX-212
// P06); legacy Settings imports it until P16.
import { BASE_PATH, api, decideHeaders } from '../../shared/api/index.ts';

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

// --- CMX-467: sessions on an older Claude Code -------------------------------------------------

/** The parts of /api/sessions/claude-version this reads. */
export type ClaudeVersion = { ok?: boolean; installed?: string | null; stale?: unknown };
export type ClaudeOffer = { wids: string[]; key: string; text: string; label: string };
export const CLAUDE_RESTART_LABEL = 'Restart onto the new Claude Code';

const staleWid = (r: unknown): string =>
  r && typeof r === 'object' && typeof (r as { wid?: unknown }).wid === 'string' ? (r as { wid: string }).wid : '';

/** The banner's "Restart onto the new Claude Code" offer, or null. Only a readable payload that
 * names the installed version AND at least one session on an older one offers: unknown is
 * never an offer. */
export function claudeOffer(cv: ClaudeVersion | null | undefined): ClaudeOffer | null {
  if (!cv || cv.ok !== true || !cv.installed) return null;
  const wids = (Array.isArray(cv.stale) ? cv.stale : []).map(staleWid).filter(Boolean);
  if (!wids.length) return null;
  const one = wids.length === 1;
  return {
    wids,
    key: `${cv.installed}:${wids.join(',')}`,
    text: `${wids.length} session${one ? '' : 's'} run${one ? 's' : ''} an older Claude Code than ${cv.installed}`,
    label: CLAUDE_RESTART_LABEL,
  };
}

export type RestartReply = { ok?: boolean; started?: boolean; error?: string; detail?: string };

/** POST /api/sessions/restart: one session (`{wid}`) or every interactive one (`{all: true}`).
 * A failed request is a refusal, never a start. */
export async function postRestart(body: { wid: string } | { all: true }): Promise<RestartReply> {
  try {
    const res = await fetch(`${BASE_PATH}/api/sessions/restart`, { method: 'POST', headers: decideHeaders(), body: JSON.stringify(body) });
    return (await res.json()) as RestartReply;
  } catch {
    return { ok: false, error: 'request failed, the dashboard may be restarting' };
  }
}

/** What to say about a restart reply: started (the result lands in the inbox) or why not. */
export function restartNote(r: RestartReply | null): { ok: boolean; text: string } {
  if (r && r.ok === true && r.started === true) return { ok: true, text: r.detail || 'Restarting; the result lands in the inbox' };
  return { ok: false, text: r?.error || 'Restart refused.' };
}
