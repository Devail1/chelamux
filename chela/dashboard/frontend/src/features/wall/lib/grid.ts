// CMX-247 (CMX-212 P33): the Wall's Gridstack grid (was terminals.js). The vendored bundle is a
// page global (index.html loads it first), read when the wall builds so a test page can stand one
// in. Tiles are keyed by window id (`gs-id`). Manual layout (pc_wall_layout) is SACRED: a drag, a
// resize and a preset write it; auto-arrange and Focus only compute a live fill and never write
// it, so turning either off restores exactly what the user left.
import { agents } from '../../../shared/store/agents.ts';
import { paneOrderKey, pinnedPanes, wallLayout } from '../../../shared/store/wall.ts';
import { focusedPane } from '../../paneframe/index.ts';
import { wantsHuman, type HeadAgent } from '../../panehead/index.ts';
import { focusLayout, rankOrder } from '../../../../../static/js/wallmodel.ts';

export type Cell = { x: number; y: number; w: number; h: number };
type Node = Cell & { id?: string; el?: HTMLElement };
export type GridItem = HTMLElement & { gridstackNode?: Node };
interface Grid {
  engine: { nodes: Node[] };
  on(ev: string, fn: (e: Event, el: GridItem) => void): void;
  save(content: false): Node[];
  makeWidget(el: HTMLElement): void;
  removeWidget(el: HTMLElement, removeDOM?: boolean): void;
  destroy(removeDOM?: boolean): void;
  cellHeight(px: number): void;
  batchUpdate(on?: boolean): void;
  update(el: HTMLElement, o: Partial<Cell>): void;
  enableMove(on: boolean): void;
  enableResize(on: boolean): void;
}
declare global {
  var GridStack: { init(opts: object, el: HTMLElement): Grid };
}

export const WALL_CELL_H = 70; // GridStack.init's cellHeight: the fill counts the stage in these rows
const LAYOUT_KEY = 'pc_wall_layout';
let grid: Grid | null = null;
let autoSig = ''; // the order auto-arrange last applied: a poll with no change never reflows
let focusWid: string | null = null; // Focus's large pane
let focusSig = ''; // "focus wid | strip order" last applied: the same guard for Focus

export const hasGrid = (): boolean => grid !== null;
const pinned = (wid: string | undefined) => !!wid && pinnedPanes.get().has(wid);
const rows = () => agents.get() as HeadAgent[];
const stage = () => document.getElementById('term-stage');
const gsEl = () => stage()?.querySelector<HTMLElement>('.grid-stack') ?? null;
const widOf = (n: Node) => n.el?.getAttribute('gs-id') ?? null;
export const isPhone = (): boolean => window.matchMedia('(max-width: 768px)').matches;

/** The wall's tile for `wid`, or null. */
export const gridItem = (wid: string): GridItem | null =>
  [...document.querySelectorAll<GridItem>('#term-stage .grid-stack-item')].find((it) => it.getAttribute('gs-id') === wid) ?? null;

export function savedLayout(): Record<string, Partial<Cell>> {
  try {
    const v: unknown = JSON.parse(localStorage.getItem(LAYOUT_KEY) || '{}');
    return v && typeof v === 'object' ? (v as Record<string, Partial<Cell>>) : {};
  } catch { return {}; }
}

function persist(): void {
  const out: Record<string, Cell> = {};
  for (const n of grid?.save(false) ?? []) if (n.id != null) out[n.id] = { x: n.x, y: n.y, w: n.w, h: n.h };
  localStorage.setItem(LAYOUT_KEY, JSON.stringify(out));
}

export function destroyGrid(): void {
  try { grid?.destroy(false); } catch { /* already gone */ }
  grid = null;
}

/** Adopt the tiles under `el` as the wall's grid (their headers, and so `.gs-grip`, exist). */
export function initGrid(el: HTMLElement): void {
  const g = (grid = GridStack.init({
    column: 12, cellHeight: WALL_CELL_H, margin: 6, float: true, handle: '.gs-grip',
    // scroll:false: Gridstack's auto-scroll near the edge scrolls the page out from under a drag
    draggable: { handle: '.gs-grip', scroll: false },
    resizable: { handles: 'e, se, s, sw, w' },
    columnOpts: { breakpoints: [{ w: 768, c: 1 }] },
  }, el));
  // iframes swallow the mouse mid-drag: `gs-dragging` drops their pointer events. A drag (not a
  // resize) also hides them (`term-dragging`) and pins page scroll while Gridstack grows a phantom
  // row. A locked drag is a swap, so the grid box is frozen to its fitted height.
  const dragging = (on: boolean, drag: boolean) => {
    el.classList.toggle('gs-dragging', on);
    if (drag) for (const c of [el, document.body]) c.classList.toggle('term-dragging', on);
  };
  const lockBox = (on: boolean) => {
    el.style.maxHeight = on ? `${el.offsetHeight}px` : '';
    el.classList.toggle('term-lock-drag', on);
  };
  g.on('dragstart', () => {
    dragging(true, true);
    if (wallLayout.get().locked) { snapshot(); lockBox(true); }
  });
  g.on('resizestart', () => dragging(true, false));
  g.on('drag', (_e, item) => { if (wallLayout.get().locked) setSwapHover(swapTarget(item)); });
  g.on('dragstop', (_e, item) => {
    dragging(false, true);
    if (wallLayout.get().locked) doSwap(item);
    lockBox(false);
    persist();
  });
  g.on('resizestop', () => { dragging(false, false); persist(); });
  resetLayoutSigs();
}

