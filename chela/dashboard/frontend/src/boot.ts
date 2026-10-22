// CMX-217 (CMX-212 P03), CMX-248 (P34): boots the page. Each feature registers its own refresh
// lane and SSE handlers at import (shared/scheduler, shared/events), and the shell hands the
// features their hosts at import (features/shell), so this file only mounts and starts them.
import { installPalette } from './features/palette/index.ts';
import { applySavedTheme } from './features/settings/index.ts';
import { startShares } from './features/share/index.ts';
import { mountApp, renderNav, selectView, VIEWS } from './features/shell/index.ts';
import { findView } from './features/shell/views.ts';
import { installIframeBridge } from './iframeBridge.ts';
import { BOOT } from './shared/api/index.ts';
import { installBridge } from './shared/bridge/index.ts';
import { openEvents } from './shared/events/index.ts';
import { installTapTips } from './shared/popover/index.ts';
import { refresh, setViewTicks, startScheduler } from './shared/scheduler/index.ts';

export const REFRESH_MS = 30000;

/** What a page needs before its first tick: the bridge, the saved theme (before anything paints
 * from it), the panes' window.chela contract, the page keys and tap tooltips, and the root. */
export function mountPage(win: Window = window) {
  installBridge(win);
  applySavedTheme(win.document);
  installIframeBridge(win);
  installPalette(win);
  installTapTips(win);
  return mountApp(win.document);
}

/** The page's polls, its first view and its event stream: once per page. */
export function start(): void {
  setViewTicks((id) => findView(VIEWS, id)?.tick ?? null);
  renderNav(); // the sidebar's view list, from the registry, before anything selects one
  // The shares already live light the pill on load, whichever view is showing (CMX-240).
  startShares();
  // The Wall is the default canvas when terminals are on; selectView runs the boot tick.
  if (BOOT.terminalsEnabled) selectView('terminals');
  else void refresh();
  startScheduler(REFRESH_MS);
  openEvents();
}
