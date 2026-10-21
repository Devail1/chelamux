// Pending approvals (CMX-103), the pure half: how a request reads, and the opt-in browser
// notification's switch. Moved from static/js/approvals.js by CMX-219 (CMX-212 P05).

/** One record of /api/overrides `requests` (chela/overrides.py), typed as loosely as it comes. */
export type Request = {
  id: string;
  kind?: string | null;
  label?: string | null;
  task_id?: string | null;
  version?: string | null;
  question?: string | null;
  head_sha?: string | null;
  judge_state?: string | null;
  base?: string | null;
  pr_url?: string | null;
  actor?: string | null;
  reason?: string | null;
  age_s?: number | null;
  expires_in_s?: number | null;
  /** Why the server already knows it cannot be approved (CI red, head moved, ...). */
  blocked?: string | null;
};

/** Settings > Notifications' "browser notification on a new approval request" (opt-in). */
export const NOTIFY_KEY = 'chela_approval_notify';

/** A duration in seconds as `<1m`, `5m`, `2h 5m`, `3d 1h`; '' when unknown. */
export function durStr(s: number | null | undefined): string {
  if (s == null) return '';
  const m = Math.max(0, Math.round(s / 60));
  if (m < 1) return '<1m';
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return h < 48 ? `${h}h ${m % 60}m` : `${Math.floor(h / 24)}d ${h % 24}h`;
}

/**
 * A request's PR url is agent-supplied: it becomes a link only when it is plainly http(s).
 * Whitespace and control characters are stripped first (browsers ignore a tab or newline
 * inside a scheme, so `java\tscript:` must not slip past a prefix test). Anything else is
 * shown as plain text, never as an href.
 */
export function safeHref(url: unknown): string | null {
  // eslint-disable-next-line no-control-regex
  const clean = String(url ?? '').trim().replace(/[\u0000-\u001f\u007f-\u009f]/g, '');
  return /^https?:\/\//i.test(clean) ? clean : null;
}

/** What the request asks, in one line, per record kind. */
export function whatText(r: Request): string {
  if (r.kind === 'release') return `Release ${r.version || r.label || '?'}`;
  if (r.kind && r.kind !== 'override') return r.question || r.kind;
  const sha = (r.head_sha || '?').slice(0, 12);
  const past = r.judge_state ? `past the judge’s ${r.judge_state}` : 'with no judge verdict';
  return `Override-merge ${r.label || r.task_id || '?'} ${past} on ${sha}${r.base ? ` → ${r.base}` : ''}`;
}

export function notifyEnabled(): boolean {
  try {
    return localStorage.getItem(NOTIFY_KEY) === '1';
  } catch {
    return false;
  }
}

type NotificationApi = { permission: string; requestPermission(): Promise<string> };

/**
 * Settings > Notifications. Turning it on asks the browser for permission; where the API is
 * missing or the answer is not "granted", it stays off, silently. Resolves to whether it is on.
 */
export async function setApprovalNotify(v: string): Promise<boolean> {
  const off = () => {
    try {
      localStorage.removeItem(NOTIFY_KEY);
    } catch {
      /* storage refused: it is off either way */
    }
    return false;
  };
  if (v !== 'on') return off();
  try {
    const N = (window as unknown as { Notification?: NotificationApi }).Notification;
    if (!N) return off();
    const perm = N.permission === 'granted' ? 'granted' : await N.requestPermission();
    if (perm !== 'granted') return off();
    localStorage.setItem(NOTIFY_KEY, '1');
    return true;
  } catch {
    return off();
  }
}
