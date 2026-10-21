// CMX-221 (CMX-212 P19): the page's global keys. Ctrl/⌘+K toggles the palette, Ctrl/⌘+, toggles
// Settings (CMX-385, like VS Code). A focused pane's keydown never reaches here: app.py's
// _TERM_PALETTE_KEY_SHIM catches both inside the iframe and calls the parent's
// window.chela.openPalette / toggleSettings (src/iframeBridge.ts). While the palette is open the
// arrows and Enter drive it whatever has focus (the sidebar's jump box keeps it, CMX-377).
// Escape is each dialog's own: the top one closes, so one Esc closes one layer.
import { toggleSettings } from '../../settings/index.ts';
import { isPaletteKey, isSettingsKey } from '../model.ts';
import { movePalette, palette, runPalette, sidebarJumpInput, togglePalette } from './state.ts';

function onKey(e: KeyboardEvent): void {
  if (isPaletteKey(e)) {
    e.preventDefault();
    togglePalette();
    return;
  }
  if (isSettingsKey(e)) {
    e.preventDefault();
    toggleSettings();
    return;
  }
  if (!palette.get().open) return;
  const move = { ArrowDown: 1, ArrowUp: -1 }[e.key];
  if (move) {
    e.preventDefault();
    movePalette(move);
  } else if (e.key === 'Enter') {
    e.preventDefault();
    runPalette();
  }
}

/**
 * Listen for the keys on `win`'s document, and give index.html's sidebar "Jump to session"
 * box (legacy markup: its inline handlers call `chela.sidebarJumpInput(this)`) its handler.
 * Once per page. Returns the uninstall.
 */
const installed = new WeakSet<Document>();
export function installPalette(win: Window = window): () => void {
  const ns = ((win as unknown as { chela?: Record<string, unknown> }).chela ??= {});
  ns.sidebarJumpInput = sidebarJumpInput;
  const doc = win.document;
  const off = () => {
    doc.removeEventListener('keydown', onKey);
    installed.delete(doc);
  };
  // once per page: a second listener would toggle the palette twice per Ctrl+K
  if (installed.has(doc)) return off;
  installed.add(doc);
  doc.addEventListener('keydown', onKey);
  return off;
}
