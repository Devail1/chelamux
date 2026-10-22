// CMX-246 (CMX-212 P31): every pane's terminal on the page is ONE React root, portaled into a
// slot in each tile legacy terminals.js still builds, as the headers and footers are. A tile's
// markup carries `<div class="island-slot" data-frame-for="@N">` where its terminal goes; after
// inserting it, terminals.js calls mountPaneFrames(scope). A slot that leaves the page is
// dropped on the next paint (its poll stops with it).
//
// Paints are SYNCHRONOUS (flushSync): legacy reads the frames straight back (the share reload,
// the focus ring, the keybar fit).
import { createPortal, flushSync } from 'react-dom';
import { createRoot, type Root } from 'react-dom/client';
import { BOOT } from '../../../shared/api/index.ts';
import { onEvent } from '../../../shared/events/index.ts';
import { agents } from '../../../shared/store/agents.ts';
import { view } from '../../../shared/store/view.ts';
import { displayLabel, type HeadAgent } from '../../panehead/index.ts';
import { startFocusTracking } from './focus.ts';
import { host, type PaneFrameHost } from './host.ts';
import { PaneFrame } from './PaneFrame.tsx';
import { markReady, readiness } from './ready.ts';
import { wireIframeShortcuts } from './shortcuts.ts';
import { installWatch, isSuspended, reportWatch, suspendedPanes } from './watch.ts';

type Slot = { wid: string; key: number };

const slots = new Map<HTMLElement, Slot>();
let root: Root | null = null;
let seq = 0;

const onWall = () => BOOT.terminalsEnabled && view.get() === 'terminals';

function loaded(ifr: HTMLIFrameElement): void {
  wireIframeShortcuts(ifr);
  reportWatch(ifr); // this fresh connection's watched state (CMX-175)
  host.frameLoaded();
}

function draw() {
  for (const el of [...slots.keys()]) if (!el.isConnected) slots.delete(el);
  const by = new Map<string, HeadAgent>();
  for (const a of agents.get() as HeadAgent[]) if (a.window_id) by.set(a.window_id, a);
  const ready = readiness.get();
  const off = suspendedPanes.get();
  return [...slots].map(([el, s]) =>
    createPortal(
      <PaneFrame wid={s.wid} title={displayLabel(by.get(s.wid), s.wid)} ready={ready.get(s.wid) ?? 'pending'} suspended={off.has(s.wid)} onLoad={loaded} />,
      el,
      String(s.key),
    ),
  );
}

/** Repaint every pane frame from the stores now (a no-op before the first mount). */
export function refreshPaneFrames(): void {
  const r = root;
  if (!r) return;
  flushSync(() => r.render(<>{draw()}</>));
}

/** Draw a terminal into every `[data-frame-for]` slot under `scope` that has none yet. */
export function mountPaneFrames(scope: ParentNode): void {
  if (!root) {
    // portals only: the root's own container never enters the page
    root = createRoot(document.createElement('div'));
    for (const s of [agents, readiness, suspendedPanes] as { subscribe(fn: () => void): () => void }[]) s.subscribe(refreshPaneFrames);
  }
  scope.querySelectorAll<HTMLElement>('[data-frame-for]').forEach((el) => {
    const wid = el.dataset.frameFor;
    if (wid && !slots.has(el)) slots.set(el, { wid, key: ++seq });
  });
  refreshPaneFrames();
}

let installed = false;

/** terminals.js hands over what the frames ask of the Wall, once at import; this also starts
 * listening for the `term-ready` push, the tab's visibility and the dock. */
export function configurePaneFrames(h: PaneFrameHost): void {
  Object.assign(host, h);
  if (installed) return;
  installed = true;
  installWatch(() => BOOT.terminalsEnabled, () => {
    if (onWall()) host.tick();
  });
  // A terminal just got its port: swap its placeholder in now. The ~1.5 s ready poll stays the
  // reliable default, so this only accelerates. `ready` carries window ids (@N).
  onEvent('term-ready', (d) => {
    if (!onWall()) return;
    const wids = (d as { ready?: unknown } | null)?.ready;
    if (!Array.isArray(wids)) return;
    const shown = new Set([...slots.values()].map((s) => s.wid));
    for (const wid of wids) if (typeof wid === 'string' && shown.has(wid) && readiness.get().get(wid) !== 'ready') markReady(wid);
  });
}

// ---- The Wall's 4 s tick --------------------------------------------------------------------
// A dead pane (window killed, `claude` exited) would otherwise sit blank until the 30 s refresh,
// so while the Wall is open the host's reconcile runs every TERM_REFRESH_MS (it diffs by window
// id, so a rename is never a drop and an add). Never while a teardown holds frames blank.
export const TERM_REFRESH_MS = 4000;
let timer: ReturnType<typeof setInterval> | null = null;

export function startTermTimer(): void {
  if (!BOOT.terminalsEnabled) return;
  stopTermTimer();
  timer = setInterval(() => {
    if (!isSuspended()) host.tick();
  }, TERM_REFRESH_MS);
  startFocusTracking();
}

export function stopTermTimer(): void {
  if (timer) clearInterval(timer);
  timer = null;
}
