// How each orchestrator state reads: a glyph and a word, so colour (the class) is never the
// only signal (Liav is red-weak).
export const CHIP_META: Record<string, { glyph: string; word: string; cls: string }> = {
  ok: { glyph: '●', word: 'live', cls: 'ok' },
  unregistered: { glyph: '○', word: 'nobody (logged here)', cls: 'none' },
  dangling: { glyph: '×', word: 'dangling', cls: 'bad' },
  gone: { glyph: '×', word: 'gone', cls: 'bad' },
  unstamped: { glyph: '◐', word: 'unverified', cls: 'warn' },
};

/** An unknown state reads as nobody: never a guess at health. */
export function chipMeta(state: string): { glyph: string; word: string; cls: string } {
  return (Object.hasOwn(CHIP_META, state) ? CHIP_META[state] : CHIP_META.unregistered) as { glyph: string; word: string; cls: string };
}

// A dangling/gone address has no self-heal path left (chela/inbox.py's resolve_heal only
// re-resolves a RENUMBERED window; after a reboot the old session is simply gone). The way
// out used to be a HUMAN typing `chela watch` in a shell; the chip offers that same fix.
export const RECOVERABLE_STATES = new Set(['dangling', 'gone']);
