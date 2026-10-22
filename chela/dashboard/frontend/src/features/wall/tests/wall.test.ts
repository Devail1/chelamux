// CMX-247 (CMX-212 P33): the Wall's modes, tiles, minimize/restore, the reconcile and the last
// viewed pane, over a fake Gridstack (setup.ts).
import { beforeEach, expect, test, vi } from 'vitest';
import { BOOT } from '../../../shared/api/index.ts';
import { dispatch } from '../../../shared/events/index.ts';
import { agents } from '../../../shared/store/agents.ts';
import { view } from '../../../shared/store/view.ts';
import { minimizedPanes, pinnedPanes, setMinimized, setPinned, termMode } from '../../../shared/store/wall.ts';
import { shareActivePane } from '../../share/index.ts';
import {
  absorbFreshTerminals, activePane, adoptSidebarDefault, configureWall, dropTerminalPane, focusPaneByWid, isWallVisible, label, minimizePane,
  renderedWids, renderTerminals, restoreFromDock, setTermMode, showPane, stepAgentMobile, termTick,
} from '../index.ts';
import { act } from '@testing-library/react';
import { closeShareDialog } from '../../share/index.ts';
import { agent, calls, freshGrid, gs, ready, serve, settle, setPhone, tile, tiles } from './setup.ts';

const host = { selectView: vi.fn(), tabSignal: vi.fn() };
configureWall(host);
beforeEach(() => {
  host.selectView.mockClear();
  host.tabSignal.mockClear();
  Element.prototype.scrollIntoView = vi.fn(); // jsdom does no layout
});

/** The Wall on screen over `rows` (each one ready), in `m` mode ('phone': a phone's width). A
 * wall of one throwaway window first, so no earlier test's render can match this one. */
async function show(rows: ReturnType<typeof agent>[], m = 'wall') {
  const seeded = { ...localStorage }; // what the test stored: the throwaway wall must not overwrite it
  setPhone(false);
  ready('@reset');
  agents.set([agent('@reset')]);
  setTermMode('wall');
  await settle();
  localStorage.clear();
  for (const [k, v] of Object.entries(seeded)) localStorage.setItem(k, String(v));
  freshGrid();
  serve((p) => (p.endsWith('/api/agents') ? rows : p.endsWith('/api/agents/context') ? [] : {}));
  ready(...rows.map((a) => a.window_id));
  agents.set(rows);
  setPhone(m === 'phone');
  setTermMode(m === 'phone' ? 'wall' : m);
  await settle();
}
const slots = (wid: string) => [...(tile(wid)?.querySelectorAll('.island-slot') ?? [])].map((s) => [...s.attributes].filter((a) => a.name.startsWith('data-')).map((a) => `${a.name}=${a.value}`).join(' '));
const pane = () => document.querySelector('#term-stage .term-pane [data-head-for]')?.getAttribute('data-head-for') ?? null;

test('the wall draws one tile per ONLINE window, keyed by its id, with its four slots in order', async () => {
  await show([agent('@1'), agent('@2'), agent('@3', { online: false })]);
  expect(tiles()).toEqual(['@1', '@2']);
  expect(renderedWids).toEqual(['@1', '@2']);
  expect(slots('@1')).toEqual(['data-head-for=@1 data-head-wall=1', 'data-recap-slot=@1', 'data-frame-for=@1', 'data-foot-for=@1 data-foot-wall=1']);
  expect(tile('@1')?.querySelector('.gs-head')).not.toBeNull(); // the React header drew BEFORE Gridstack adopted it
  expect(document.querySelector('#term-stage iframe.term-frame')).not.toBeNull();
  expect(termMode.get()).toBe('wall');
  expect(gs.init).toHaveBeenCalledTimes(1);
  expect(gs.init.mock.calls[0]?.[0]).toEqual({
    column: 12, cellHeight: 70, margin: 6, float: true, handle: '.gs-grip',
    draggable: { handle: '.gs-grip', scroll: false }, resizable: { handles: 'e, se, s, sw, w' }, columnOpts: { breakpoints: [{ w: 768, c: 1 }] },
  });
});

test('a tile with no saved spot goes two to a row, 6x5; a saved one keeps its spot', async () => {
  localStorage.setItem('pc_wall_layout', JSON.stringify({ '@3': { x: 3, y: 7, w: 4, h: 2 } }));
  await show([agent('@1'), agent('@2'), agent('@3'), agent('@4')]);
  expect(gs.adopted).toEqual({ '@1': { x: 0, y: 0, w: 6, h: 5 }, '@2': { x: 6, y: 0, w: 6, h: 5 }, '@3': { x: 3, y: 7, w: 4, h: 2 }, '@4': { x: 6, y: 5, w: 6, h: 5 } });
});