export const adopt = (el: HTMLElement): void => grid?.makeWidget(el);

/** Take a tile out of the grid; `dom` also removes the element. */
export function unadopt(el: HTMLElement, dom: boolean): void {
  try { grid?.removeWidget(el, dom); } catch { if (dom) el.remove(); }
}

/** The row under the lowest tile: where a tile with no saved spot goes. */
export const nextFreeY = (): number => (grid?.engine.nodes ?? []).reduce((m, n) => Math.max(m, (n.y || 0) + (n.h || 0)), 0);

function batch(fn: (g: Grid) => void): void {
  const g = grid;
  if (!g) return;
  g.batchUpdate();
  try { fn(g); } finally { g.batchUpdate(false); } // commit (this Gridstack has no commit())
}

// ---- Lock: a drag swaps two panes ------------------------------------------------------------
// Gridstack's own swap only fires for equal-sized, touching tiles. So lock turns resize off and the
// drop swaps: snapshot every tile at dragstart, then exchange the dragged tile's full geometry with
// the pane under its centre (none there: revert), undoing only the reflow the drag caused.
let swapSnap: Record<string, Cell> | null = null;
let swapHover: string | null = null;

function snapshot(): void {
  swapSnap = {};
  for (const n of grid?.engine.nodes ?? []) { const w = widOf(n); if (w) swapSnap[w] = { x: n.x, y: n.y, w: n.w, h: n.h }; }
}

function swapTarget(el: GridItem | null): string | null {
  const dragged = el?.getAttribute('gs-id');
  const from = dragged ? swapSnap?.[dragged] : undefined;
  const node = el?.gridstackNode;
  if (!swapSnap || !from || !node) return null;
  const cx = node.x + from.w / 2;
  const cy = node.y + from.h / 2;
  for (const [wid, t] of Object.entries(swapSnap)) if (wid !== dragged && cx >= t.x && cx < t.x + t.w && cy >= t.y && cy < t.y + t.h) return wid;
  return null;
}

function setSwapHover(wid: string | null): void {
  if (swapHover && swapHover !== wid) gridItem(swapHover)?.classList.remove('swap-target');
  swapHover = wid;
  if (wid) gridItem(wid)?.classList.add('swap-target');
}

function doSwap(el: GridItem): void {
  setSwapHover(null);
  const snap = swapSnap;
  const target = swapTarget(el);
  swapSnap = null;
  const dragged = el.getAttribute('gs-id');
  const from = dragged ? snap?.[dragged] : undefined;
  if (!snap || !from || !el.gridstackNode) return;
  batch((g) => {
    for (const n of g.engine.nodes) {
      const w = widOf(n);
      const s = w ? snap[w] : undefined;
      if (n.el && s && w !== dragged && w !== target && (n.x !== s.x || n.y !== s.y || n.w !== s.w || n.h !== s.h)) g.update(n.el, s);
    }
    const to = target ? snap[target] : undefined;
    if (!to) return g.update(el, from);
    g.update(el, { ...to });
    const other = g.engine.nodes.find((n) => widOf(n) === target)?.el;
    if (other) g.update(other, { ...from });
  });
}

// ---- The fill --------------------------------------------------------------------------------
// How many rows fit between the stage top and the floor (the canvas content box, minus the dock
// with its margins; CMX-397's negative bottom margin sinks it into the padding), and the row
// height that fills exactly. CMX-414: NOT floored; a whole-px height strands px under the Wall.
function wallFill(): { total: number; cellPx: number } {
  const st = stage();
  const canvas = st?.closest('.canvas');
  const dock = document.getElementById('term-min-dock');
  let floor = window.innerHeight;
  if (canvas) floor = Math.min(floor, canvas.getBoundingClientRect().bottom) - (parseFloat(getComputedStyle(canvas).paddingBottom) || 0);
  let dockH = 0;
  if (!isPhone() && dock && !dock.hidden) {
    const ds = getComputedStyle(dock);
    dockH = dock.getBoundingClientRect().height + (parseFloat(ds.marginTop) || 0) + (parseFloat(ds.marginBottom) || 0);
  }
  const avail = Math.max(240, floor - (st ? st.getBoundingClientRect().top : 120) - dockH - 4);
  const total = Math.max(3, Math.floor(avail / WALL_CELL_H));
  return { total, cellPx: Math.max(40, avail / total) };
}

