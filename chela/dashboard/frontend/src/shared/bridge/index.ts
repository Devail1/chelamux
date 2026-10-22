// CMX-178: the state the legacy modules and the React islands share, until a ported component
// owns its own fetch. A store is a value plus listeners, read in React through
// useSyncExternalStore (React's own primitive for an outside store, so no dependency and no
// tearing). Legacy code reaches the same stores through window.chelaBridge (`.get()` to read,
// `.set()` to publish) until CMX-212 P03 turns that into imports; it is bundled since P01.
import { useCallback, useSyncExternalStore } from 'react';
import { flushSync } from 'react-dom';
import type { NavState, RecentState, SidebarState } from '../../features/sidebar/model.ts';

export interface Store<T> {
  get(): T;
  set(value: T): void;
  subscribe(listener: () => void): () => void;
}

export function createStore<T>(initial: T): Store<T> {
  let value = initial;
  const listeners = new Set<() => void>();
  return {
    get: () => value,
    set(next) {
      if (Object.is(next, value)) return;
      value = next;
      for (const listener of [...listeners]) listener();
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}

export function useStore<T>(store: Store<T>): T {
  return useSyncExternalStore(store.subscribe, store.get);
}

/** useStore, repainted before `set` returns: for a store whose writer reads the DOM straight
 * back (the sidebar's rows: Alt+N, jump to the orchestrator). */
export function useStoreNow<T>(store: Store<T>): T {
  const subscribe = useCallback((paint: () => void) => store.subscribe(() => flushSync(paint)), [store]);
  return useSyncExternalStore(subscribe, store.get);
}

/** One row of /api/agents, as legacy fetched it. Typed loosely until the sidebar port. */
export type Agent = { window_id?: string; name?: string } & Record<string, unknown>;

/** The sidebar's server layout (CMX-82, /api/sidebar/layout), published whole on every change. */
export interface SidebarLayout {
  order: string[];
  custom: Record<string, unknown> | null;
  view: Record<string, unknown> | null;
  /** The desktop sidebar's width in px (CMX-278); null = the default. */
  width: number | null;
  loaded: boolean;
}

export interface Bridge {
  agents: Store<Agent[]>;
  sidebarLayout: Store<SidebarLayout>;
  /** `renderSidebarAgents()`: what the sidebar island draws (CMX-276). */
  sidebar: Store<SidebarState | null>;
  /** The nav rows (views.js) and the Work badges (work.js) (CMX-278). */
  nav: Store<NavState>;
  /** Recent sessions: the /api/restore payload and its toggle and undo (CMX-278). */
  recent: Store<RecentState>;
}

declare global {
  interface Window {
    chelaBridge?: Bridge;
  }
}

export function createBridge(): Bridge {
  return {
    agents: createStore<Agent[]>([]),
    sidebarLayout: createStore<SidebarLayout>({ order: [], custom: null, view: null, width: null, loaded: false }),
    sidebar: createStore<SidebarState | null>(null),
    nav: createStore<NavState>({ items: [], active: null, badges: {} }),
    recent: createStore<RecentState>({ rows: [], dispatcher_rows: [], revealed: false, undo: null }),
  };
}

/** The page's one bridge on `win`, created on first call and kept after. */
export function installBridge(win: Window): Bridge {
  win.chelaBridge ??= createBridge();
  return win.chelaBridge;
}