test('a corrupt saved layout is no layout: the tiles take their default spots', async () => {
  localStorage.setItem('pc_wall_layout', '{nope');
  await show([agent('@1')]);
  expect(gs.adopted['@1']).toEqual({ x: 0, y: 0, w: 6, h: 5 });
});

test('a pinned pane is drawn pinned', async () => {
  setPinned('@2', true);
  await show([agent('@1'), agent('@2')]);
  expect(tile('@2')?.classList.contains('pane-pinned')).toBe(true);
  expect(tile('@1')?.classList.contains('pane-pinned')).toBe(false);
  setPinned('@2', false);
  expect(tile('@2')?.classList.contains('pane-pinned')).toBe(false); // the outline follows the store
});

test('a render with the same window set keeps every tile and iframe (no reload)', async () => {
  await show([agent('@1'), agent('@2')]);
  const before = document.querySelector('#term-stage iframe');
  agents.set([agent('@2'), agent('@1', { name: 'renamed' })]); // reordered and renamed: no change by wid
  await renderTerminals();
  expect(document.querySelector('#term-stage iframe')).toBe(before);
  expect(gs.init).toHaveBeenCalledTimes(1);
});

test('no window: "No active agents" and no grid', async () => {
  await show([]);
  expect(document.getElementById('term-stage')?.textContent).toBe('No active agents');
  expect(document.getElementById('term-stage')?.className).toBe('');
  expect(renderedWids).toEqual([]);
});

test('an empty agent cache is refetched; an error body is no pane list', async () => {
  await show([agent('@1')]);
  serve((p) => (p.endsWith('/api/agents') ? { error: 'boom' } : {}));
  agents.set([]);
  await renderTerminals();
  expect(calls.filter((c) => c.path.endsWith('/api/agents')).length).toBeGreaterThan(0);
  expect(agents.get()).toEqual([]);
  expect(document.getElementById('term-stage')?.textContent).toBe('No active agents');
});

test('a phone is always single mode, one pane and no grid; the choice is not saved', async () => {
  await show([agent('@1'), agent('@2')], 'phone');
  expect(termMode.get()).toBe('single');
  expect(document.getElementById('term-stage')?.className).toBe('term-single');
  expect(document.querySelectorAll('#term-stage .term-pane')).toHaveLength(1);
  expect(tiles()).toEqual([]);
  expect(localStorage.getItem('pc_term_mode')).toBe('wall');
  const p = document.querySelector('#term-stage .term-pane');
  expect([...(p?.querySelectorAll('.island-slot') ?? [])].map((s) => s.getAttribute('data-head-wall') ?? s.getAttribute('data-foot-wall'))).toEqual([null, null, null]);
  expect(p?.querySelector('[data-recap-slot]')).toBeNull();
});

test('single mode remembers the pane it opens, under pc_term_last_wid with its session id and name', async () => {
  await show([agent('@1'), agent('@2')], 'single');
  expect(activePane()).toBe('@1');
  expect(pane()).toBe('@1');
  await showPane('@2');
  expect(pane()).toBe('@2');
  expect(renderedWids).toEqual(['@2']);
  expect(JSON.parse(localStorage.getItem('pc_term_last_wid') ?? 'null')).toEqual({ wid: '@2', session_id: 's@2', name: 'n@2' });
});

test('setTermMode saves the mode and re-renders in it', async () => {
  await show([agent('@1')]);
  setTermMode('single');
  await settle();
  expect(localStorage.getItem('pc_term_mode')).toBe('single');
  expect(document.querySelector('#term-stage .term-pane')).not.toBeNull();
  expect(isWallVisible()).toBe(false);
  setTermMode('wall');
  await settle();
  expect(tiles()).toEqual(['@1']);
  expect(isWallVisible()).toBe(true);
  view.set('work');
  expect(isWallVisible()).toBe(false);
});

test('minimize pulls the tile out of the grid but keeps it (and its iframe) in the page; restore puts it back', async () => {
  await show([agent('@1'), agent('@2')]);
  const frame = tile('@1')?.querySelector('iframe');
  minimizePane('@1');
  const t = tile('@1');
  expect(gs.calls).toContainEqual(['removeWidget', '@1', false]);
  expect(t?.style.display).toBe('none');
  expect(t?.dataset.minimized).toBe('1');
  expect(JSON.parse(t?.dataset.gsCoords ?? '{}')).toEqual({ x: 0, y: 0, w: 6, h: 10 });
  expect(minimizedPanes.get().has('@1')).toBe(true);
  expect(gs.nodes().map((n) => n.id)).toEqual(['@2']);
  restoreFromDock('@1');
  expect(t?.getAttribute('style')).toBeNull();
  expect(t?.dataset.minimized).toBeUndefined();
  expect(gs.calls).toContainEqual(['makeWidget', '@1']);
  expect(minimizedPanes.get().has('@1')).toBe(false);
  expect(tile('@1')?.querySelector('iframe')).toBe(frame);
});

