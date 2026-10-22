// CMX-242 (CMX-212 P28): the dock's memory: the lazy dock's decisions (persisted, so a reload
// never re-litigates one; mid-gate it would re-dock the very pane you were answering) and when
// each pane last started being busy (the chips' recency order).
import { minimizedPanes } from '../../../shared/store/wall.ts';
import { lazyDockStep, type AutoDock, type DockAgent } from '../model.ts';

const AUTODOCK_KEY = 'pc_wall_autodock';

function loadAutoDock(): AutoDock {
  try {
    const v: unknown = JSON.parse(localStorage.getItem(AUTODOCK_KEY) || '{}');
    return v && typeof v === 'object' && !Array.isArray(v) ? (v as AutoDock) : {};
  } catch {
    return {};
  }
}

let autoDock: AutoDock = loadAutoDock();

function saveAutoDock(): void {
  try {
    localStorage.setItem(AUTODOCK_KEY, JSON.stringify(autoDock));
  } catch {
    /* private mode: the decisions hold for this page */
  }
}

/** What the lazy dock asks of the Wall (terminals.js owns the grid). */
export interface LazyDockHost {
  onWall(wid: string): boolean;
  minimize(wid: string): void;
  restore(wid: string): void;
}

/**
 * A DISPATCHED worker opens MINIMIZED and POPS OUT when it blocks on a human (CMX-76): a worker
 * must not occupy attention surface (a Telegram topic, a Wall tile) until it needs a human.
 * Called once per agents poll, after the tiles exist.
 */
export function runLazyDock(agents: readonly DockAgent[], host: LazyDockHost): void {
  const step = lazyDockStep(agents, autoDock, { onWall: host.onWall, minimized: (w) => minimizedPanes.get().has(w) });
  autoDock = step.state;
  step.restore.forEach((w) => host.restore(w));
  step.minimize.forEach((w) => host.minimize(w));
  if (step.changed) saveAutoDock();
}

/** The window died: its decision dies with it. */
export function forgetAutoDock(wid: string): void {
  if (!(wid in autoDock)) return;
  autoDock = Object.fromEntries(Object.entries(autoDock).filter(([w]) => w !== wid));
  saveAutoDock();
}

const status: Record<string, string | null | undefined> = {};
const activity: Record<string, number> = {};

/** Stamp the panes that just STARTED being busy (a rising edge), for the recency order. */
export function stampActivity(agents: readonly DockAgent[], now = Date.now()): void {
  for (const a of agents) {
    const wid = a.window_id;
    if (!wid) continue;
    if (a.session_status === 'busy' && status[wid] !== 'busy') activity[wid] = now;
    status[wid] = a.session_status;
  }
}

export function paneActivity(): { status: Readonly<typeof status>; activity: Readonly<typeof activity> } {
  return { status, activity };
}
