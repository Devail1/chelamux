// CMX-247 (CMX-212 P33): the grid's layouts, driven the way the page drives them: the toolbar's
// store setters and Gridstack's drag events, over a fake Gridstack (setup.ts). jsdom does no
// layout, so the stage sits at the top of a 768 px viewport: the fill is 10 rows of 76.4 px.
import { afterAll, beforeAll, beforeEach, describe, expect, test, vi } from 'vitest';
import css from '../../../../../static/style.css?raw';
import { agents } from '../../../shared/store/agents.ts';
import { setPinned, setWallAuto, setWallFocus, setWallLocked, setWallPreset, wallLayout } from '../../../shared/store/wall.ts';
import { configureWall, renderTerminals, setTermMode, termTick } from '../index.ts';
import { agent, freshGrid, gs, ready, saved, serve, settle, tile, type Cell } from './setup.ts';

configureWall({ selectView() {}, tabSignal() {} });

/** A wall over `rows` (each ready), from the saved `layout` (none by default). A wall of one
 * throwaway window first, so no earlier test's render can match this one; the grid before it
 * (an earlier test's) may have saved its layout on a preset change, so that is dropped. */
async function wall(rows: ReturnType<typeof agent>[], layout?: Record<string, Cell>) {
  ready('@reset');
  agents.set([agent('@reset')]);
  setTermMode('wall');
  await settle();
  localStorage.removeItem('pc_wall_layout');
  if (layout) localStorage.setItem('pc_wall_layout', JSON.stringify(layout));
  freshGrid();
  serve((p) => (p.endsWith('/api/agents') ? rows : {}));
  ready(...rows.map((a) => a.window_id));
  agents.set(rows);
  await renderTerminals();
}
const cells = (...wids: string[]) => Object.fromEntries(wids.map((w) => [w, gs.cell(w)]));
const c = (x: number, y: number, w: number, h: number): Cell => ({ x, y, w, h });
const last = (name: string) => gs.calls.filter((x) => x[0] === name).at(-1)?.[1];

beforeEach(() => {
  localStorage.setItem('pc_pane_order', '[]');
});

test('the default preset (2 columns) fills the height: the first column splits, the last pane eats the rest', async () => {
  await wall([agent('@1'), agent('@2'), agent('@3')]);
  // unordered panes keep their x, then y: @1 (0,0) and @3 (0,5) share the first column
  expect(cells('@1', '@2', '@3')).toEqual({ '@1': c(0, 0, 6, 5), '@3': c(0, 5, 6, 5), '@2': c(6, 0, 6, 10) });
  expect(last('cellHeight')).toBeCloseTo(76.4);
  expect(saved()).toEqual({ '@1': c(0, 0, 6, 5), '@3': c(0, 5, 6, 5), '@2': c(6, 0, 6, 10) }); // a preset IS a manual layout
});

test('a column preset never opens an empty column', async () => {
  setWallPreset({ cols: 3, rows: 1 });
  await wall([agent('@1'), agent('@2')]);
  expect(cells('@1', '@2')).toEqual({ '@1': c(0, 0, 6, 10), '@2': c(6, 0, 6, 10) });
});

test('a grid preset flows row-major; panes past cols x rows wrap below', async () => {
  setWallPreset({ cols: 2, rows: 2 });
  await wall([agent('@1'), agent('@2'), agent('@3'), agent('@4'), agent('@5')]);
  expect(cells('@1', '@2', '@3', '@5')).toEqual({ '@1': c(0, 0, 6, 5), '@2': c(6, 0, 6, 5), '@3': c(0, 5, 6, 5), '@5': c(0, 10, 6, 5) });
});

test('a 3-row grid gives the last row the remainder', async () => {
  setWallPreset({ cols: 1, rows: 3 });
  await wall([agent('@1'), agent('@2'), agent('@3')]);
  expect(cells('@1', '@2', '@3')).toEqual({ '@1': c(0, 0, 12, 3), '@2': c(0, 3, 12, 3), '@3': c(0, 6, 12, 4) });
});