/** Lay the unpinned tiles out in `keyFn` order to fill the height (ties keep their x/y). A column
 * preset (rows 1) balances the panes over `cols` columns, each split evenly; a grid preset flows
 * row-major over cols x rows, the rest wrapping below. The last pane or row eats the remainder.
 * False when there is nothing to lay out. */
function fillByOrder(keyFn: (wid: string) => number, cols: number, nrows: number): boolean {
  if (!grid) return false;
  const { total, cellPx } = wallFill();
  grid.cellHeight(cellPx);
  const key = (n: Node) => keyFn(n.id ?? '');
  const nodes = grid.engine.nodes.filter((n) => !pinned(n.id)).sort((a, b) => key(a) - key(b) || a.x - b.x || a.y - b.y);
  const N = nodes.length;
  if (!N) return false;
  batch((g) => {
    const put = (n: Node | undefined, c: Cell) => n?.el && g.update(n.el, c);
    if (nrows === 1) {
      const c = Math.min(cols, N); // never an empty column
      const w = Math.max(1, Math.floor(12 / c));
      let idx = 0;
      for (let col = 0; col < c; col++) {
        const count = Math.floor(N / c) + (col < N % c ? 1 : 0);
        let y = 0;
        for (let r = 0; r < count; r++) {
          const h = r === count - 1 ? Math.max(3, total - y) : Math.max(3, Math.floor(total / count));
          put(nodes[idx++], { x: col * w, y, w, h });
          y += h;
        }
      }
      return;
    }
    const w = Math.max(1, Math.floor(12 / cols));
    const hBase = Math.max(3, Math.floor(total / nrows));
    const lastH = Math.max(3, total - (nrows - 1) * hBase);
    nodes.sort((a, b) => key(a) - key(b) || a.y - b.y || a.x - b.x);
    nodes.forEach((n, i) => {
      const r = Math.floor(i / cols);
      const y = r < nrows ? r * hBase : (nrows - 1) * hBase + lastH + (r - nrows) * hBase;
      put(n, { x: (i % cols) * w, y, w, h: r === nrows - 1 ? lastH : hBase });
    });
  });
  return true;
}

/** Apply the toolbar's preset in pc_pane_order and save it as the manual layout; also the refit
 * after a resize or a dock change. Auto-arrange and Focus own the layout while on (never saved). */
export function applyGridLayout(): void {
  if (!grid) return;
  const { preset, auto, focus } = wallLayout.get();
  if (focus || auto) {
    resetLayoutSigs();
    return focus ? applyWallFocus() : applyAutoArrange();
  }
  if (fillByOrder(paneOrderKey(), preset.cols, preset.rows)) persist();
}

const tiledWids = (): string[] => (grid?.engine.nodes ?? []).map((n) => n.id).filter((id): id is string => id != null && !pinned(id));

function ranked(tiled: ReadonlySet<string>): string[] {
  const relevant = rows().filter((a) => a.window_id && tiled.has(a.window_id));
  return rankOrder(relevant, Object.fromEntries(relevant.map((a) => [a.window_id as string, wantsHuman(a)])));
}

/** Auto-arrange: rank the tiles by attention and fill in that order; a no-op unless it is on and
 * the order changed. A tile missing from the poll (a gap, not yet dropped) sorts last. */
export function applyAutoArrange(): void {
  if (!grid || !wallLayout.get().auto) return;
  const order = ranked(new Set(grid.engine.nodes.map((n) => n.id ?? '')));
  if (order.join() === autoSig) return;
  autoSig = order.join();
  const { preset } = wallLayout.get();
  fillByOrder((wid) => (order.includes(wid) ? order.indexOf(wid) : Number.MAX_SAFE_INTEGER), preset.cols, preset.rows);
}

// Park every node in its own row far below, then place each: a restore or a focus layout often
// swaps cells, and moving one straight into a cell another holds makes Gridstack shove that one
// away. Both passes are one batch, so the parked spots never paint.
function parkThenPlace(nodes: Node[], cell: (n: Node) => Cell): void {
  batch((g) => {
    nodes.forEach((n, i) => n.el && g.update(n.el, { x: 0, y: 10000 + i * 2, w: 1, h: 1 }));
    nodes.forEach((n) => n.el && g.update(n.el, cell(n)));
  });
}

