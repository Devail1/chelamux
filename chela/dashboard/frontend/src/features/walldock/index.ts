// CMX-242 (CMX-212 P28): the Wall's Minimized tray and the lazy dock for dispatched workers.
// `model.ts` (the pure decisions) is this module's other entry point.
export { configureDock, dockWids, mountDock, refreshDock, type DockHost } from './lib/mount.tsx';
export { Dock, type DockProps } from './lib/Dock.tsx';
export { forgetAutoDock, runLazyDock, stampActivity, type LazyDockHost } from './lib/state.ts';
