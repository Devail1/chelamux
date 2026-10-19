// CMX-276: the sidebar's session list. `model.ts` (the pure grouping model) is this module's
// other entry point; legacy imports its build, static/app/sidebarmodel.js.
export { Sidebar, type MenuEvent, type SidebarActions } from './lib/Sidebar.tsx';
export { legacyActions, mountSidebar } from './lib/mount.tsx';
export { CONTEXT_SHOWN_FROM } from './lib/rowview.ts';