/** Put every unpinned tile with a saved spot back on it (auto or Focus turned off). */
export function restoreManualLayout(): void {
  const saved = savedLayout();
  const valid = (s: Partial<Cell> | undefined): s is Cell => !!s && Number.isInteger(s.x) && Number.isInteger(s.y) && (s.w ?? 0) > 0 && (s.h ?? 0) > 0;
  const nodes = (grid?.engine.nodes ?? []).filter((n) => !pinned(n.id) && valid(saved[n.id ?? '']));
  if (nodes.length) parkThenPlace(nodes, (n) => saved[n.id ?? ''] as Cell);
}

// ---- Focus: one pane large, the rest a strip -------------------------------------------------
/** The large pane: the click-focused one (the focus ring), else the first wanting a human, else the
 * first tiled. Pinned panes are never eligible. */
function pickFocus(): void {
  const tiled = tiledWids();
  const ringed = focusedPane()?.closest('.grid-stack-item')?.getAttribute('gs-id');
  const wanter = rows().find((a) => a.window_id && tiled.includes(a.window_id) && wantsHuman(a))?.window_id;
  focusWid = (ringed && tiled.includes(ringed) ? ringed : wanter) ?? tiled[0] ?? null;
}

/** Promote a strip pane to the large slot (a click on its header). */
export function promoteFocus(wid: string): void {
  if (wid === focusWid || pinned(wid)) return;
  focusWid = wid;
  focusSig = '';
  applyWallFocus();
}

function clearFocus(): void {
  focusWid = null;
  for (const n of grid?.engine.nodes ?? []) n.el?.classList.remove('wall-focus-main', 'wall-focus-strip');
  gsEl()?.classList.remove('wall-focus-on');
}

/** Lay Focus out (wallmodel focusLayout); a no-op unless it is on and the large pane or the strip's
 * needs-you-first order changed. A tile missing from the poll still gets a strip slot. */
export function applyWallFocus(): void {
  if (!grid || !wallLayout.get().focus) return;
  const tiled = tiledWids();
  if (!focusWid || !tiled.includes(focusWid)) pickFocus();
  const main = focusWid;
  if (!main) return;
  const order = ranked(new Set(tiled)).filter((w) => w !== main);
  for (const w of tiled) if (w !== main && !order.includes(w)) order.push(w);
  if (`${main}|${order.join()}` === focusSig) return;
  focusSig = `${main}|${order.join()}`;
  const { total, cellPx } = wallFill();
  grid.cellHeight(cellPx);
  const layout = focusLayout([main, ...order], main, total);
  const at = (n: Node) => layout[n.id ?? ''];
  parkThenPlace(grid.engine.nodes.filter(at), (n) => at(n) as Cell);
  for (const n of grid.engine.nodes) {
    n.el?.classList.toggle('wall-focus-main', n.id === main);
    n.el?.classList.toggle('wall-focus-strip', !!at(n) && n.id !== main);
  }
  gsEl()?.classList.add('wall-focus-on');
}

/** Auto-arrange and Focus compute the layout, so a manual move or resize is off while either is on;
 * lock turns resize off (a drag swaps). Called after every build, so a reload keeps the feel. */
export function applyWallInteractivity(): void {
  const { locked, auto, focus } = wallLayout.get();
  grid?.enableMove(!auto && !focus);
  grid?.enableResize(!locked && !auto && !focus);
}

/** A fresh tile set: the next auto-arrange and Focus apply unconditionally. */
export function resetLayoutSigs(): void {
  autoSig = '';
  focusSig = '';
}

// The toolbar changed the layout: apply it now. Turning Auto or Focus ON lays it out at once;
// turning both OFF restores pc_wall_layout. A preset is a NEW object on every choice (choosing the
// active one again re-tidies a dragged wall); lock only changes what a drag may do.
let seen = wallLayout.get();
wallLayout.subscribe(() => {
  const prev = seen;
  const next = (seen = wallLayout.get());
  if (prev.focus && !next.focus) clearFocus();
  if (prev.auto !== next.auto || prev.focus !== next.focus) resetLayoutSigs();
  applyWallInteractivity();
  if (!prev.focus && next.focus) { pickFocus(); applyWallFocus(); }
  else if (!prev.auto && next.auto) applyAutoArrange();
  else if ((prev.auto || prev.focus) && !next.auto && !next.focus) restoreManualLayout();
  else if (prev.preset !== next.preset) applyGridLayout();
});
