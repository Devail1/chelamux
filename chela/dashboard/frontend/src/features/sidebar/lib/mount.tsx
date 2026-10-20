// CMX-276: the sidebar island. It draws the bridge's `sidebar` store (nav.js publishes it on
// every render) and repaints SYNCHRONOUSLY on each publish: legacy reads the rows straight
// back (Alt+N, jump to the orchestrator), so the DOM must be current when publish returns.
import { flushSync } from 'react-dom';
import { createRoot, type Root } from 'react-dom/client';
import type { Bridge } from '../../../shared/bridge/index.ts';
import { Sidebar, type SidebarActions } from './Sidebar.tsx';

/** nav.js's handlers, looked up on `win.chela` at call time (nav.js loads after the island). */
export function legacyActions(win: Window): SidebarActions {
  const chela = () => (win as unknown as { chela?: Partial<SidebarActions> }).chela;
  return {
    selectAgent: name => chela()?.selectAgent?.(name),
    toggleGroup: key => chela()?.toggleGroup?.(key),
    openGroupMenu: (ev, key) => chela()?.openGroupMenu?.(ev, key),
    openRowMenu: (ev, key) => chela()?.openRowMenu?.(ev, key),
    groupNewSession: key => chela()?.groupNewSession?.(key),
    toggleArchivedSection: () => chela()?.toggleArchivedSection?.(),
    toggleShowArchived: () => chela()?.toggleShowArchived?.(),
    unarchiveRow: (btn, key) => chela()?.unarchiveRow?.(btn, key),
  };
}

/** Mount into `#sidebar-agents`; null when the page has none. */
export function mountSidebar(win: Window, bridge: Bridge, actions = legacyActions(win)): Root | null {
  const el = win.document.getElementById('sidebar-agents');
  if (!el) return null;
  const root = createRoot(el);
  const paint = () => flushSync(() => root.render(<Sidebar state={bridge.sidebar.get()} actions={actions} />));
  bridge.sidebar.subscribe(paint);
  paint();
  return root;
}
