// CMX-276: the sidebar's island. `model.ts` (the pure grouping model) and `controller.ts` (its
// state and commands, CMX-278) are this module's other entry points, which legacy imports.
export { LONG_PRESS_MS, Sidebar, type SidebarActions } from './lib/Sidebar.tsx';
export { legacyActions, mountSidebar } from './lib/mount.tsx';
export { ViewMenu } from './lib/menus.tsx';
export { Nav } from './lib/Nav.tsx';
export { Recent } from './lib/Recent.tsx';
export { ResizeHandle } from './lib/Resize.tsx';
export { CONTEXT_SHOWN_FROM } from './lib/rowview.ts';
