// CMX-276: the sidebar island. It draws the bridge's `sidebar` store (controller.ts publishes it
// on every render) and repaints SYNCHRONOUSLY on each publish: legacy reads the rows straight
// back (Alt+N, jump to the orchestrator), so the DOM must be current when publish returns.
// CMX-277: the VIEW menu on the Sessions header is a second root over the same store.
// CMX-278: the nav rows, Recent sessions and the width handle are roots of their own.
import type { ReactNode } from 'react';
import { flushSync } from 'react-dom';
import { createRoot, type Root } from 'react-dom/client';
import type { Bridge, Store } from '../../../shared/bridge/index.ts';
import type { AgentRow } from '../model.ts';
import type { SidebarActions } from './actions.ts';
import { ViewMenu } from './menus.tsx';
import { Nav } from './Nav.tsx';
import { Recent } from './Recent.tsx';
import { ResizeHandle } from './Resize.tsx';
import { Sidebar } from './Sidebar.tsx';

// Every SidebarActions method, checked by the compiler: one missing here fails typecheck.
const ACTIONS = Object.keys({
  selectAgent: 1, selectView: 1, toggleGroup: 1, groupNewSession: 1, toggleArchivedSection: 1, unarchiveRow: 1,
  groupMenuAction: 1, rowMenuAction: 1, moveToGroup: 1, viewMenuAction: 1, newCustomGroup: 1, renameCustomGroup: 1,
  deleteCustomGroup: 1, setSidebarWidth: 1, resumeSession: 1, dismissRecentSession: 1, clearRecentSessions: 1,
  undoDismissRecent: 1, closeRecentUndo: 1, toggleDispatcherSessions: 1,
} satisfies Record<keyof SidebarActions, 1>);

/** The handlers, looked up on `win.chela` at call time (they load after the islands). */
export function legacyActions(win: Window): SidebarActions {
  const chela = () => (win as unknown as { chela?: Record<string, ((...a: unknown[]) => unknown) | undefined> }).chela;
  return Object.fromEntries(ACTIONS.map(name => [name, (...args: unknown[]) => chela()?.[name]?.(...args)])) as unknown as SidebarActions;
}

/** One root in `#id`, repainted synchronously whenever one of `stores` publishes. */
function island(win: Window, id: string, stores: Store<unknown>[], draw: () => ReactNode): Root | null {
  const el = win.document.getElementById(id);
  if (!el) return null;
  const root = createRoot(el);
  const paint = () => flushSync(() => root.render(draw()));
  for (const s of stores) s.subscribe(paint);
  paint();
  return root;
}

/** Mount into `#sidebar-agents` (and the VIEW menu, the nav rows, Recent sessions and the
 * width handle into their slots); null when the page has no sidebar. */
export function mountSidebar(win: Window, bridge: Bridge, actions = legacyActions(win)): Root | null {
  if (!win.document.getElementById('sidebar-agents')) return null;
  const root = island(win, 'sidebar-agents', [bridge.sidebar], () => <Sidebar state={bridge.sidebar.get()} actions={actions} />);
  island(win, 'sidebar-view-menu', [bridge.sidebar], () => <ViewMenu state={bridge.sidebar.get()} actions={actions} />);
  island(win, 'side-nav', [bridge.nav], () => <Nav nav={bridge.nav.get()} actions={actions} />);
  island(win, 'side-recent-island', [bridge.recent, bridge.agents],
    () => <Recent recent={bridge.recent.get()} agents={bridge.agents.get() as unknown as AgentRow[]} actions={actions} />);
  island(win, 'sidebar-resize', [bridge.sidebarLayout],
    () => <ResizeHandle width={bridge.sidebarLayout.get().width} onCommit={w => actions.setSidebarWidth(w)} />);
  return root;
}
