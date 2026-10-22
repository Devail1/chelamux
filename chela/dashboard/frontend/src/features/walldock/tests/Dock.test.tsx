// CMX-242 (CMX-212 P28): the Minimized tray. Ported from walldock, dispatched_progress_icons
// and judge_battery_badge (the chip's dot) .test.mjs, plus Liav's 10-19 ask: ONE row that
// scrolls sideways, with a count, edge fades and a sideways wheel.
import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, test, vi } from 'vitest';
import css from '../../../../../static/style.css?raw';
import { agents } from '../../../shared/store/agents.ts';
import { minimizedPanes, setMinimized, termMode } from '../../../shared/store/wall.ts';
import type { HeadAgent } from '../../panehead/index.ts';
import { configureDock, Dock, dockWids, mountDock, refreshDock, runLazyDock, forgetAutoDock, stampActivity } from '../index.ts';

beforeEach(() => {
  localStorage.clear();
  agents.set([]);
  minimizedPanes.set(new Set());
  termMode.set('wall');
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const by = (rows: HeadAgent[]) => new Map(rows.map((a) => [a.window_id as string, a]));
const chips = (root: ParentNode) => [...root.querySelectorAll<HTMLElement>('.min-chip')];

function dock(wids: string[], rows: HeadAgent[] = wids.map((w) => ({ window_id: w, name: `n${w}` }))) {
  const restore = vi.fn<(wid: string) => void>();
  const r = render(<Dock wids={wids} agents={by(rows)} restore={restore} />);
  return { ...r, restore };
}

test('one chip per minimized pane, in the order given, with the count in the label', () => {
  const { container } = dock(['@3', '@1']);
  expect(container.querySelector('.min-dock-label')?.textContent).toBe('Minimized (2)');
  expect(chips(container).map((c) => c.dataset.wid)).toEqual(['@3', '@1']);
  const c = chips(container)[0] as HTMLElement;
  expect(c.className).toBe('min-chip min-chip-minimized');
  expect(c.title).toBe('Click to restore n@3');
  expect(c.querySelector('.min-chip-title')?.textContent).toBe('n@3');
  expect(c.querySelector('.min-chip-icon svg')).not.toBeNull();
});

test('a click restores that pane', () => {
  const { container, restore } = dock(['@3', '@1']);
  fireEvent.click(chips(container)[1] as HTMLElement);
  expect(restore).toHaveBeenCalledWith('@1');
});

test('a chip waiting on a human wears term-waiting and the waiting dot', () => {
  const { container } = dock(['@1', '@2'], [{ window_id: '@1', needs_human: true }, { window_id: '@2', session_status: 'busy' }]);
  const [a, b] = chips(container) as [HTMLElement, HTMLElement];
  expect(a.classList.contains('term-waiting')).toBe(true);
  expect(a.querySelector('.term-status-dot')?.classList.contains('waiting')).toBe(true);
  expect(b.classList.contains('term-waiting')).toBe(false);
  expect(b.querySelector('.term-status-dot')?.classList.contains('working')).toBe(true);
  expect(b.querySelector('.term-status-dot')?.getAttribute('title')).toBe('Working');
});

test('a dispatched run\'s chip wears the run\'s own progress icon, labelled (CMX-137)', () => {
  const run = { window_id: '@7', name: 'cmx-1', session_status: 'busy', run: { task_id: 'CMX-1', state: 'running' } } as HeadAgent;
  const { container } = dock(['@7'], [run]);
  const dot = (chips(container)[0] as HTMLElement).querySelector('.term-status-dot') as HTMLElement;
  expect(dot.getAttribute('role')).toBe('img');
  expect(dot.getAttribute('aria-label')).toBeTruthy();
  expect(dot.title).toBe(dot.getAttribute('aria-label'));
});

// CMX-311: jsdom parses the 240 KB style.css on insertion, ~2 s on a loaded machine: once, in
// beforeAll (the hook's own 10 s budget), not inside a test's 5 s, and only around these tests.
describe('under style.css', () => {
  let style: HTMLStyleElement;
  beforeAll(() => {
    style = document.createElement('style');
    style.textContent = css;
    document.head.append(style);
  });
  afterAll(() => style.remove());

  test('the tray is ONE row: nowrap, scrolling sideways, chips never shrink (style.css)', () => {
    const host = document.createElement('div');
    host.id = 'term-min-dock';
    document.body.append(host);
    render(<Dock wids={['@1', '@2']} agents={by([])} restore={() => {}} />, { container: host });
    const dockCs = getComputedStyle(host);
    expect(dockCs.flexWrap).toBe('nowrap');
    const strip = host.querySelector('.min-dock-chips') as HTMLElement;
    const cs = getComputedStyle(strip);
    expect(cs.flexWrap).toBe('nowrap');
    expect(cs.overflowX).toBe('auto');
    expect(cs.overflowY).toBe('hidden');
    expect(getComputedStyle(chips(host)[0] as HTMLElement).flexShrink).toBe('0');
    host.hidden = true;
    expect(getComputedStyle(host).display).toBe('none');
    host.remove();
  });
});

/** jsdom lays nothing out: give the strip a scroll geometry. */
function geometry(strip: HTMLElement, scrollWidth: number, clientWidth: number) {
  Object.defineProperty(strip, 'scrollWidth', { configurable: true, value: scrollWidth });
  Object.defineProperty(strip, 'clientWidth', { configurable: true, value: clientWidth });
}

test('a fade marks each edge that hides chips, and only while it does', () => {
  const { container } = dock(['@1', '@2', '@3']);
  const strip = container.querySelector('.min-dock-chips') as HTMLElement;
  expect(strip.className).toBe('min-dock-chips');   // no overflow, no fade
  geometry(strip, 600, 200);
  strip.scrollLeft = 0;
  fireEvent.scroll(strip);
  expect(strip.className).toBe('min-dock-chips fade-end');
  strip.scrollLeft = 200;
  fireEvent.scroll(strip);
  expect(strip.className).toBe('min-dock-chips fade-start fade-end');
  strip.scrollLeft = 400;
  fireEvent.scroll(strip);
  expect(strip.className).toBe('min-dock-chips fade-start');
});

test('a window resize re-measures the fades', () => {
  const { container } = dock(['@1']);
  const strip = container.querySelector('.min-dock-chips') as HTMLElement;
  geometry(strip, 600, 200);
  fireEvent(window, new Event('resize'));
  expect(strip.className).toBe('min-dock-chips fade-end');
});

test('a vertical wheel scrolls the row sideways, and keeps the page still', () => {
  const { container } = dock(['@1', '@2']);
  const strip = container.querySelector('.min-dock-chips') as HTMLElement;
  geometry(strip, 600, 200);
  const down = new WheelEvent('wheel', { deltaY: 120, cancelable: true, bubbles: true });
  strip.dispatchEvent(down);
  expect(strip.scrollLeft).toBe(120);
  expect(down.defaultPrevented).toBe(true);
});

test('a horizontal wheel, or a row that fits, is left to the browser', () => {
  const { container } = dock(['@1', '@2']);
  const strip = container.querySelector('.min-dock-chips') as HTMLElement;
  geometry(strip, 600, 200);
  const side = new WheelEvent('wheel', { deltaX: 50, deltaY: 10, cancelable: true });
  strip.dispatchEvent(side);
  expect(side.defaultPrevented).toBe(false);
  geometry(strip, 200, 200);
  const fits = new WheelEvent('wheel', { deltaY: 120, cancelable: true });
  strip.dispatchEvent(fits);
  expect(fits.defaultPrevented).toBe(false);
  expect(strip.scrollLeft).toBe(0);
});

test('a reorder glides: a chip that moved starts from its old place', () => {
  const left = new Map([['@1', 0], ['@2', 100]]);
  const offset = vi.spyOn(HTMLElement.prototype, 'offsetLeft', 'get').mockImplementation(function (this: HTMLElement) {
    return left.get(this.dataset.wid ?? '') ?? 0;
  });
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation(() => 0);
  const rows = [{ window_id: '@1' }, { window_id: '@2' }];
  const { container, rerender } = dock(['@1', '@2'], rows);
  left.set('@2', 0).set('@1', 100);
  rerender(<Dock wids={['@2', '@1']} agents={by(rows)} restore={() => {}} />);
  const [two, one] = chips(container) as [HTMLElement, HTMLElement];
  expect(two.style.transform).toBe('translateX(100px)');
  expect(one.style.transform).toBe('translateX(-100px)');
  offset.mockRestore();
});

// --- the dock's root (terminals.js hands it #term-min-dock) --------------------------------

test('the root shows the minimized live panes in wall mode only, hidden when there are none', () => {
  const el = document.createElement('div');
  document.body.append(el);
  const restore = vi.fn<(wid: string) => void>();
  configureDock({ restore });
  agents.set([{ window_id: '@1', online: true }, { window_id: '@2', online: true }, { window_id: '@3', online: false }]);
  mountDock(el);
  expect(el.hidden).toBe(true);
  setMinimized('@2', true);
  expect(el.hidden).toBe(false);
  expect(chips(el).map((c) => c.dataset.wid)).toEqual(['@2']);
  setMinimized('@3', true);   // offline: no chip
  expect(chips(el).map((c) => c.dataset.wid)).toEqual(['@2']);
  fireEvent.click(chips(el)[0] as HTMLElement);
  expect(restore).toHaveBeenCalledWith('@2');
  termMode.set('single');
  expect(el.hidden).toBe(true);
  expect(chips(el)).toEqual([]);
  termMode.set('wall');
  refreshDock();
  expect(el.hidden).toBe(false);
  el.remove();
});

test('the chips follow recency: a pane that just started working moves to the front', () => {
  agents.set([{ window_id: '@1', session_status: 'idle' }, { window_id: '@2', session_status: 'idle' }]);
  setMinimized('@1', true);
  setMinimized('@2', true);
  expect(dockWids()).toEqual(['@1', '@2']);
  stampActivity([{ window_id: '@2', session_status: 'busy' }], 10);
  expect(dockWids()).toEqual(['@2', '@1']);
  // staying busy is not a new start: the stamp holds
  stampActivity([{ window_id: '@2', session_status: 'busy' }, { window_id: '@1', session_status: 'busy' }], 20);
  expect(dockWids()).toEqual(['@1', '@2']);
});

test('runLazyDock acts the machine\'s decisions out through the Wall, and remembers them across a reload', () => {
  const host = { onWall: () => true, minimize: vi.fn<(w: string) => void>(), restore: vi.fn<(w: string) => void>() };
  runLazyDock([{ window_id: '@9', dispatched: true, session_status: 'busy' }], host);
  expect(host.minimize).toHaveBeenCalledWith('@9');
  expect(JSON.parse(localStorage.getItem('pc_wall_autodock') ?? '{}')).toEqual({ '@9': 'docked' });
  setMinimized('@9', true);
  runLazyDock([{ window_id: '@9', dispatched: true, needs_human: true }], host);
  expect(host.restore).toHaveBeenCalledWith('@9');
  expect(JSON.parse(localStorage.getItem('pc_wall_autodock') ?? '{}')).toEqual({ '@9': 'popped' });
  forgetAutoDock('@9');
  expect(JSON.parse(localStorage.getItem('pc_wall_autodock') ?? '{}')).toEqual({});
  forgetAutoDock('@9');   // unknown: a no-op
});
