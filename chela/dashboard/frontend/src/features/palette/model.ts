// CMX-221 (CMX-212 P19): the ⌘K palette's pure half, ported from nav.js: the fuzzy score, and
// how a query orders the rows. No DOM.
import type { IconName } from '../../shared/icons/index.ts';

export type PaletteItem = {
  title: string;
  sub: string;
  run: () => void;
  /** A session's status dot (working / waiting / idle); otherwise the icon. */
  dot?: string;
  icon?: IconName;
  /** The window a row is about, scored on its own (CMX-417). */
  wid?: string;
};

/** Subsequence fuzzy score with a word-boundary bonus; -1 = no match. */
export function fuzzyScore(q: string, s: string): number {
  q = q.toLowerCase();
  s = s.toLowerCase();
  if (!q) return 0;
  let si = 0, score = 0, run = 0, first = -1;
  for (const c of q) {
    let found = -1;
    for (; si < s.length; si++) {
      if (s[si] === c) {
        found = si;
        break;
      }
    }
    if (found < 0) return -1;
    if (first < 0) first = found;
    run = found === 0 || s[found - 1] === ' ' ? run + 2 : 1;
    score += run;
    si = found + 1;
  }
  return score - first * 0.1;
}

/**
 * The rows for a typed query, best first. A row's window id is scored on its own too: inside
 * the full "title sub" haystack, a long name's first-match penalty would push an "@3" below
 * zero. `exact` (the window the query names exactly, CMX-417) is pinned first, once.
 */
export function rankItems(q: string, items: PaletteItem[], exact: PaletteItem | null): PaletteItem[] {
  const ranked = items
    .filter((it) => !(exact && it.wid === exact.wid))
    .map((it) => ({ it, sc: Math.max(fuzzyScore(q, `${it.title} ${it.sub}`), it.wid ? fuzzyScore(q, it.wid) : -1) }))
    .filter((x) => x.sc >= 0)
    .sort((a, b) => b.sc - a.sc)
    .map((x) => x.it);
  return exact ? [exact, ...ranked] : ranked;
}

/** Status colour → the word a row says and the dot's state class (the sidebar's). */
export const STATUS_WORD: Record<string, string> = { green: 'working', yellow: 'waiting', grey: 'idle' };
export const DOT_CLASS: Record<string, string> = { green: 'working', yellow: 'waiting', grey: 'idle' };
/** CMX-116: on an empty query the open panes float first, the ones wanting you ahead. */
export const ATTENTION_RANK: Record<string, number> = { yellow: 0, green: 1, grey: 2 };

/** Ctrl/⌘+K (the palette) and Ctrl/⌘+, (Settings, CMX-385; not with Shift or Alt). */
export const isPaletteKey = (e: KeyboardEvent) => (e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K');
export const isSettingsKey = (e: KeyboardEvent) => (e.metaKey || e.ctrlKey) && !e.shiftKey && !e.altKey && e.key === ',';
