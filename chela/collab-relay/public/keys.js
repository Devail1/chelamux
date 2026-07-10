// Pure key logic for the joiner's mobile keys-line — special-key escape sequences
// and the swipe→wheel mapping. NO DOM, NO crypto: kept as a tiny standalone module
// so the exact byte sequences and the scroll sign/threshold are unit-tested under
// node (tests/keys.test.mjs) and can't silently drift. The SPA imports these; the
// sequences ride the same T_INPUT channel as typed input.

// Special-key → terminal escape sequence (xterm/VT forms). Enter = CR, Tab = HT,
// arrows/nav = the CSI sequences xterm emits for those keys.
export const KEY_SEQ = {
  Escape: '\x1b', Tab: '\t', BTab: '\x1b[Z', Enter: '\r',
  Up: '\x1b[A', Down: '\x1b[B', Right: '\x1b[C', Left: '\x1b[D',
  Home: '\x1b[H', End: '\x1b[F', PageUp: '\x1b[5~', PageDown: '\x1b[6~',
};

// A control char: Ctrl-A..Z → 0x01..0x1a (charcode & 0x1f). Used for ^C and the
// sticky-Ctrl letter layer. A missing/empty letter defaults to 'a' (harmless).
export const ctrlChar = (letter) =>
  String.fromCharCode((letter || 'a').toLowerCase().charCodeAt(0) & 0x1f);

// Resolve a keybar button's key name to its byte sequence. 'C-x' → control char;
// otherwise the KEY_SEQ table (null for an unknown key, so the caller sends nothing).
export function keySeq(name) {
  if (!name) return null;
  if (name.slice(0, 2) === 'C-') return ctrlChar(name.slice(2));
  return KEY_SEQ[name] != null ? KEY_SEQ[name] : null;
}

// Swipe → wheel deltaY. Natural touch scroll: content tracks the finger, so a
// downward drag (dy > 0) scrolls toward older content (wheel up = negative delta).
// Below the threshold returns 0 (de-jitters sub-pixel moves → no wheel).
export const SWIPE_MIN_PX = 1;
export function swipeWheelDelta(dy) {
  return Math.abs(dy) < SWIPE_MIN_PX ? 0 : -dy;
}
