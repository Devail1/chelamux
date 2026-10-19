// The build's entry (CMX-177), loaded by index.html through the manifest (CMX-178) BEFORE the
// legacy main.js, so window.chelaBridge exists by the time a legacy module publishes to it.
import { mountSidebar } from './features/sidebar/index.ts';
import { VersionBadge } from './features/version/index.ts';
import { installBridge } from './shared/bridge/index.ts';
import { mountIsland } from './shared/islands/index.tsx';

export function boot(win: Window = window) {
  const bridge = installBridge(win);
  mountIsland('version-island', VersionBadge, {}, win.document);
  mountSidebar(win, bridge);
}

boot();
