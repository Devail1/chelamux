// The React islands (CMX-178). main.tsx imports this BEFORE the legacy main.js, so
// window.chelaBridge exists by the time a legacy module publishes to it. `pnpm dev` loads it
// alone (index.html), since its page has only the island slots.
import { mountDecisions } from './features/decisions/index.ts';
import { DiffModal } from './features/diffmodal/index.ts';
import { mountKeybar } from './features/keybar/index.ts';
import { NewTask } from './features/newtask/index.ts';
import { installPalette } from './features/palette/index.ts';
import { applySavedTheme } from './features/settings/index.ts';
import { mountShares } from './features/share/index.ts';
import { mountSidebar } from './features/sidebar/index.ts';
import { TaskModal } from './features/taskmodal/index.ts';
import { WorkToasts, WorkView } from './features/work/index.ts';
import { installBridge } from './shared/bridge/index.ts';
import { mountChrome } from './chrome.tsx';
import { installIframeBridge } from './iframeBridge.ts';
import { mountIsland } from './shared/islands/index.tsx';
import { installTapTips } from './shared/popover/index.ts';

export function boot(win: Window = window) {
  const bridge = installBridge(win);
  applySavedTheme(win.document); // before anything paints from it
  installIframeBridge(win);
  installPalette(win);
  mountSidebar(win, bridge);
  mountIsland('diff-modal-island', DiffModal, {}, win.document);
  mountKeybar(win);
  // CMX-233: the Work view (its Board, Runs and Schedules segments) and its toasts.
  mountIsland('panel-work', WorkView, {}, win.document);
  mountIsland('work-toasts', WorkToasts, {}, win.document);
  mountIsland('newtask-island', NewTask, {}, win.document);
  mountDecisions(win);
  mountIsland('task-modal-island', TaskModal, {}, win.document);
  mountChrome(win.document);
  installTapTips(win);
  mountShares(win.document);
}

boot();
