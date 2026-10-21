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
