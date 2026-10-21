// The React islands (CMX-178). main.tsx imports this BEFORE the legacy main.js, so
// window.chelaBridge exists by the time a legacy module publishes to it. `pnpm dev` loads it
// alone (index.html), since its page has only the island slots.
import { DiffModal } from './features/diffmodal/index.ts';
import { mountKeybar } from './features/keybar/index.ts';
import { NewTask } from './features/newtask/index.ts';
import { Runs } from './features/runs/index.ts';
import { Schedules } from './features/schedules/index.ts';
import { mountSidebar } from './features/sidebar/index.ts';
import { VersionBadge } from './features/version/index.ts';
import { installBridge } from './shared/bridge/index.ts';
import { mountIsland } from './shared/islands/index.tsx';

export function boot(win: Window = window) {
  const bridge = installBridge(win);
  mountIsland('version-island', VersionBadge, {}, win.document);
  mountSidebar(win, bridge);
  mountIsland('diff-modal-island', DiffModal, {}, win.document);
  mountKeybar(win);
  // CMX-231: the Work view's Runs and Schedules segments, and the New-task dialog.
  mountIsland('work-runs', Runs, {}, win.document);
  mountIsland('work-schedules', Schedules, {}, win.document);
  mountIsland('newtask-island', NewTask, {}, win.document);
}

boot();
