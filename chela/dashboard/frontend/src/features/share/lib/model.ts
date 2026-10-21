// CMX-239 (CMX-212 P24): the share dialog's rules, pure. The dialog only keeps the choice
// honest: the server re-checks the setting, the live sandbox verdict and the typed window
// name, and the host bridge re-checks every keystroke (CMX-403).

export type ShareMode = 'view' | 'typing' | 'unsandboxed';

/** GET /api/term/<wid>/share-options. A failed read is `{}`: view only, nothing more. */
export type ShareOptions = {
  share_typing?: boolean;
  sandboxed?: boolean;
  typing_allowed?: boolean;
  unsandboxed_offered?: boolean;
  window_name?: string;
  unsandboxed_minutes?: number;
  unsandboxed_choices?: number[];
  unsandboxed_long_minutes?: number;
};

export const SHARE_TYPING_OFF_REASON = 'Typing is disabled in Settings';
export const SHARE_NOT_SANDBOXED_REASON = 'Not a sandboxed session. Start one from New session → Sandboxed';

/** The UNSANDBOXED duration picker (CMX-419), shared with the Active shares upgrade: the
 * server's choices plus the configured default, and the length past which the window name
 * must be typed a SECOND time. */
export function unsafeDurations(o?: ShareOptions | null): { choices: number[]; longMins: number } {
  const base = o?.unsandboxed_choices?.length ? o.unsandboxed_choices : [30, 240, 1440, 10080, 20160];
  const dflt = o?.unsandboxed_minutes || 30;
  const choices = [...new Set([...base, dflt])].sort((a, b) => a - b);
  return { choices, longMins: o?.unsandboxed_long_minutes || 240 };
}

/** "30 min" · "4 h" · "1 day" · "14 days". */
export function durLabel(n: number): string {
  if (n % 1440 === 0) return `${n / 1440} ${n === 1440 ? 'day' : 'days'}`;
  return n % 60 === 0 ? `${n / 60} h` : `${n} min`;
}

/** What the dialog may offer for one window. */
export type ShareOffer = {
  /** "Allow typing": the setting is on AND the window verifies live as sandboxed. */
  typingOk: boolean;
  /** Why typing is off, or ''. */
  reason: string;
  /** The trusted-peer override: setting on, a NON-sandboxed window, the server offers it. */
  offerUnsafe: boolean;
  /** The window name the override needs typed. */
  name: string;
  /** The preselected duration. */
  mins: number;
  choices: number[];
  longMins: number;
};

export function shareOffer(o: ShareOptions): ShareOffer {
  const typingOk = !!o.typing_allowed;
  const reason = typingOk ? '' : o.share_typing ? SHARE_NOT_SANDBOXED_REASON : SHARE_TYPING_OFF_REASON;
  const offerUnsafe = !!(o.share_typing && !o.sandboxed && o.unsandboxed_offered && o.window_name);
  return { typingOk, reason, offerUnsafe, name: o.window_name || '', mins: o.unsandboxed_minutes || 30, ...unsafeDurations(o) };
}

export type ShareDraft = { mode: ShareMode; minutes: number; confirm: string; confirmLong: string };

/** A pick longer than `longMins` needs the window name typed twice. */
export const isLong = (offer: ShareOffer, minutes: number) => minutes > offer.longMins;

/**
 * The POST /share body for a draft, or null while the draft may not be minted: a mode the
 * window was not offered, or the override without the window name typed (twice past 4 h).
 */
export function mintBody(offer: ShareOffer, d: ShareDraft): Record<string, unknown> | null {
  if (d.mode === 'view') return { on: true, mode: 'view' };
  if (d.mode === 'typing') return offer.typingOk ? { on: true, mode: 'typing' } : null;
  if (!offer.offerUnsafe || d.confirm.trim() !== offer.name) return null;
  const long = isLong(offer, d.minutes);
  if (long && d.confirmLong.trim() !== offer.name) return null;
  return { on: true, mode: 'unsandboxed', minutes: d.minutes, confirm: offer.name, ...(long ? { confirm_long: offer.name } : {}) };
}