test('pc_pane_order orders the fill', async () => {
  localStorage.setItem('pc_pane_order', JSON.stringify(['@2', '@1']));
  await wall([agent('@1'), agent('@2')]);
  expect(cells('@1', '@2')).toEqual({ '@2': c(0, 0, 6, 10), '@1': c(6, 0, 6, 10) });
});

test('a pinned pane sits out of the fill', async () => {
  setPinned('@1', true);
  await wall([agent('@1'), agent('@2'), agent('@3')], { '@1': c(2, 20, 3, 3) });
  expect(cells('@1', '@2', '@3')).toEqual({ '@1': c(2, 20, 3, 3), '@3': c(0, 0, 6, 10), '@2': c(6, 0, 6, 10) }); // ties keep their x
  setPinned('@1', false);
});

test('choosing a preset re-tidies at once, the active one again included', async () => {
  await wall([agent('@1'), agent('@2')]);
  setWallPreset({ cols: 1, rows: 2 });
  expect(cells('@1', '@2')).toEqual({ '@1': c(0, 0, 12, 5), '@2': c(0, 5, 12, 5) });
  gs.calls.length = 0;
  setWallPreset({ cols: 1, rows: 2 });
  expect(gs.calls.some((x) => x[0] === 'cellHeight')).toBe(true);
});

test('the dock below the stage takes its margin box out of the fill', async () => {
  await wall([agent('@1')]);
  const dock = document.getElementById('term-min-dock') as HTMLElement;
  dock.hidden = false;
  dock.style.marginTop = '10px';
  dock.getBoundingClientRect = () => ({ height: 100 }) as DOMRect;
  setWallPreset({ cols: 1, rows: 1 });
  expect(last('cellHeight')).toBeCloseTo(654 / 9); // 768 - 110 - 4 = 654 px, 9 rows
});

test('the canvas floor and its bottom padding bound the fill', async () => {
  await wall([agent('@1')]);
  const canvas = document.createElement('main');
  canvas.className = 'canvas';
  canvas.style.paddingBottom = '20px';
  canvas.getBoundingClientRect = () => ({ bottom: 600 }) as DOMRect;
  const stage = document.getElementById('term-stage') as HTMLElement;
  stage.parentElement?.replaceWith(canvas);
  canvas.append(stage);
  setWallPreset({ cols: 1, rows: 1 });
  expect(last('cellHeight')).toBeCloseTo(576 / 8); // 600 - 20 - 4 = 576 px, 8 rows
});

test('a drag and a resize save the layout; a drag hides the frames and pins the page while it runs', async () => {
  await wall([agent('@1'), agent('@2')]);
  localStorage.removeItem('pc_wall_layout');
  const grid = document.querySelector('#term-stage .grid-stack') as HTMLElement;
  gs.fire('dragstart', tile('@1') as HTMLElement);
  expect([grid.className, document.body.className]).toEqual(['grid-stack wall-movable gs-dragging term-dragging', 'term-dragging']);
  gs.fire('dragstop', tile('@1') as HTMLElement);
  expect([grid.className, document.body.className]).toEqual(['grid-stack wall-movable', '']);
  expect(saved()).toEqual({ '@1': c(0, 0, 6, 10), '@2': c(6, 0, 6, 10) });
  localStorage.removeItem('pc_wall_layout');
  gs.fire('resizestart');
  expect(grid.classList.contains('gs-dragging')).toBe(true);
  expect(document.body.classList.contains('term-dragging')).toBe(false);
  gs.fire('resizestop');
  expect(grid.classList.contains('gs-dragging')).toBe(false);
  expect(saved()).toEqual({ '@1': c(0, 0, 6, 10), '@2': c(6, 0, 6, 10) });
});

