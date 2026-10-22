// What the pane frames ask of the Wall, which legacy terminals.js still owns (the grid, the
// single pane's selector). It hands this over once at import (configurePaneFrames).
export interface PaneFrameHost {
  /** The single pane's window id ('' when none). */
  activeWid(): string;
  /** The 4 s reconcile: drop vanished panes, absorb new ones, repaint the status. */
  tick(): void;
  /** A pane's terminal (re)loaded: the keybar re-fits it (CMX-154). */
  frameLoaded(): void;
}

export const host: PaneFrameHost = { activeWid: () => '', tick() {}, frameLoaded() {} };
