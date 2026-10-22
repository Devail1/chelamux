// The page's switch keys inside a pane (from terminals.js). Once a pane has focus, keystrokes
// land in its ttyd iframe's OWN document, and a same-origin iframe's keydown never bubbles to
// the parent, so the page goes deaf to Alt+N (CMX-85, the sidebar's numbering) and Ctrl/⌘+K
// (CMX-116, the palette) until focus returns. So the same keys are wired inside each pane's
// document, on every load (each navigation brings a new Document). No INPUT/TEXTAREA guard:
// ttyd's page holds only the terminal, and its input IS xterm's helper textarea.
//
// CMX-114: a real keydown targets that textarea and xterm consumes it FIRST, so it never
// reliably reaches a bubble-phase listener (only a synthetic dispatchEvent does, which is why a
// jsdom guard once passed on a dead feature). Capture phase + stopImmediatePropagation: this
// runs before xterm, and the stray Alt-digit or Ctrl+K (readline's kill-line) never reaches the
// shell.
import { openPalette } from '../../palette/index.ts';
import { altSwitchKeydown, setAltHeld } from '../../sidebar/controller.ts';
import { isPaletteKey } from '../model.ts';

export function wireIframeShortcuts(ifr: HTMLIFrameElement): void {
  let doc: Document | null;
  try {
    doc = ifr.contentDocument;
  } catch {
    return; // cross-doc guard
  }
  if (!doc) return;
  doc.addEventListener(
    'keydown',
    (e) => {
      if (altSwitchKeydown(e)) {
        e.preventDefault();
        e.stopImmediatePropagation();
        return;
      }
      if (isPaletteKey(e)) {
        e.preventDefault();
        e.stopImmediatePropagation();
        openPalette();
      }
    },
    true,
  );
  // Releasing Alt inside a pane hides the sidebar numbers (CMX-85).
  doc.addEventListener(
    'keyup',
    (e) => {
      if (e.key === 'Alt') setAltHeld(false);
    },
    true,
  );
}
