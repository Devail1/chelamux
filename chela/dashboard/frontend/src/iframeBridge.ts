// CMX-221 (FRONTEND_PORT_PLAN.md §3): what a terminal pane calls on its parent. A focused pane's
// keydown fires in the ttyd iframe's own document, so app.py's _TERM_PALETTE_KEY_SHIM catches
// Ctrl/⌘+K and Ctrl/⌘+, there and calls `window.parent.chela.openPalette()` /
// `.toggleSettings()`. This module registers exactly those two, and since CMX-248 (P34) they are
// all window.chela holds. iframeBridge.test.ts fails if either goes missing or another is added.
import { openPalette } from './features/palette/index.ts';
import { toggleSettings } from './features/settings/index.ts';

export const IFRAME_CONTRACT = {
  openPalette: () => openPalette(),
  toggleSettings: (focus?: string) => toggleSettings(focus),
};

export function installIframeBridge(win: Window = window): void {
  const w = win as unknown as { chela?: Record<string, unknown> };
  Object.assign((w.chela ??= {}), IFRAME_CONTRACT);
}
