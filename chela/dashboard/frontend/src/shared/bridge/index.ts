// CMX-178: the state the legacy modules and the React islands share, until a ported component
// owns its own fetch. A store is a value plus listeners, read in React through
// useSyncExternalStore (React's own primitive for an outside store, so no dependency and no
// tearing). Legacy code reaches the same stores through window.chelaBridge (`.get()` to read,
// `.set()` to publish) until CMX-212 P03 turns that into imports; it is bundled since P01.
import { useSyncExternalStore } from 'react';

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

/** One row of /api/agents, as legacy fetched it. Typed loosely until the sidebar port. */
export type Agent = { window_id?: string; name?: string } & Record<string, unknown>;

/** nav.js's `_layout` (CMX-82, /api/sidebar/layout), published whole on every change. */
export interface SidebarLayout {
  order: string[];
  custom: Record<string, unknown> | null;
  view: Record<string, unknown> | null;
  loaded: boolean;
}

export interface Bridge {
  agents: Store<Agent[]>;
  sidebarLayout: Store<SidebarLayout>;
}

declare global {
  interface Window {
    chelaBridge?: Bridge;
  }
}

export function createBridge(): Bridge {
  return {
    agents: createStore<Agent[]>([]),
    sidebarLayout: createStore<SidebarLayout>({ order: [], custom: null, view: null, loaded: false }),
  };
}

/** The page's one bridge on `win`, created on first call and kept after. */
export function installBridge(win: Window): Bridge {
  win.chelaBridge ??= createBridge();
  return win.chelaBridge;
}
