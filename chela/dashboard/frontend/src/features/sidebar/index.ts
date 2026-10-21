// CMX-276: the sidebar's session list. `model.ts` (the pure grouping model) is this module's
// other entry point, which legacy nav.js / terminals.js import.
export { Sidebar, type SidebarActions } from './lib/Sidebar.tsx';
export { legacyActions, mountSidebar } from './lib/mount.tsx';
export { ViewMenu } from './lib/menus.tsx';
export { CONTEXT_SHOWN_FROM } from './lib/rowview.ts';
