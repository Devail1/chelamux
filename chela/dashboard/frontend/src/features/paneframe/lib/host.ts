// What the pane frames ask of the Wall that terminals.js still owns, handed over at its import.
export interface PaneFrameHost {
  /** The single pane's window id ('' when none). */
  activeWid(): string;
  /** The 4 s reconcile (terminals.js termTick): drop vanished panes, absorb new ones. */
  tick(): void;
  /** A pane's terminal (re)loaded: the keybar re-fits it (CMX-154). */
  frameLoaded(): void;
}

export const host: PaneFrameHost = { activeWid: () => '', tick() {}, frameLoaded() {} };
