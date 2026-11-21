// CMX-241 (CMX-212 P26): what a pane header SAYS, as a straight function of its /api/agents
// row. No DOM, no fetch. From terminals.js's display labels and status dots.
import { stateTitle, tileState, type TileState, type WallAgent } from '../../../../static/js/wallmodel.ts';

/** The /api/agents fields a pane header reads. */
export type HeadAgent = WallAgent & {
  name?: string;
  /** CMX-62: renamed by a human (the server's flag, never guessed from the string). */
  manual_name?: boolean;
  /** CMX-146: Claude's auto-generated session title. */
  ai_title?: string | null;
  /** CMX-434: the title set with `/rename` (customTitle); it outranks ai_title, as in Claude Code. */
  custom_title?: string | null;
  /** CMX-434: a title queued for this session; it is typed in once the session is idle. */
  title_pending?: string | null;
  session_name?: string | null;
  needs_human?: boolean;
  /** CMX-418: 'web' on a sandboxed session launched with web access. */
  share_net?: string | null;
  /** CMX-241: the pane's own working directory, home as `~`; sent only while no Claude runs. */
  shell_cwd?: string | null;
  /** CMX-395: started before the latest Claude account switch (chela/claude_account.py). */
  account_switch?: unknown;
};

type MaybeAgent = HeadAgent | null | undefined;

/** Blocked on a human right now: util.js `wantsHuman`, the one predicate, read the same way. */
export function wantsHuman(a: MaybeAgent): boolean {
  return !!a && (a.needs_human === true || a.session_status === 'waiting');
}

/** Claude's session title: its `/rename` title (CMX-434), else its `ai_title`, else the live
 * session's name; '' before any exists. */
export function claudeTitle(a: MaybeAgent): string {
  return (a && (a.custom_title || a.ai_title || a.session_name)) || '';
}

/**
 * A window's display label, shared by the sidebar row, the palette and the dock (CMX-62): a
 * MANUAL name > Claude's session title > the window name. The window name is a stable key
 * that never follows the title. A window the poll has not listed reads as its id. CMX-459: the
 * pane HEADER no longer leads with it (headLabel: the name first, the title second); these
 * one-slot surfaces keep it, since only one of the two fits.
 */
export function displayLabel(a: MaybeAgent, wid: string): string {
  if (!a) return wid;
  if (a.manual_name) return a.name || wid;
  return claudeTitle(a) || a.name || 'claude window';
}

/**
 * The dim text beside the header's name: Claude's session title (its `/rename` title, else its
 * `ai_title`, else the live session's name), '' when there is none or it only repeats the name.
 * CMX-459 (Liav, 11-21): a FIXED order, the name first and the title second, whoever named the
 * window. Before, CMX-62 let the title lead an un-renamed window, so the two swapped places.
 */
export function paneSubtitle(a: MaybeAgent, wid: string): string {
  if (!a) return '';
  const t = claudeTitle(a);
  return t && t !== (a.name || wid) ? t : '';
}

/**
 * A plain shell: the server checked and no Claude process runs in the pane (`claude:no` in
 * `chela peek`). Only an explicit false counts: a row that does not say reads as a session.
 */
export function isShell(a: MaybeAgent): boolean {
  return !!a && a.claude_running === false;
}

export interface HeadLabel {
  title: string;
  subtitle: string;
  /** The subtitle is the shell's cwd (drawn after a dot), not a session title. */
  cwd: boolean;
}

/**
 * The header's title and subtitle. A plain shell reads `Shell` and its cwd until Claude starts
 * in it (Liav, 10-19): its tmux window name (`liavedunix-2`) made it look like an idle session.
 * The window itself is never renamed (its name feeds the Telegram topic and
 * `--remote-control`, chela/rc_rename.py). A name a human gave it still wins.
 */
export function headLabel(a: MaybeAgent, wid: string): HeadLabel {
  if (isShell(a)) return { title: a?.manual_name ? displayLabel(a, wid) : 'Shell', subtitle: a?.shell_cwd || '', cwd: true };
  // CMX-459: the WINDOW name first (bold), Claude's title second (dim), always.
  return { title: (a && a.name) || wid, subtitle: paneSubtitle(a, wid), cwd: false };
}

/** CMX-459: Claude runs in the pane (the server said so): only then can its title be set. A row
 * that does not say is not assumed to (the server would refuse it anyway). */
export function canRetitle(a: MaybeAgent): boolean {
  return !!a && a.claude_running === true;
}

/** CMX-459: what "Rename title" opens with: a title already queued, else the current one. */
export function titleDraft(a: MaybeAgent): string {
  return (a && (a.title_pending || claudeTitle(a))) || '';
}

/**
 * The state pill: tileState (wallmodel.ts), except that a shell reads `shell` where it would
 * read `idle`. Anything more specific (needs you, a battery, PR open) still shows.
 */
export function headState(a: MaybeAgent): TileState & { title: string } {
  const s = tileState(a, wantsHuman(a));
  if (s.cls === 'idle' && isShell(a)) return { word: 'shell', cls: 'shell', title: 'Shell: no Claude session runs in this pane' };
  return { ...s, title: stateTitle(a, s) };
}

/**
 * CMX-377: the status dot's class vocabulary (working/waiting/idle/done), shared with the
 * sidebar row and the dock chips (style.css `.term-status-dot.*`). tileState's own names are
 * mapped onto it; `unknown` reads as the idle shape. CMX-40: a running battery is work, one
 * that died without a verdict wants a human.
 */
export const DOT_FOR_STATE: Readonly<Record<string, string>> = {
  'needs-you': 'waiting', working: 'working', 'pr-open': 'done', unknown: 'idle', idle: 'idle',
  testing: 'working', died: 'waiting',
};

/** The dot class for a row: the SAME tileState the pill reads, so the two never disagree. */
export function dotState(a: MaybeAgent): string {
  return DOT_FOR_STATE[tileState(a, wantsHuman(a)).cls] || 'idle';
}
