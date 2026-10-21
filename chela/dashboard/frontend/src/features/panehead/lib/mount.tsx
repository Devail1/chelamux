// CMX-241: every pane header on the page is ONE React root, portaled into a slot in each tile
// legacy terminals.js still builds (FRONTEND_PORT_PLAN.md, lane W: inside out). A tile's
// markup carries `<div class="island-slot" data-head-for="@N" data-head-wall="1">`; after
// inserting it, terminals.js calls mountPaneHeads(scope). A slot that leaves the page (a
// rebuild, a killed pane) is dropped on the next paint.
//
// Paints are SYNCHRONOUS (flushSync), on a mount and on every publish of the stores a header
// reads: Gridstack looks up each tile's `.gs-grip` handle as it inits, and legacy code reads
// the headers straight back (the wire's toast anchor, the room badges).
import { createPortal, flushSync } from 'react-dom';
import { createRoot, type Root } from 'react-dom/client';
import { agents } from '../../../shared/store/agents.ts';
import { orchestrator } from '../../../shared/store/orchestrator.ts';
import { pinnedPanes } from '../../../shared/store/wall.ts';
import { shares } from '../../share/index.ts';
import type { HeadAgent } from '../model.ts';
import { PaneHead, type PaneHeadHost } from './PaneHead.tsx';

type Slot = { wid: string; wall: boolean; key: number };

const slots = new Map<HTMLElement, Slot>();
let root: Root | null = null;
let seq = 0;

const NO_HOST: PaneHeadHost = { minimize() {}, dropPane() {}, wireDragStart() {}, leaveRoom() {}, labelsChanged() {} };
let host: PaneHeadHost = NO_HOST;

/** terminals.js hands over what the headers ask of the Wall, once at import. */
export function configurePaneHeads(h: PaneHeadHost): void {
  host = h;
}

function draw() {
  for (const el of [...slots.keys()]) if (!el.isConnected) slots.delete(el);
  const by = new Map<string, HeadAgent>();
  for (const a of agents.get() as HeadAgent[]) if (a.window_id) by.set(a.window_id, a);
  const orchWid = orchestrator.get().wid;
  const shared = shares.get();
  const pins = pinnedPanes.get();
  const now = Date.now();
  return [...slots].map(([el, s]) =>
    createPortal(
      <PaneHead wid={s.wid} wall={s.wall} agent={by.get(s.wid)} orchWid={orchWid} share={shared.get(s.wid)} pinned={pins.has(s.wid)} now={now} host={host} />,
      el,
      String(s.key),
    ),
  );
}

/** Repaint every header from the stores now (a no-op before the first mount). */
export function refreshPaneHeads(): void {
  const r = root;
  if (!r) return;
  flushSync(() => r.render(<>{draw()}</>));
}

/** Draw a header into every `[data-head-for]` slot under `scope` that has none yet. */
export function mountPaneHeads(scope: ParentNode): void {
  if (!root) {
    // portals only: the root's own container never enters the page
    root = createRoot(document.createElement('div'));
    for (const s of [agents, orchestrator, shares, pinnedPanes] as { subscribe(fn: () => void): () => void }[]) s.subscribe(refreshPaneHeads);
  }
  scope.querySelectorAll<HTMLElement>('[data-head-for]').forEach((el) => {
    const wid = el.dataset.headFor;
    if (wid && !slots.has(el)) slots.set(el, { wid, wall: el.dataset.headWall === '1', key: ++seq });
  });
  refreshPaneHeads();
}
