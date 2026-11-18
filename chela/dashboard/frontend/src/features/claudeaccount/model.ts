// CMX-395: what the header's Claude account line SAYS, as a straight function of the
// /api/claude-account answer (chela/claude_account.py), and the per-session switch badge.
// Unknown never reads as OK: an answer that is missing, malformed or "unknown" says so.

export type AccountTone = 'ok' | 'warn' | 'out' | 'unknown' | 'pending';

export interface AccountView {
  tone: AccountTone;
  /** The line itself: the account, or the state when there is no account to show. */
  text: string;
  /** The expiry warning beside the account ('' when none). */
  warn: string;
  /** The hover: plan, login method, expiry. */
  title: string;
}

type Obj = Record<string, unknown>;

const str = (v: unknown): string => (typeof v === 'string' ? v.trim() : '');

/** `3d`, `5h`: how long is left, for a warning. Under an hour reads `1h`. */
export function left(seconds: number): string {
  if (seconds >= 86400) return `${Math.floor(seconds / 86400)}d`;
  return `${Math.max(1, Math.ceil(seconds / 3600))}h`;
}

/** `2027-01-15 12:00 UTC`, or '' for a stamp that does not parse. */
export function utc(iso: unknown): string {
  const t = typeof iso === 'string' ? Date.parse(iso) : NaN;
  return Number.isFinite(t) ? `${new Date(t).toISOString().slice(0, 16).replace('T', ' ')} UTC` : '';
}

/** `name@… · Org` when there is an org (the domain says nothing the org does not), else the email. */
export function accountLabel(email: string, org: string): string {
  if (!org) return email;
  const at = email.indexOf('@');
  return `${at > 0 ? `${email.slice(0, at)}@…` : email} · ${org}`;
}

function unknown(reason: string): AccountView {
  return {
    tone: 'unknown', text: 'account: unknown', warn: '',
    title: `Claude account unknown: ${reason}. Run claude auth status to check.`,
  };
}

export function accountView(answer: unknown): AccountView {
  if (answer === undefined) {
    return { tone: 'pending', text: 'account: checking', warn: '', title: 'Reading claude auth status' };
  }
  if (!answer || typeof answer !== 'object' || Array.isArray(answer)) return unknown('no answer');
  const a = answer as Obj;
  if (a.state === 'unknown') return unknown(str(a.reason) || 'no reason given');
  if (a.state === 'logged_out') {
    return {
      tone: 'out', text: 'Claude not logged in', warn: '',
      title: 'claude auth status: not logged in. Agents fail until you run claude auth login.',
    };
  }
  if (a.state !== 'ok' && a.state !== 'expiring') return unknown('an answer this page does not know');
  const email = str(a.email);
  if (!email) return unknown('no account email');
  const org = str(a.org_name);
  const expires = utc(a.expires_at);
  const secs = typeof a.expires_in_s === 'number' && Number.isFinite(a.expires_in_s) ? a.expires_in_s : null;
  let warn = '';
  if (a.state === 'expiring') {
    if (secs === null) warn = 'login expiring';
    else warn = secs <= 0 ? 'login expired' : `login expires in ${left(secs)}`;
  }
  const lines = [
    `Claude account: ${email}`,
    org && `Org: ${org}`,
    `Plan: ${str(a.plan) || 'unknown'}`,
    `Login method: ${str(a.auth_method) || 'unknown'}`,
    expires
      ? `Login expires: ${expires}${secs !== null && secs > 0 ? ` (in ${left(secs)})` : secs !== null ? ' (expired)' : ''}`
      : 'Login expiry: unknown',
    warn && 'Run claude auth login before it lapses: a lapsed login stops every agent.',
  ];
  const sw = switchOf(a.change);
  if (sw) lines.push(`Account switched from ${sw.from} to ${sw.to} at ${utc(sw.at) || sw.at}`);
  return { tone: warn ? 'warn' : 'ok', text: accountLabel(email, org), warn, title: lines.filter(Boolean).join('\n') };
}

/** One /api/agents row's `account_switch` (chela/claude_account.py switch_badge). */
export interface AccountSwitch {
  from: string;
  to: string;
  at: string;
  started_unknown: boolean;
}

function switchOf(v: unknown): AccountSwitch | null {
  if (!v || typeof v !== 'object') return null;
  const o = v as Obj;
  const from = str(o.from);
  const to = str(o.to);
  if (!from || !to) return null;
  return { from, to, at: str(o.at), started_unknown: o.started_unknown === true };
}

/** The muted badge on a session that started before the latest account switch: it MAY still
 * use the old account. Never "uses": a running session's account is not knowable. */
export function switchBadge(v: unknown): { text: string; title: string } | null {
  const sw = switchOf(v);
  if (!sw) return null;
  const when = utc(sw.at) || sw.at || 'an unknown time';
  const why = sw.started_unknown
    ? `its start time is unknown, and the account switched to ${sw.to} at ${when}`
    : `it started before the switch to ${sw.to} at ${when}`;
  return { text: sw.from, title: `May still use ${sw.from}: ${why}. Restart it to be sure.` };
}
