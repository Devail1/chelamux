// Who is watching a pane, told to the server two ways (from terminals.js).
//
// Grouped tmux sessions SHARE one real window, and a tmux window has one size: with
// `window-size largest` (scripts/agent-terminals.sh) a wall left open in a background tab keeps
// its ttyd socket alive (keepalive pings auto-pong while hidden) and pins every shared window to
// that ghost's geometry, cropping the wall you are looking at.
//
// 1. Ignore-size (CMX-175), instant and non-destructive: the moment a frame stops (or starts)
//    being watched (tab hidden or shown, pane minimized or restored) POST /api/term/<wid>/watch
//    with its cid, and app.py flags that ONE tmux client out of the `largest` computation.
// 2. Background-tab teardown, to reclaim --max-clients slots from a really abandoned tab: hidden
//    past TERM_HIDE_GRACE_MS, blank the frames whose window has a second viewer
//    (/api/term/clients; a lone client always fits, so it keeps its terminal). A blanked frame
//    keeps its src in `data-suspended-src` (term-reconnect.js reads it, §3 of the port plan) and
//    reconnects when the tab is shown again.
import { api } from '../../../shared/api/index.ts';
import { createStore } from '../../../shared/bridge/index.ts';
import { minimizedPanes } from '../../../shared/store/wall.ts';
import { cidOfSrc, widOfSrc } from '../model.ts';

export const TERM_HIDE_GRACE_MS = 45000;

/** The wids whose frames the teardown blanked (they draw `about:blank`). */
export const suspendedPanes = createStore<ReadonlySet<string>>(new Set());

/** True while a teardown holds frames blank: the 4 s tick must not add or reconnect panes. */
export const isSuspended = (): boolean => suspendedPanes.get().size > 0;

/** The wid a frame shows, from its live src or, once blanked, the stashed one. */
export function widOfFrame(ifr: HTMLIFrameElement): string | null {
  return widOfSrc(ifr.dataset.suspendedSrc || ifr.getAttribute('src') || '');
}

/** The frames on the Wall's stage, optionally only `wid`'s. */
export function framesOf(wid?: string): HTMLIFrameElement[] {
  const all = [...document.querySelectorAll<HTMLIFrameElement>('#term-stage iframe.term-frame')];
  return wid === undefined ? all : all.filter((f) => widOfFrame(f) === wid);
}

/** Watched: the tab is visible AND the pane is not in the dock (a minimized tile is still a live
 * tmux client, only hidden, and would go on sizing the window from inside the dock). */
export function isWatched(wid: string): boolean {
  return document.visibilityState !== 'hidden' && !minimizedPanes.get().has(wid);
}

/** Report one frame's watched state to the tmux client its cid names. Best effort: a missed
 * report leaves tmux's default (`largest` over every client). */
export function reportWatch(ifr: HTMLIFrameElement): void {
  const src = ifr.getAttribute('src') || '';
  if (!src || src === 'about:blank') return; // torn down: nothing to flag
  const wid = widOfSrc(src);
  const cid = cidOfSrc(src);
  if (!wid || !cid) return;
  api('/api/term/' + encodeURIComponent(wid) + '/watch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cid, watching: isWatched(wid) }),
  }).catch(() => {});
}

async function teardown(): Promise<void> {
  let counts: Record<string, number>;
  try {
    counts = ((await api('/api/term/clients')) ?? {}) as Record<string, number>;
  } catch {
    return; // contention unknown: disturb nothing
  }
  if (document.visibilityState !== 'hidden') return; // shown again mid-fetch
  const next = new Set(suspendedPanes.get());
  for (const ifr of framesOf()) {
    const src = ifr.getAttribute('src') || '';
    if (!src || src === 'about:blank') continue;
    const wid = widOfSrc(src);
    if (wid && (counts[wid] || 0) > 1) next.add(wid); // sole viewer: stays connected
  }
  if (next.size !== suspendedPanes.get().size) suspendedPanes.set(next);
}

let hideTimer: ReturnType<typeof setTimeout> | undefined;

/** Listen for the tab hiding and showing, and for panes entering and leaving the dock. Once. */
export function installWatch(enabled: () => boolean, resumed: () => void): void {
  document.addEventListener('visibilitychange', () => {
    if (!enabled()) return;
    clearTimeout(hideTimer);
    framesOf().forEach(reportWatch);
    if (document.hidden) hideTimer = setTimeout(() => void teardown(), TERM_HIDE_GRACE_MS);
    else if (isSuspended()) {
      suspendedPanes.set(new Set()); // the frames reconnect, freshly sized to the visible tile
      resumed(); // reconcile what the tick skipped while suspended
    }
  });
  let docked = minimizedPanes.get();
  minimizedPanes.subscribe(() => {
    const now = minimizedPanes.get();
    for (const wid of new Set([...docked, ...now])) {
      if (docked.has(wid) !== now.has(wid)) framesOf(wid).forEach(reportWatch);
    }
    docked = now;
  });
}
