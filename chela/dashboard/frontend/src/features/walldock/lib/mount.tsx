// CMX-242 (CMX-212 P28): the dock's root, rendered into #term-min-dock, which terminals.js hands
// over on its first render. It shows only in wall mode and only while a pane is minimized; the
// paint is SYNCHRONOUS (flushSync) because the grid fits the panes above the dock right after a
// minimize or a restore, and reads its height to do it.
import { flushSync } from 'react-dom';
import { createRoot, type Root } from 'react-dom/client';
import { agents } from '../../../shared/store/agents.ts';
import { minimizedPanes, orderedWids, termMode } from '../../../shared/store/wall.ts';
import type { HeadAgent } from '../../panehead/index.ts';
import { taskbarOrder } from '../model.ts';
import { Dock } from './Dock.tsx';
import { paneActivity, stampActivity } from './state.ts';

/** What a chip asks of the Wall: put this tile back (terminals.js owns the grid). */
export interface DockHost {
  restore(wid: string): void;
}

let host: DockHost = { restore() {} };
let el: HTMLElement | null = null;
let root: Root | null = null;
let subscribed = false;

export function configureDock(h: DockHost): void {
  host = h;
}

/** The minimized panes the dock shows, in chip order ([] outside wall mode). */
export function dockWids(): string[] {
  if (termMode.get() !== 'wall') return [];
  const min = minimizedPanes.get();
  const live = (agents.get() as HeadAgent[])
    .filter((a) => a.window_id && (a as { online?: boolean }).online !== false)
    .map((a) => a.window_id as string)
    .filter((w) => min.has(w));
  const { status, activity } = paneActivity();
  return taskbarOrder(orderedWids(live), status, activity);
}

/** Repaint the dock from the stores now (a no-op before the first mount). */
export function refreshDock(): void {
  const r = root;
  const target = el;
  if (!r || !target) return;
  const rows = agents.get() as HeadAgent[];
  stampActivity(rows);
  const wids = dockWids();
  const by = new Map<string, HeadAgent>();
  for (const a of rows) if (a.window_id) by.set(a.window_id, a);
  flushSync(() => r.render(<Dock wids={wids} agents={by} restore={(w) => host.restore(w)} />));
  target.hidden = !wids.length;
}

/** Render the dock into `target` (idempotent for the same element). */
export function mountDock(target: HTMLElement | null): void {
  if (!target) return;
  if (target !== el) {
    root?.unmount();
    el = target;
    root = createRoot(target);
  }
  if (!subscribed) {
    subscribed = true;
    for (const s of [agents, minimizedPanes, termMode] as { subscribe(fn: () => void): () => void }[]) s.subscribe(refreshDock);
  }
  refreshDock();
}
