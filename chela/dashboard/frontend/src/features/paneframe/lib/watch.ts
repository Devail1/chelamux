// Who is watching a pane, told to the server two ways. Grouped tmux sessions share one window of
// one size, and `window-size largest` lets a ghost wall in a background tab pin it to its size.
// 1. Ignore-size (CMX-175), instant: POST /api/term/<wid>/watch {cid, watching} when a frame
//    stops or starts being watched (tab hidden/shown, pane docked/restored).
// 2. Teardown: hidden past the grace, blank the frames a second viewer shares (a lone client
//    always fits), src kept in `data-suspended-src` (term-reconnect.js reads it, plan §3).
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

/** Watched: the tab is visible AND the pane is not docked (a docked tile is still a client). */
export function isWatched(wid: string): boolean {
  return document.visibilityState !== 'hidden' && !minimizedPanes.get().has(wid);
}

/** Report one frame's watched state to the tmux client its cid names. Best effort. */
export function reportWatch(ifr: HTMLIFrameElement): void {
  const src = ifr.getAttribute('src') || ''; // about:blank (torn down) names no wid: nothing to flag
  const wid = widOfSrc(src), cid = cidOfSrc(src);
  if (!wid || !cid) return;
  const body = JSON.stringify({ cid, watching: isWatched(wid) });
  api(`/api/term/${encodeURIComponent(wid)}/watch`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body }).catch(() => {});
}

async function teardown(): Promise<void> {
  const counts = (await api('/api/term/clients').catch(() => null)) as Record<string, number> | null;
  if (!counts || document.visibilityState !== 'hidden') return; // contention unknown, or shown again mid-fetch: disturb nothing
  const next = new Set(suspendedPanes.get());
  for (const ifr of framesOf()) {
    const wid = widOfSrc(ifr.getAttribute('src') || ''); // a blank frame names none
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
