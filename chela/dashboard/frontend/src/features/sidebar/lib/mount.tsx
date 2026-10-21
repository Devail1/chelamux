// CMX-276: the sidebar island. It draws the bridge's `sidebar` store (nav.js publishes it on
// every render) and repaints SYNCHRONOUSLY on each publish: legacy reads the rows straight
// back (Alt+N, jump to the orchestrator), so the DOM must be current when publish returns.
// CMX-277: the VIEW menu on the Sessions header is a second root over the same store.
import { flushSync } from 'react-dom';
import type { Root } from 'react-dom/client';
import { createIslandRoot } from '../../../shared/islands/index.tsx';
import type { Bridge } from '../../../shared/bridge/index.ts';
import { ViewMenu } from './menus.tsx';
import { Sidebar, type SidebarActions } from './Sidebar.tsx';

// Every SidebarActions method, checked by the compiler: one missing here fails typecheck.
const ACTIONS = Object.keys({
  selectAgent: 1, toggleGroup: 1, groupNewSession: 1, toggleArchivedSection: 1, unarchiveRow: 1, groupMenuAction: 1,
  rowMenuAction: 1, moveToGroup: 1, viewMenuAction: 1, newCustomGroup: 1, renameCustomGroup: 1, deleteCustomGroup: 1,
} satisfies Record<keyof SidebarActions, 1>);

/** nav.js's handlers, looked up on `win.chela` at call time (nav.js loads after the island). */
export function legacyActions(win: Window): SidebarActions {
  const chela = () => (win as unknown as { chela?: Record<string, ((...a: unknown[]) => unknown) | undefined> }).chela;
  return Object.fromEntries(ACTIONS.map(name => [name, (...args: unknown[]) => chela()?.[name]?.(...args)])) as unknown as SidebarActions;
}

/** Mount into `#sidebar-agents` (and the VIEW menu into `#sidebar-view-menu`); null when the page has no sidebar. */
export function mountSidebar(win: Window, bridge: Bridge, actions = legacyActions(win)): Root | null {
  const el = win.document.getElementById('sidebar-agents');
  if (!el) return null;
  const root = createIslandRoot(el);
  const slot = win.document.getElementById('sidebar-view-menu');
  const view = slot ? createIslandRoot(slot) : null;
  const paint = () => flushSync(() => {
    const state = bridge.sidebar.get();
    root.render(<Sidebar state={state} actions={actions} />);
    view?.render(<ViewMenu state={state} actions={actions} />);
  });
  bridge.sidebar.subscribe(paint);
  paint();
  return root;
}
