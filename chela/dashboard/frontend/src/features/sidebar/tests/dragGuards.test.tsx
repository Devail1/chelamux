// CMX-430: the drag's guards, each pinned by a test that fails when the guard is corrupted
// (the 11-18 nightly sweep found that no test could tell them apart). Most tests drive the hook
// through a small list of our own with fixed boxes: its `accepts` can say yes to everything
// (so a pinned section is refused by the drag itself), its scroller is ours, and it reports
// each view it draws. The list the island draws is in tests/drag.test.tsx and tests/manualSort.test.tsx.
import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import {
  type DragDrop, type DragSource, type DragView, DRAG_THRESHOLD_PX, LONG_PRESS_DRAG_MS, Sidebar, groupSlot, useSidebarDrag,
} from '../index.ts';
import { NEEDS_YOU_KEY, UNGROUPED_KEY, kidsKey, buildItems } from '../model.ts';
import { actions, state, win } from './fixtures.ts';

/** Pin an element's box (jsdom lays nothing out). */
function box(el: Element, top: number, bottom: number) {
  Object.defineProperty(el, 'getBoundingClientRect', {
    configurable: true,
    value: () => ({ top, bottom, left: 0, right: 240, width: 240, height: bottom - top, x: 0, y: top, toJSON() {} }),
  });
}

const pt = (x: number, y: number, over: Partial<PointerEventInit> = {}) =>
  ({ pointerId: 1, isPrimary: true, pointerType: 'mouse', button: 0, clientX: x, clientY: y, ...over });

const row = (item: string, group: string, reorder = false): DragSource =>
  ({ kind: 'row', assign: `a:${item}`, group, item, reorder });

function List({ drop, seen }: { drop: DragDrop; seen: (v: DragView | null) => void }) {
  const { view, bind } = useSidebarDrag(drop);
  seen(view);
  const group = (g: string, rows: string[]) => (
    <div className="side-group" data-g={g}>
      <div className="group-head" data-head={g} onPointerDown={bind({ kind: 'group', key: g })} />
      <div className="group-rows">
        {rows.map(r => <div key={r} className="agent-row" data-item={r} onPointerDown={bind(row(r, g, true))} />)}
      </div>
    </div>
  );
  return (
    <>
      <div className="side-triage"><div className="agent-row" data-item="n1" onPointerDown={bind(row('n1', NEEDS_YOU_KEY))} /></div>
      {group('g1', ['r1', 'r2', 'r3'])}
      {group('g2', [])}
      {group('g3', [])}
      <div className="loose" onPointerDown={bind(row('x', 'g1'))} />
    </>
  );
}

let frames: Map<number, FrameRequestCallback>;
let cancelled: ReturnType<typeof vi.fn>;

beforeEach(() => {
  frames = new Map();
  let n = 0;
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => { frames.set(++n, cb); return n; });
  cancelled = vi.fn((id: number) => { frames.delete(id); });
  vi.stubGlobal('cancelAnimationFrame', cancelled);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  document.body.innerHTML = '';
  document.body.className = '';
});

/** Run the queued animation frames once. */
const tick = () => {
  const due = [...frames.values()];
  frames.clear();
  act(() => { due.forEach(cb => cb(0)); });
};

/** A scroll box with a settable scrollTop (jsdom's is always 0). */
function scrollBox(overflowY: string, scrollHeight: number, clientHeight: number) {
  const el = document.createElement('div');
  el.style.overflowY = overflowY;
  let top = 200;
  Object.defineProperties(el, {
    scrollHeight: { configurable: true, value: scrollHeight },
    clientHeight: { configurable: true, value: clientHeight },
    scrollTop: { configurable: true, get: () => top, set: (v: number) => { top = v; } },
  });
  return el;
}

/** The list inside: `outer` (the real scroller, box 100-400) > a box that scrolls but has
 * nothing to scroll > a box whose content overflows but does not scroll > the list.
 * Sections: Needs you 0-40, g1 40-120 (head, then rows r1 r2 r3 of 20px), g2 120-200,
 * g3 200-280; the list 0-300. */
