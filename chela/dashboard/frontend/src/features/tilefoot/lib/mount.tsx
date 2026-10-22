// CMX-242 (CMX-212 P27): every pane footer and recap line on the page is ONE React root,
// portaled into slots in each tile legacy terminals.js still builds, the way the pane headers
// are (features/panehead). A tile's markup carries `<div class="island-slot" data-foot-for="@N"
// data-foot-wall="1">` under its terminal and, on the wall, `<div class="island-slot"
// data-recap-slot="@N">` under its header; after inserting it, terminals.js calls
// mountTileFoots(scope). A slot that leaves the page is dropped on the next paint.
//
// Paints are SYNCHRONOUS (flushSync), on a mount and on every publish of the stores a footer
// reads (agents, context, the pinned recaps): the grid's fill measures the panes right after.
import { createPortal, flushSync } from 'react-dom';
import { createRoot, type Root } from 'react-dom/client';
import { agents } from '../../../shared/store/agents.ts';
import { context, type ContextRow } from '../../../shared/store/context.ts';
import type { HeadAgent } from '../../panehead/index.ts';
import { Recap, recapOpen } from './Recap.tsx';
import { TileFoot } from './TileFoot.tsx';

type Slot = { wid: string; kind: 'foot' | 'recap'; wall: boolean; key: number };

const slots = new Map<HTMLElement, Slot>();
let root: Root | null = null;
let seq = 0;

function draw() {
  for (const el of [...slots.keys()]) if (!el.isConnected) slots.delete(el);
  const by = new Map<string, HeadAgent>();
  for (const a of agents.get() as HeadAgent[]) if (a.window_id) by.set(a.window_id, a);
  const ctx = new Map<string, ContextRow>();
  for (const c of context.get()) if (c.window_id) ctx.set(c.window_id, c);
  const open = recapOpen.get();
  return [...slots].map(([el, s]) =>
    createPortal(
      s.kind === 'recap'
        ? <Recap wid={s.wid} agent={by.get(s.wid)} open={open.has(s.wid)} />
        : <TileFoot wid={s.wid} wall={s.wall} agent={by.get(s.wid)} ctx={ctx.get(s.wid)} />,
      el,
      String(s.key),
    ),
  );
}

/** Repaint every footer and recap from the stores now (a no-op before the first mount). */
export function refreshTileFoots(): void {
  const r = root;
  if (!r) return;
  flushSync(() => r.render(<>{draw()}</>));
}

/** Draw into every footer and recap slot under `scope` that has none yet. */
export function mountTileFoots(scope: ParentNode): void {
  if (!root) {
    // portals only: the root's own container never enters the page
    root = createRoot(document.createElement('div'));
    for (const s of [agents, context, recapOpen] as { subscribe(fn: () => void): () => void }[]) s.subscribe(refreshTileFoots);
  }
  scope.querySelectorAll<HTMLElement>('[data-foot-for], [data-recap-slot]').forEach((el) => {
    if (slots.has(el)) return;
    const foot = el.dataset.footFor;
    const recap = el.dataset.recapSlot;
    if (foot) slots.set(el, { wid: foot, kind: 'foot', wall: el.dataset.footWall === '1', key: ++seq });
    else if (recap) slots.set(el, { wid: recap, kind: 'recap', wall: true, key: ++seq });
  });
  refreshTileFoots();
}
