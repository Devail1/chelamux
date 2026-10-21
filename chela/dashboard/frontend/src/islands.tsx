// The React islands (CMX-178). main.tsx imports this BEFORE the legacy main.js, so
// window.chelaBridge exists by the time a legacy module publishes to it. `pnpm dev` loads it
// alone (index.html), since its page has only the island slots.
import { mountDecisions } from './features/decisions/index.ts';
import { DiffModal } from './features/diffmodal/index.ts';
import { mountKeybar } from './features/keybar/index.ts';
import { mountSidebar } from './features/sidebar/index.ts';
import { TaskModal } from './features/taskmodal/index.ts';
import { VersionBadge } from './features/version/index.ts';
import { installBridge } from './shared/bridge/index.ts';
import { mountIsland } from './shared/islands/index.tsx';

export function boot(win: Window = window) {
  const bridge = installBridge(win);
  mountIsland('version-island', VersionBadge, {}, win.document);
  mountSidebar(win, bridge);
  mountIsland('diff-modal-island', DiffModal, {}, win.document);
  mountKeybar(win);
  mountDecisions(win);
  mountIsland('task-modal-island', TaskModal, {}, win.document);
}

boot();