function mount(outerY = 'auto') {
  const drop = { group: vi.fn(), accepts: vi.fn(() => true), row: vi.fn(), reorder: vi.fn() };
  const outer = scrollBox(outerY, 1000, 300);
  const still = scrollBox('auto', 300, 300);
  const spill = scrollBox('visible', 500, 100);
  const host = document.createElement('div');
  outer.appendChild(still).appendChild(spill).appendChild(host);
  document.body.appendChild(outer);
  box(outer, 100, 400);
  let view: DragView | null = null;
  render(<List drop={drop} seen={v => { view = v; }} />, { container: host });
  const [needs, g1, g2, g3] = [...host.children];
  [[needs, 0, 40], [g1, 40, 120], [g2, 120, 200], [g3, 200, 280]].forEach(([el, t, b]) => box(el as Element, t as number, b as number));
  ['r1', 'r2', 'r3'].forEach((r, i) => box(host.querySelector(`[data-item="${r}"]`) as Element, 60 + i * 20, 80 + i * 20));
  box(host, 0, 300);
  const q = (sel: string) => host.querySelector(sel) as HTMLElement;
  return {
    drop, outer, still, spill, q, view: () => view as DragView | null,
    head: (g: string) => q(`[data-head="${g}"]`), item: (r: string) => q(`[data-item="${r}"]`),
  };
}

const dragging = () => document.body.classList.contains('sidebar-dragging');

test('groupSlot with no groups: no line and no target (a list with nothing to drop among)', () => {
  expect(groupSlot([], 'A', 10)).toEqual({ before: null, line: null });
});

describe('what the drag draws: a move that changes nothing keeps the view, one that does redraws', () => {
  test('group line: another group\'s line, then the same group\'s other side', () => {
    const { head, view, drop } = mount();
    fireEvent.pointerDown(head('g3'), pt(20, 210));
    fireEvent.pointerMove(window, pt(20, 50));
    expect(view()?.line).toEqual({ key: 'g1', side: 'before' });
    const v = view();
    fireEvent.pointerMove(window, pt(20, 55));
    expect(view()).toBe(v); // the same view: nothing to redraw
    fireEvent.pointerMove(window, pt(20, 130));
    expect(view()?.line).toEqual({ key: 'g2', side: 'before' });
    fireEvent.pointerUp(window, pt(20, 130));
    fireEvent.pointerDown(head('g1'), pt(20, 50));
    fireEvent.pointerMove(window, pt(20, 290));
    expect(view()?.line).toEqual({ key: 'g3', side: 'after' });
    fireEvent.pointerMove(window, pt(20, 230));
    expect(view()?.line).toEqual({ key: 'g3', side: 'before' });
    fireEvent.pointerUp(window, pt(20, 230));
    expect(drop.group.mock.calls).toEqual([['g3', 'g2'], ['g1', 'g3']]);
  });

  test('row over a group: another group, the list under every group, then off the list', () => {
    const { item, view, drop } = mount();
    fireEvent.pointerDown(item('r1'), pt(20, 70));
    fireEvent.pointerMove(window, pt(20, 130));
    expect(view()?.over).toEqual({ key: 'g2', ok: true });
    const v = view();
    fireEvent.pointerMove(window, pt(20, 140));
    expect(view()).toBe(v); // the same view: nothing to redraw
    fireEvent.pointerMove(window, pt(20, 290));
    expect(view()?.over).toEqual({ key: UNGROUPED_KEY, ok: true });
    fireEvent.pointerMove(window, pt(20, 350));
    expect(view()?.over).toBeNull();
    // let go off the list: nothing is written, though `accepts` would take anything
    fireEvent.pointerUp(window, pt(20, 350));
    expect(drop.row).not.toHaveBeenCalled();
  });

  test('row line within its group: one row, then the next', () => {
    const { item, view, drop } = mount();
    fireEvent.pointerDown(item('r3'), pt(20, 110));
    fireEvent.pointerMove(window, pt(20, 62));
    expect(view()?.rowLine).toEqual({ key: 'r1', side: 'before' });
    const v = view();
    fireEvent.pointerMove(window, pt(20, 64));
    expect(view()).toBe(v); // the same view: nothing to redraw
    fireEvent.pointerMove(window, pt(20, 85));
    expect(view()?.rowLine).toEqual({ key: 'r2', side: 'before' });
    fireEvent.pointerUp(window, pt(20, 85));
    expect(drop.reorder.mock.calls).toEqual([['g1', 'r3', 'r2']]);
  });
});

test('a pinned section takes no drop even when `accepts` would: Needs you is marked and nothing is written', () => {
  const { item, view, drop } = mount();
  fireEvent.pointerDown(item('r1'), pt(20, 70));
  fireEvent.pointerMove(window, pt(20, 20));
  expect(view()?.over).toEqual({ key: NEEDS_YOU_KEY, ok: false });
  fireEvent.pointerUp(window, pt(20, 20));
  expect(drop.row).not.toHaveBeenCalled();
});