test('restore puts a tile back on the spot it was minimized from', async () => {
  await show([agent('@1')]);
  const t = tile('@1');
  minimizePane('@1');
  if (t) t.dataset.gsCoords = JSON.stringify({ x: 2, y: 4, w: 3, h: 6 });
  restoreFromDock('@1');
  expect(gs.adopted['@1']).toEqual({ x: 2, y: 4, w: 3, h: 6 });
});

test('restore with unreadable coords still re-adopts the tile', async () => {
  await show([agent('@1')]);
  minimizePane('@1');
  const t = tile('@1');
  if (t) t.dataset.gsCoords = '{bad';
  restoreFromDock('@1');
  expect(gs.calls.filter((c) => c[0] === 'makeWidget')).toEqual([['makeWidget', '@1']]);
});

test('a pane minimized on the last visit opens minimized', async () => {
  setMinimized('@2', true);
  await show([agent('@1'), agent('@2')]);
  expect(tile('@2')?.style.display).toBe('none');
  expect(gs.nodes().map((n) => n.id)).toEqual(['@1']);
});

test('a dropped wall pane leaves the grid and the page; a minimized one is removed directly', async () => {
  await show([agent('@1'), agent('@2'), agent('@3')]);
  minimizePane('@3');
  dropTerminalPane('@1');
  expect(gs.calls).toContainEqual(['removeWidget', '@1', true]);
  expect(tile('@1')).toBeNull();
  dropTerminalPane('@3');
  expect(gs.calls.filter((c) => c[1] === '@3' && c[0] === 'removeWidget')).toEqual([['removeWidget', '@3', false]]); // only its minimize
  expect(tile('@3')).toBeNull();
  expect(minimizedPanes.get().has('@3')).toBe(false);
  expect(renderedWids).toEqual(['@2']);
  agents.set([agent('@2')]);
  await renderTerminals(); // the drop is the new baseline: no rebuild of the survivor
  expect(gs.init).toHaveBeenCalledTimes(1);
});

test("a single pane's dead session shows a note instead of a blank frame", async () => {
  await show([agent('@1')], 'single');
  dropTerminalPane('@1');
  expect(document.querySelector('#term-stage [data-frame-for]')).toBeNull();
  expect(document.querySelector('#term-stage .pane-ended')?.textContent).toBe('n@1: session ended');
  dropTerminalPane('@1');
  expect(document.querySelectorAll('#term-stage .pane-ended')).toHaveLength(1);
});

test('a new window is appended below the lowest tile without touching the others', async () => {
  await show([agent('@1'), agent('@2')]);
  const keep = tile('@1');
  ready('@3', '@4');
  localStorage.setItem('pc_wall_layout', JSON.stringify({ '@4': { x: 6, y: 1, w: 3, h: 2 } }));
  await absorbFreshTerminals(['@1', '@2', '@3', '@4']);
  expect(tiles()).toEqual(['@1', '@2', '@3', '@4']);
  expect(tile('@1')).toBe(keep);
  expect(gs.init).toHaveBeenCalledTimes(1);
  expect(gs.adopted['@3']).toEqual({ x: 0, y: 10, w: 6, h: 5 });
  expect(gs.adopted['@4']).toEqual({ x: 6, y: 1, w: 3, h: 2 });
  expect(tile('@3')?.querySelector('.gs-head')).not.toBeNull();
  expect(renderedWids).toEqual(['@1', '@2', '@3', '@4']);
});

test('single mode absorbs nothing; nor does a suspended (hidden) wall', async () => {
  await show([agent('@1')], 'single');
  await absorbFreshTerminals(['@1', '@2']);
  expect(renderedWids).toEqual(['@1']);
});

test('the 4 s tick drops vanished windows, absorbs new ones and reads the context', async () => {
  await show([agent('@1'), agent('@2')]);
  ready('@3');
  serve((p) => (p.endsWith('/api/agents') ? [agent('@2'), agent('@3')] : p.endsWith('/api/agents/context') ? [{ window_id: '@2', used_pct: 40 }] : {}));
  calls.length = 0;
  await termTick();
  expect(tiles()).toEqual(['@2', '@3']);
  const paths = calls.map((c) => c.path);
  expect(paths.indexOf('/api/agents')).toBeGreaterThan(-1);
  expect(paths.indexOf('/api/agents/context')).toBeGreaterThan(paths.indexOf('/api/agents'));
  expect(host.tabSignal).toHaveBeenLastCalledWith([agent('@2'), agent('@3')]);
});

