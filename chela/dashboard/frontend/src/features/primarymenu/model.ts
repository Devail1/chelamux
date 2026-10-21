// The gear menu's readouts (CMX-212 P20), pure: the account-wide rate-limit pills and the
// summary. Moved from static/js/agents.js and header.js by CMX-219.
import type { ContextRow } from '../../shared/store/context.ts';

/** A reading older than this is drawn dimmed: no agent has refreshed its statusline lately. */
export const RL_STALE_MS = 10 * 60 * 1000;

/** `Resets in 1 hr 28 min` / `in 2 days 3 hr`, from epoch seconds; null with nothing sensible
 * to show (never "Resets in NaN"). */
export function rlResetTooltip(resetsAt: number | null | undefined, now = Date.now()): string | null {
  if (resetsAt == null || !Number.isFinite(resetsAt)) return null;
  const msLeft = resetsAt * 1000 - now;
  if (msLeft <= 0) return 'Resets now';
  const totalMin = Math.round(msLeft / 60000);
  if (totalMin < 1) return 'Resets soon';
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  let span: string;
  if (h >= 24) {
    const d = Math.floor(h / 24);
    const rh = h % 24;
    const days = `${d} day${d > 1 ? 's' : ''}`;
    span = rh >= 1 ? `${days} ${rh} hr` : days;
  } else if (h >= 1) {
    span = `${h} hr ${m} min`;
  } else {
    span = `${m} min`;
  }
  return `Resets in ${span}`;
}

/** How long ago the winning sample was written; null when its timestamp is unusable. */
export function readingAge(ts: string | null | undefined, now = Date.now()): string | null {
  if (!ts) return null;
  const ms = now - Date.parse(ts);
  if (!Number.isFinite(ms) || ms < 0) return null;
  const min = Math.round(ms / 60000);
  if (min < 1) return 'updated just now';
  if (min < 60) return `updated ${min} min ago`;
  return `updated ${Math.floor(min / 60)} hr ${min % 60} min ago`;
}

export type RlReading = { pct: number; tone: '' | 'yellow' | 'red'; stale: boolean; tip: string };

/** A rate limit is account-wide: the FRESHEST sample across agents, shown once. null = none. */
export function rlReading(rows: ContextRow[], pctKey: 'rate_limit_pct' | 'weekly_rl_pct', resetKey: 'rate_limit_resets_at' | 'weekly_rl_resets_at', now = Date.now()): RlReading | null {
  let pct: number | null = null;
  let ts = '';
  let resetsAt: number | null = null;
  for (const a of rows) {
    const p = a[pctKey];
    if (p != null && (a.ts || '') >= ts) {
      pct = p;
      ts = a.ts || '';
      resetsAt = a[resetKey] ?? null;
    }
  }
  if (pct == null) return null;
  return {
    pct: Math.round(pct),
    tone: pct > 80 ? 'red' : pct > 60 ? 'yellow' : '',
    stale: !!ts && now - Date.parse(ts) > RL_STALE_MS,
    tip: [rlResetTooltip(resetsAt, now), readingAge(ts, now)].filter(Boolean).join(' · '),
  };
}

/** /api/summary. */
export type Summary = {
  agents_total?: number;
  agents_online?: number;
  windows_total?: number;
  schedules_active?: number;
  schedules_total?: number;
  next_runs?: Record<string, string>;
};

/** The Sessions header's count: online/total agents, toned, or the window count with none. */
export function agentsCount(d: Summary): { text: string; tone: string } {
  const total = d.agents_total ?? 0;
  const online = d.agents_online ?? 0;
  if (total > 0) return { text: `${online}/${total}`, tone: online === total ? 'green' : online > 0 ? 'yellow' : 'red' };
  return { text: `${d.windows_total ?? 0} windows`, tone: '' };
}

/** The soonest scheduled run, `[name, iso]`, or null. */
export function soonest(nextRuns: Record<string, string> | undefined): [string, string] | null {
  return Object.entries(nextRuns || {}).sort((a, b) => a[1].localeCompare(b[1]))[0] ?? null;
}