test('a handle bound outside every section starts nothing and throws nothing', () => {
  const { q, view } = mount();
  const errors = vi.fn();
  window.addEventListener('error', errors);
  try {
    fireEvent.pointerDown(q('.loose'), pt(20, 70));
    fireEvent.pointerMove(window, pt(20, 130));
  } finally {
    window.removeEventListener('error', errors);
  }
  expect(errors).not.toHaveBeenCalled();
  expect([dragging(), view()]).toEqual([false, null]);
});

describe('one pointer drives the drag', () => {
  test('a pointer that is not the primary one starts no drag', () => {
    const { head, view } = mount();
    fireEvent.pointerDown(head('g3'), pt(20, 210, { isPrimary: false }));
    fireEvent.pointerMove(window, pt(20, 50, { isPrimary: false }));
    expect([dragging(), view()]).toEqual([false, null]);
  });

  test('a second finger neither starts, moves, drops nor cancels the first one\'s drag', () => {
    const { head, view, drop } = mount();
    const p2 = (y: number) => pt(20, y, { pointerId: 2, isPrimary: false });
    fireEvent.pointerDown(head('g3'), pt(20, 210));
    fireEvent.pointerMove(window, p2(50)); // far past the threshold, but not the dragging pointer
    expect(dragging()).toBe(false);
    fireEvent.pointerMove(window, pt(20, 50));
    expect(view()?.line).toEqual({ key: 'g1', side: 'before' });
    fireEvent.pointerMove(window, p2(130));
    expect(view()?.line).toEqual({ key: 'g1', side: 'before' });
    fireEvent.pointerUp(window, p2(130));
    fireEvent.pointerCancel(window, p2(130));
    expect(drop.group).not.toHaveBeenCalled();
    expect(dragging()).toBe(true);
    fireEvent.pointerUp(window, pt(20, 50));
    expect(drop.group.mock.calls).toEqual([['g3', 'g1']]);
  });
});

describe('keys and the context menu', () => {
  test('Esc before the drag starts is not taken: the press goes on to drag', () => {
    const { head, view } = mount();
    fireEvent.pointerDown(head('g3'), pt(20, 210));
    const esc = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    window.dispatchEvent(esc);
    expect(esc.defaultPrevented).toBe(false);
    fireEvent.pointerMove(window, pt(20, 210 - DRAG_THRESHOLD_PX));
    expect(view()?.source).toEqual({ kind: 'group', key: 'g3' });
    fireEvent.pointerUp(window, pt(20, 210 - DRAG_THRESHOLD_PX));
  });

  test('another key during a drag is not taken and cancels nothing', () => {
    const { head, drop } = mount();
    fireEvent.pointerDown(head('g3'), pt(20, 210));
    fireEvent.pointerMove(window, pt(20, 50));
    const key = new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true });
    window.dispatchEvent(key);
    expect([key.defaultPrevented, dragging()]).toEqual([false, true]);
    fireEvent.pointerUp(window, pt(20, 50));
    expect(drop.group.mock.calls).toEqual([['g3', 'g1']]);
  });

  test('a context menu before the long press is up still opens; once the drag runs it does not', () => {
    vi.useFakeTimers();
    const { head } = mount();
    fireEvent.pointerDown(head('g3'), pt(20, 210, { pointerType: 'touch' }));
    const early = new MouseEvent('contextmenu', { bubbles: true, cancelable: true });
    head('g3').dispatchEvent(early);
    expect(early.defaultPrevented).toBe(false);
    act(() => { vi.advanceTimersByTime(LONG_PRESS_DRAG_MS); });
    const late = new MouseEvent('contextmenu', { bubbles: true, cancelable: true });
    head('g3').dispatchEvent(late);
    expect(late.defaultPrevented).toBe(true);
    fireEvent.pointerUp(window, pt(20, 210, { pointerType: 'touch' }));
  });
});