test('the tick does nothing off the Wall, and a failed read changes nothing', async () => {
  await show([agent('@1')]);
  view.set('work');
  calls.length = 0;
  await termTick();
  expect(calls).toEqual([]);   // not even the agents read
  view.set('terminals');
  vi.stubGlobal('fetch', () => Promise.reject(new Error('offline')));
  await termTick();
  expect(tiles()).toEqual(['@1']);
});

test('the windows event reconciles by window id; off the Wall it is ignored', async () => {
  await show([agent('@1'), agent('@2')]);
  serve((p) => (p.endsWith('/api/agents') ? [agent('@2')] : {}));
  view.set('work');
  calls.length = 0;
  dispatch('windows', '{}');
  await settle();
  expect(calls.filter((c) => c.path === '/api/agents')).toEqual([]);
  view.set('terminals');
  dispatch('windows', '{}');
  await settle();
  expect(tiles()).toEqual(['@2']);
});

test('focusing a pane switches to the Wall, restores it from the dock and flashes it', async () => {
  await show([agent('@1'), agent('@2')]);
  vi.useFakeTimers();
  minimizePane('@2');
  host.selectView.mockClear();
  focusPaneByWid('@2');
  expect(host.selectView.mock.calls).toEqual([['terminals']]);
  expect(JSON.parse(localStorage.getItem('pc_term_last_wid') ?? '{}').wid).toBe('@2');
  await vi.advanceTimersByTimeAsync(60);
  expect(minimizedPanes.get().has('@2')).toBe(false);
  const content = tile('@2')?.querySelector('.grid-stack-item-content');
  expect(content?.classList.contains('pane-flash')).toBe(true);
  await vi.advanceTimersByTimeAsync(1100);
  expect(content?.classList.contains('pane-flash')).toBe(false);
});

test('in single mode a focus opens that pane', async () => {
  await show([agent('@1'), agent('@2')], 'single');
  focusPaneByWid('@2');
  await settle();
  expect(pane()).toBe('@2');
});

test('the header swipe steps the single pane in the sidebar order, wrapping', async () => {
  await show([agent('@1'), agent('@2'), agent('@3')], 'phone');
  stepAgentMobile(1);
  expect(activePane()).toBe('@2');
  stepAgentMobile(-1);
  stepAgentMobile(-1);
  expect(activePane()).toBe('@3');
  await settle();
  const head = document.createElement('span');
  head.className = 'gs-head';
  document.querySelector('#term-stage .term-pane')?.append(head);
  const touch = (type: string, x: number, y: number) => {
    const e = new Event(type, { bubbles: true });
    Object.assign(e, type === 'touchstart' ? { touches: [{ clientX: x, clientY: y }] } : { changedTouches: [{ clientX: x, clientY: y }] });
    head.dispatchEvent(e);
  };
  touch('touchstart', 200, 10);
  touch('touchend', 100, 20); // left: the next one
  expect(activePane()).toBe('@1');
  touch('touchstart', 100, 10);
  touch('touchend', 140, 10); // under 50 px: no step
  expect(activePane()).toBe('@1');
});

test('one pane: the swipe has nowhere to go', async () => {
  await show([agent('@1')], 'single');
  stepAgentMobile(1);
  expect(activePane()).toBe('@1');
});

test('adoptSidebarDefault keeps a pane the stored choice opened', async () => {
  localStorage.setItem('pc_term_last_wid', JSON.stringify({ wid: '@2', session_id: 's@2', name: 'n@2' }));
  await show([agent('@1'), agent('@2')], 'single');
  adoptSidebarDefault();
  expect(activePane()).toBe('@2');
});

test('the label is the shared display label; an unknown window reads as its id', async () => {
  await show([agent('@1', { name: 'shell' })]);
  expect(label('@9')).toBe('@9');
  expect(label('@1')).not.toBe('');
});

test('the gear menu shares the open pane, and says so when there is none', async () => {
  await show([agent('@1')], 'single');
  calls.length = 0;
  expect(shareActivePane()).toBe(true);
  await settle();
  expect(calls.map((c) => c.path)).toContain('/api/term/%401/share-info');
  act(() => closeShareDialog());
  await show([]);
  calls.length = 0;
  expect(shareActivePane()).toBe(false);
  expect(calls).toEqual([]);
});

test('with terminals off nothing renders', async () => {
  BOOT.terminalsEnabled = false;
  try {
    agents.set([agent('@1')]);
    await renderTerminals();
    focusPaneByWid('@1');
    expect(host.selectView).not.toHaveBeenCalledWith('terminals');
    expect(document.getElementById('term-stage')?.children).toHaveLength(0);
  } finally {
    BOOT.terminalsEnabled = true;
  }
});

test('pinned panes stay pinned through a rebuild', async () => {
  pinnedPanes.set(new Set(['@1']));
  await show([agent('@1')]);
  expect(tile('@1')?.classList.contains('pane-pinned')).toBe(true);
});