// --- a LIVE share (CMX-240, P25): the Active shares sheet, its pill, the pane's banner ------

/** Each mode in the dialog's own words (CMX-421): what the sheet says about THAT share. */
export const SHARE_MODE_UI: Record<ShareMode, { label: string; desc: string }> = {
  view: { label: 'View only', desc: 'Anyone with the link + code can watch. Nothing they type reaches this machine.' },
  typing: { label: 'Allow typing', desc: 'Anyone with the link + code can watch and type into this sandboxed session.' },
  unsandboxed: { label: 'Full access: UNSANDBOXED', desc: 'UNSANDBOXED: the first guest to type gets a real shell on this machine.' },
};

/** "12d 4h left" · "3h 5m left" · "25m left" · "<1m left", or '' with no expiry (epoch s). */
export function shareTimeLeft(expiresAt: number | null | undefined, nowMs: number): string {
  if (!expiresAt) return '';
  const mins = Math.floor((expiresAt * 1000 - nowMs) / 60000);
  if (mins < 1) return '<1m left';
  const d = Math.floor(mins / 1440);
  const h = Math.floor((mins % 1440) / 60);
  const m = mins % 60;
  if (d) return `${d}d ${h}h left`;
  if (h) return `${h}h ${m}m left`;
  return `${m}m left`;
}

/** The red banner's words, on the pill and on the pane: the override can outlast the day. */
export function unsafeBannerText(expiresAt: number | null | undefined, nowMs: number): string {
  const left = shareTimeLeft(expiresAt, nowMs);
  return `UNSANDBOXED: guest can type${left ? ` · ${left}` : ''}`;
}

/** "at 14:05" today, else "Oct 15, 14:05". */
export function fmtShareEnd(expiresAt: number, nowMs: number): string {
  const d = new Date(expiresAt * 1000);
  const t = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return d.toDateString() === new Date(nowMs).toDateString() ? `at ${t}` : `${d.toLocaleDateString([], { month: 'short', day: 'numeric' })}, ${t}`;
}

/** What the active-shares pill says. The most permissive live mode wins it (CMX-403). */
export type PillView = { count: number; mode: ShareMode; text: string; label: string };

export function pillView(entries: readonly { mode: ShareMode; expiresAt: number | null }[], nowMs: number): PillView {
  const n = entries.length;
  // With several overrides, the one that lasts longest (no expiry lasts longest) is the exposure.
  const unsafe = entries
    .filter((e) => e.mode === 'unsandboxed')
    .reduce<{ expiresAt: number | null } | undefined>((a, e) => (!a ? e : a.expiresAt === null || (e.expiresAt !== null && a.expiresAt >= e.expiresAt) ? a : e), undefined);
  const mode: ShareMode = unsafe ? 'unsandboxed' : entries.some((e) => e.mode === 'typing') ? 'typing' : 'view';
  const label =
    (unsafe ? 'UNSANDBOXED: a guest can type into a real shell. ' : '') +
    `${n} active share${n === 1 ? '' : 's'}${mode === 'view' ? ' (view only)' : ' (typing allowed)'}, tap to manage or stop`;
  return { count: n, mode, text: unsafe ? unsafeBannerText(unsafe.expiresAt, nowMs) : `${n} sharing`, label };
}

/**
 * The POST /share-mode body that upgrades a live share to UNSANDBOXED, or null while it may not
 * be sent: the same typed-name rule (twice past 4 h) as minting one, without `on`.
 */
export function upgradeBody(offer: ShareOffer, d: Omit<ShareDraft, 'mode'>): Record<string, unknown> | null {
  const body = mintBody(offer, { ...d, mode: 'unsandboxed' });
  if (!body) return null;
  delete body.on;
  return body;
}
