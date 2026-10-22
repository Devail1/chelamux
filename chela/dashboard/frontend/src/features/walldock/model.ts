// CMX-242 (CMX-212 P28): the dock's decisions, pure. No DOM, no fetch, no storage. From
// terminals.js `_lazyDock` and `_taskbarOrder`.

/** The /api/agents fields the dock reads. */
export interface DockAgent {
  window_id?: string;
  /** The RUN ROW owns this window id (server-derived, never a guess from the name). */
  dispatched?: boolean;
  needs_human?: boolean;
  session_status?: string | null;
}

/**
 * The lazy dock's per-window decision (CMX-76): absent (never decided) → 'docked' (the wall hid
 * a working worker) → 'popped' (it blocked, so it earned the wall; from then on the window is
 * the human's and the wall never touches it again). A decision is taken exactly ONCE per
 * window, so the wall never fights the human: re-deriving each tick would re-dock a pane the
 * moment you pulled it out.
 */
export type AutoDock = Readonly<Record<string, 'docked' | 'popped'>>;

export interface LazyDockStep {
  state: AutoDock;
  /** Tiles to take off the wall into the dock. */
  minimize: string[];
  /** Docked tiles to put back on the wall. */
  restore: string[];
  /** `state` differs from the one passed in (persist it). */
  changed: boolean;
}

export interface LazyDockView {
  /** The window has a tile on the wall (a tile not built yet is decided on the next tick). */
  onWall(wid: string): boolean;
  /** The window is minimized now. */
  minimized(wid: string): boolean;
}

const wants = (a: DockAgent) => a.needs_human === true || a.session_status === 'waiting';

/** One poll's worth of the machine. Never mutates `state`. */
export function lazyDockStep(agents: readonly (DockAgent | null | undefined)[], state: AutoDock, view: LazyDockView): LazyDockStep {
  const next = new Map(Object.entries(state));
  const minimize: string[] = [];
  const restore: string[] = [];
  let changed = false;
  for (const a of agents) {
    const wid = a?.window_id;
    if (!a || !wid) continue;
    const s = next.get(wid);
    // Not (or no longer) the dispatcher's: tmux hands `@N` out afresh after a server restart,
    // and hiding a HUMAN's terminal is the one failure this feature may not have.
    if (!a.dispatched) {
      if (s === 'docked' && view.minimized(wid)) restore.push(wid);
      if (s) {
        next.delete(wid);
        changed = true;
      }
      continue;
    }
    if (!s) {
      if (wants(a)) next.set(wid, 'popped'); // born blocked: it keeps its tile
      else if (view.onWall(wid)) {
        minimize.push(wid); // working quietly: off the attention surface
        next.set(wid, 'docked');
      } else continue; // no tile yet: decide on the next tick
      changed = true;
    } else if (s === 'docked' && wants(a)) {
      // THE POP-OUT: it wants a human, so it has earned the wall. One pop, ever.
      if (view.minimized(wid)) restore.push(wid);
      next.set(wid, 'popped');
      changed = true;
    }
  }
  return { state: Object.fromEntries(next), minimize, restore, changed };
}

/**
 * The dock's chip order: panes waiting on you first, then most recently busy first (`activity`:
 * when each last STARTED being busy), the manual `base` order as the stable tiebreak.
 */
export function taskbarOrder(base: readonly string[], status: Readonly<Record<string, string | null | undefined>>,
  activity: Readonly<Record<string, number>>): string[] {
  const waiting = (w: string) => (status[w] === 'waiting' ? 0 : 1);
  const ts = (w: string) => activity[w] ?? 0;
  return base.slice().sort((a, b) => waiting(a) - waiting(b) || ts(b) - ts(a) || base.indexOf(a) - base.indexOf(b));
}