test('lock turns resize off, and a drag dropped on another pane swaps the two', async () => {
  setWallPreset({ cols: 2, rows: 2 });
  await wall([agent('@1'), agent('@2'), agent('@3')]);
  setWallLocked(true);
  expect([last('enableMove'), last('enableResize')]).toEqual([true, false]);
  const t1 = tile('@1') as HTMLElement & { gridstackNode: Cell };
  gs.fire('dragstart', t1);
  const grid = document.querySelector('#term-stage .grid-stack') as HTMLElement;
  expect(grid.classList.contains('term-lock-drag')).toBe(true);
  Object.assign(t1.gridstackNode, { x: 6, y: 0 }); // the drag carried @1 over @2
  gs.fire('drag', t1);
  expect(tile('@2')?.classList.contains('swap-target')).toBe(true);
  gs.fire('dragstop', t1);
  expect(cells('@1', '@2', '@3')).toEqual({ '@1': c(6, 0, 6, 5), '@2': c(0, 0, 6, 5), '@3': c(0, 5, 6, 5) });
  expect(tile('@2')?.classList.contains('swap-target')).toBe(false);
  expect(grid.classList.contains('term-lock-drag')).toBe(false);
  setWallLocked(false);
});

test('a locked drag dropped on empty space snaps back, and undoes the reflow it caused', async () => {
  setWallPreset({ cols: 2, rows: 2 });
  await wall([agent('@1'), agent('@2'), agent('@3')]);
  setWallLocked(true);
  const t1 = tile('@1') as HTMLElement & { gridstackNode: Cell };
  gs.fire('dragstart', t1);
  Object.assign(t1.gridstackNode, { x: 6, y: 5 }); // the empty cell
  const n3 = gs.nodes().find((n) => n.id === '@3');
  if (n3) n3.y = 7; // the drag nudged @3
  gs.fire('dragstop', t1);
  expect(cells('@1', '@3')).toEqual({ '@1': c(0, 0, 6, 5), '@3': c(0, 5, 6, 5) });
  setWallLocked(false);
});

test('an unlocked drag never swaps', async () => {
  setWallPreset({ cols: 2, rows: 2 });
  await wall([agent('@1'), agent('@2')]);
  const t1 = tile('@1') as HTMLElement & { gridstackNode: Cell };
  gs.fire('dragstart', t1);
  Object.assign(t1.gridstackNode, { x: 6, y: 0 });
  gs.fire('drag', t1);
  expect(tile('@2')?.classList.contains('swap-target')).toBe(false);
  gs.fire('dragstop', t1);
  expect(gs.cell('@2')).toEqual(c(6, 0, 6, 5));
});

test('auto-arrange ranks a pane that wants you first, never saves, and restores the manual layout when it turns off', async () => {
  await wall([agent('@1'), agent('@2'), agent('@3')]);
  const manual = saved();
  setWallAuto(true);
  expect([last('enableMove'), last('enableResize')]).toEqual([false, false]);
  const waiting = [agent('@1'), agent('@2'), agent('@3', { session_status: 'waiting' })];
  serve((p) => (p.endsWith('/api/agents') ? waiting : {}));
  await termTick(); // the status pass re-ranks
  expect(cells('@3', '@1', '@2')).toEqual({ '@3': c(0, 0, 6, 5), '@1': c(0, 5, 6, 5), '@2': c(6, 0, 6, 10) });
  expect(saved()).toEqual(manual);
  gs.calls.length = 0;
  await termTick(); // the same order: no reflow
  expect(gs.calls.filter((x) => x[0] === 'batchUpdate')).toEqual([]);
  setWallAuto(false);
  expect(cells('@1', '@2', '@3')).toEqual(manual);
  expect([last('enableMove'), last('enableResize')]).toEqual([true, true]);
});

