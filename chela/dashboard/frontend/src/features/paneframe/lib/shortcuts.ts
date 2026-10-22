// The page's switch keys inside a pane. A focused pane's keystrokes land in its iframe's OWN
// document and never bubble to the parent, so Alt+N (CMX-85) and Ctrl/⌘+K (CMX-116) are wired
// there too, on every load (each navigation brings a new Document).
// CMX-114: xterm consumes a real keydown on its helper textarea FIRST, so a bubble listener never
// reliably sees it: capture phase + stopImmediatePropagation, so the stray Alt-digit or Ctrl+K
// (readline's kill-line) never reaches the shell.
import { openPalette } from '../../palette/index.ts';
import { altSwitchKeydown, setAltHeld } from '../../sidebar/controller.ts';
import { isPaletteKey } from '../model.ts';
import { attempt } from './focus.ts';

export function wireIframeShortcuts(ifr: HTMLIFrameElement): void {
  const doc = attempt(() => ifr.contentDocument);
  if (!doc) return;
  doc.addEventListener('keydown', (e) => {
    const alt = altSwitchKeydown(e); // first: it also tracks Alt held
    if (!alt && !isPaletteKey(e)) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    if (!alt) openPalette();
  }, true);
  // Releasing Alt inside a pane hides the sidebar numbers (CMX-85).
  doc.addEventListener('keyup', (e) => e.key === 'Alt' && setAltHeld(false), true);
}
