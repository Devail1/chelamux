// CMX-248 (CMX-212 P34): the app shell. <App> (the page), the view switch and the hosts it
// hands the features (lib/nav.ts, which configures them at import), the tab signal. `views.ts`
// (the registry's pure kernel) is this module's other entry point.
export { App } from './lib/App.tsx';
export { mountApp, mountSlots } from './lib/mount.tsx';
export { agentDotColor, renderNav, selectAgent, selectView, showAgentDetail } from './lib/nav.ts';
export { VIEWS } from './lib/registry.ts';
export { SLOTS, sidebarActions } from './lib/slots.tsx';
export { updateTabSignal } from './lib/tabSignal.ts';