test('Focus puts the pane that wants you large and the rest in a strip; a header click promotes a strip pane', async () => {
  await wall([agent('@1'), agent('@2', { needs_human: true }), agent('@3')]);
  const manual = saved();
  setWallFocus(true);
  expect(cells('@1', '@2', '@3')).toEqual({ '@2': c(0, 0, 8, 10), '@1': c(8, 0, 4, 5), '@3': c(8, 5, 4, 5) });
  expect(tile('@2')?.classList.contains('wall-focus-main')).toBe(true);
  expect(tile('@1')?.classList.contains('wall-focus-strip')).toBe(true);
  expect(document.querySelector('#term-stage .grid-stack')?.classList.contains('wall-focus-on')).toBe(true);
  expect(saved()).toEqual(manual);
  const head = document.createElement('div');
  head.className = 'gs-head';
  tile('@3')?.append(head);
  head.click();
  expect(gs.cell('@3')).toEqual(c(0, 0, 8, 10));
  const btn = document.createElement('button');
  head.append(btn);
  tile('@1')?.append(Object.assign(document.createElement('div'), { className: 'gs-head' }));
  btn.click(); // a header button keeps its own meaning
  expect(gs.cell('@3')).toEqual(c(0, 0, 8, 10));
  setWallFocus(false);
  expect(cells('@1', '@2', '@3')).toEqual(manual);
  expect(tile('@3')?.classList.contains('wall-focus-main')).toBe(false);
  expect(document.querySelector('#term-stage .grid-stack')?.classList.contains('wall-focus-on')).toBe(false);
});

test('Focus with no one waiting takes the first pane; auto and Focus exclude each other', async () => {
  await wall([agent('@1'), agent('@2')]);
  setWallAuto(true);
  setWallFocus(true);
  expect(wallLayout.get()).toMatchObject({ auto: false, focus: true });
  expect(cells('@1', '@2')).toEqual({ '@1': c(0, 0, 8, 10), '@2': c(8, 0, 4, 10) });
  setWallFocus(false);
});

// CMX-329 (§7 10-24): the header shows the move cursor only where Gridstack can move a tile;
// in Focus a strip pane's header is a pointer (a click promotes it), the large pane's and Auto's
// the default. The title keeps the default too: it renames on a double-click, it is not a link.
// style.css is parsed once, in beforeAll (CMX-311: ~2 s under jsdom).
describe('under style.css', () => {
  let style: HTMLStyleElement;
  beforeAll(() => {
    style = document.createElement('style');
    style.textContent = css;
    document.head.append(style);
  });
  afterAll(() => style.remove());

  const cursors = (wid: string) => {
    const head = tile(wid)?.querySelector('.gs-head') as HTMLElement;
    const grip = head.querySelector('.gs-grip') as HTMLElement;
    const title = grip.querySelector('.pane-title') as HTMLElement;
    return [head, grip, title].map((el) => getComputedStyle(el).cursor);
  };

  test('the move cursor shows only where a tile can move', async () => {
    await wall([agent('@1'), agent('@2', { needs_human: true })]);
    expect(last('enableMove')).toBe(true);
    expect(cursors('@1')).toEqual(['auto', 'move', 'move']);
    setWallLocked(true); // a locked drag still moves (it swaps)
    expect(last('enableMove')).toBe(true);
    expect(cursors('@1')).toEqual(['auto', 'move', 'move']);
    setWallLocked(false);
    setWallAuto(true);
    expect(last('enableMove')).toBe(false);
    expect(cursors('@1')).toEqual(['auto', 'auto', 'default']);
    setWallAuto(false);
    setWallFocus(true);
    expect(last('enableMove')).toBe(false);
    expect(tile('@1')?.classList.contains('wall-focus-strip')).toBe(true);
    expect(cursors('@1')).toEqual(['pointer', 'pointer', 'default']); // a click brings it forward
    expect(cursors('@2')).toEqual(['auto', 'auto', 'default']); // the large pane: nothing to promote
    tile('@1')?.querySelector<HTMLElement>('.gs-head')?.click();
    expect(tile('@1')?.classList.contains('wall-focus-main')).toBe(true);
    setWallFocus(false);
    expect(last('enableMove')).toBe(true);
    expect(cursors('@1')).toEqual(['auto', 'move', 'move']);
  });
});

test('a resize refits the wall, debounced', async () => {
  await wall([agent('@1')]);
  vi.useFakeTimers();
  gs.calls.length = 0;
  window.dispatchEvent(new Event('resize'));
  window.dispatchEvent(new Event('resize'));
  await vi.advanceTimersByTimeAsync(199);
  expect(gs.calls.filter((x) => x[0] === 'cellHeight')).toEqual([]);
  await vi.advanceTimersByTimeAsync(1);
  expect(gs.calls.filter((x) => x[0] === 'cellHeight')).toHaveLength(1);
});