describe('near an edge of the scroller the list scrolls, 8px a frame', () => {
  // the scroller's box is 100-400; the edge band is 36px
  for (const overflowY of ['auto', 'scroll']) {
    test(`overflow-y: ${overflowY}: up at the top edge, down at the bottom, never in between; only the box that scrolls`, () => {
      const { head, outer, still, spill } = mount(overflowY);
      fireEvent.pointerDown(head('g2'), pt(20, 200));
      fireEvent.pointerMove(window, pt(20, 136)); // just inside the band's end: no scroll
      expect(frames.size).toBe(0);
      fireEvent.pointerMove(window, pt(20, 135));
      fireEvent.pointerMove(window, pt(20, 134)); // one frame at a time
      expect(frames.size).toBe(1);
      tick();
      expect(outer.scrollTop).toBe(192);
      tick();
      expect(outer.scrollTop).toBe(184);
      expect([still.scrollTop, spill.scrollTop]).toEqual([200, 200]);
      fireEvent.pointerUp(window, pt(20, 134));
      expect(frames.size).toBe(0); // the queued frame is cancelled with the drag
      fireEvent.pointerDown(head('g2'), pt(20, 200));
      fireEvent.pointerMove(window, pt(20, 364)); // the band's end: no scroll
      expect(frames.size).toBe(0);
      fireEvent.pointerMove(window, pt(20, 365));
      tick();
      expect(outer.scrollTop).toBe(192);
      fireEvent.pointerUp(window, pt(20, 365));
    });
  }

  test('a drag that queued no frame cancels none', () => {
    const { head } = mount();
    fireEvent.pointerDown(head('g2'), pt(20, 200));
    fireEvent.pointerMove(window, pt(20, 250));
    fireEvent.pointerMove(window, pt(20, 260));
    fireEvent.pointerUp(window, pt(20, 260));
    expect(cancelled).not.toHaveBeenCalled();
  });

  test('with no cancelAnimationFrame, a frame queued before the drop scrolls nothing after it', () => {
    vi.stubGlobal('cancelAnimationFrame', undefined);
    const { head, outer } = mount();
    fireEvent.pointerDown(head('g2'), pt(20, 200));
    fireEvent.pointerMove(window, pt(20, 150)); // starts the drag
    fireEvent.pointerMove(window, pt(20, 120)); // in the top band: a frame is queued
    fireEvent.pointerUp(window, pt(20, 120));
    expect(dragging()).toBe(false);
    expect(frames.size).toBe(1);
    tick();
    expect([outer.scrollTop, frames.size]).toEqual([200, 0]);
  });
});

// --- CMX-390: a child reorders among ITS siblings, in the list the island draws -------------

test('a child drags among its own siblings, not the first sibling list of its folder', () => {
  const at = (h: number) => new Date(Date.parse('2026-10-19T12:00:00Z') - h * 3600e3).toISOString();
  const kid = (n: string, parent: string, h: number) =>
    win(n, { cwd: '/srv/x', session_id: `S-${n}`, parent_session_id: parent, created: at(10), last_activity: at(h) });
  const agents = [
    win('P', { cwd: '/srv/x', session_id: 'S-P', created: at(10), last_activity: at(1) }), kid('P1', 'S-P', 2), kid('P2', 'S-P', 3),
    win('Q', { cwd: '/srv/x', session_id: 'S-Q', created: at(10), last_activity: at(4) }), kid('Q1', 'S-Q', 5), kid('Q2', 'S-Q', 6),
  ];
  const acts = actions();
  const { container: el } = render(
    <Sidebar state={state(agents, { view: { ...state([]).view, groupBy: 'folder', sort: 'manual' } })} actions={acts} />);
  // every row 30px, in the order drawn: P P1 P2 Q Q1 Q2
  const rows = [...el.querySelectorAll<HTMLElement>('.agent-row')];
  expect(rows.map(r => r.dataset.agent)).toEqual(['P', 'P1', 'P2', 'Q', 'Q1', 'Q2']);
  rows.forEach((r, i) => box(r, 30 + i * 30, 60 + i * 30));
  box(el.querySelector('.side-group') as Element, 0, 210);
  box(el, 0, 300);
  const [q1, q2] = [rows[4], rows[5]] as [HTMLElement, HTMLElement];
  fireEvent.pointerDown(q2, pt(20, 195));
  fireEvent.pointerMove(window, pt(20, 155)); // the top half of Q1
  expect(q1.classList.contains('drop-before')).toBe(true);
  fireEvent.pointerUp(window, pt(20, 155));
  const q = buildItems([agents[3] as never])[0];
  expect(acts.dropRow.mock.calls).toEqual([[kidsKey(q as never), q2.dataset.item, q1.dataset.item]]);
});
