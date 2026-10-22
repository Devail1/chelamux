// CMX-246 (CMX-212 P31): a Wall pane's terminal frame, portaled into each tile legacy
// terminals.js builds: its readiness, the focus ring, the keys a pane carries, who is watching
// it, the background-tab teardown and the 4 s tick; and the keybar's keys and Paste.
export { configurePaneFrames, mountPaneFrames, refreshPaneFrames, startTermTimer, stopTermTimer, TERM_REFRESH_MS } from './lib/mount.tsx';
export { PaneFrame, type PaneFrameProps } from './lib/PaneFrame.tsx';
export type { PaneFrameHost } from './lib/host.ts';
export { forgetReady, isReady, markReady, readiness, READY_MAX_TRIES, READY_POLL_MS, resolveReady, retryReady, type Readiness } from './lib/ready.ts';
export { applyTermFocus, focusedPane, focusFrame } from './lib/focus.ts';
export { framesOf, isSuspended, isWatched, reportWatch, suspendedPanes, TERM_HIDE_GRACE_MS, widOfFrame } from './lib/watch.ts';
export { htmlToText, termKey, termPaste } from './lib/paste.ts';
export { wireIframeShortcuts } from './lib/shortcuts.ts';
export { cidOfSrc, frameSrc, isPaletteKey, newCid, termSrc, uriListToText, widOfSrc } from './model.ts';
