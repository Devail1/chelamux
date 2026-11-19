// CMX-380: drag a group to reorder it, drag a session onto a custom group. The pure half
// (where a drop lands, what it writes) and the list's pointer handling, in jsdom with each
// section's box stubbed. The persisted writes are tests/dragWrite.test.ts; the phone's
// long press in a scrolled drawer is tests/browser/sidebar_drag.test.mjs.
import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { DRAG_THRESHOLD_PX, LONG_PRESS_DRAG_MS, Sidebar, TOUCH_SLOP_PX, groupSlot } from '../index.ts';
import {
  type AgentRow, type Custom, DISPATCHED_KEY, UNGROUPED_KEY, assignKey, buildItems, canDragGroup, customKey, dropAssignee,
  moveGroup, normalizeView, orderGroups, placeGroup,
} from '../model.ts';
import { actions, runWin, state, win } from './fixtures.ts';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  document.body.className = '';
});

// --- the model: a drop writes what the menus write ---------------------------------------

/** The order the list draws for `stored` (model.orderGroups), as the menus see it. */
const drawn = (keys: string[], stored: string[]) =>
  orderGroups(keys.map(key => ({ key, label: key.replace(/^\//, '') })), stored).map(g => g.key);

test('dragging group C above group A writes the order two Move up\'s would', () => {
  const keys = ['/a', '/b', '/c'];
  const once = moveGroup(drawn(keys, []), [], '/c', -1) ?? [];
  const twice = moveGroup(drawn(keys, once), once, '/c', -1);
  expect(twice).toEqual(['/c', '/a', '/b']);
  expect(placeGroup(drawn(keys, []), [], '/c', '/a')).toEqual(twice);
  // and down: A below C is two Move down's
  const d1 = moveGroup(drawn(keys, []), [], '/a', 1) ?? [];
  expect(placeGroup(drawn(keys, []), [], '/a', null)).toEqual(moveGroup(drawn(keys, d1), d1, '/a', 1));
});

test('placeGroup keeps a stored group that is not on screen, after the visible order', () => {
  expect(placeGroup(['/a', '/b'], ['/gone', '/b', '/a'], '/b', '/a')).toEqual(['/b', '/a', '/gone']);
});

test('placeGroup: Dispatched stays last, never moves, and a drop that changes nothing writes nothing', () => {
  const keys = ['/a', '/b', DISPATCHED_KEY];
  expect(placeGroup(keys, [], '/a', null)).toEqual(['/b', '/a', DISPATCHED_KEY]);
  expect(placeGroup(keys, [], '/a', DISPATCHED_KEY)).toEqual(['/b', '/a', DISPATCHED_KEY]);
  expect(placeGroup(keys, [], DISPATCHED_KEY, '/a')).toBeNull();
  expect(placeGroup(keys, [], '/a', '/a')).toBeNull();
  expect(placeGroup(keys, [], '/a', '/b')).toBeNull(); // already just before /b
  expect(placeGroup(['/a', '/b'], [], '/b', null)).toBeNull(); // already last
  expect(placeGroup(keys, [], '/zz', '/a')).toBeNull();
  expect(placeGroup(keys, [], '/a', '/zz')).toBeNull();
});

test('canDragGroup: every headed group but Dispatched and the flat list', () => {
  expect(canDragGroup({ key: '/a' })).toBe(true);
  expect(canDragGroup({ key: customKey('g1') })).toBe(true);
  expect(canDragGroup({ key: DISPATCHED_KEY })).toBe(false);
  expect(canDragGroup({ key: '~all', flat: true })).toBe(false);
});

test('dropAssignee: a custom group is its id, Ungrouped or no group unassigns, anything else is no target', () => {
  const c: Custom = { groups: [{ id: 'g1', name: 'Infra' }], assign: {} };
  expect(dropAssignee(customKey('g1'), c)).toBe('g1');
  expect(dropAssignee(UNGROUPED_KEY, c)).toBeNull();
  expect(dropAssignee(null, c)).toBeNull();
  expect(dropAssignee(customKey('gone'), c)).toBeUndefined();
  expect(dropAssignee('~needs-you', c)).toBeUndefined();
  expect(dropAssignee('~archived', c)).toBeUndefined();
  expect(dropAssignee(DISPATCHED_KEY, c)).toBeUndefined();
  expect(dropAssignee('/srv/a', c)).toBeUndefined();
});

test('dropAssignee: only a `~custom:` key names a group, even when the rest of a key spells a group id', () => {
  // '/srv/abc' is as long as the `~custom:` prefix, so the folder '/srv/abcg1' ends in 'g1'
  const c: Custom = { groups: [{ id: 'g1', name: 'Infra' }], assign: {} };
  expect('~custom:'.length).toBe('/srv/abc'.length);
  expect(dropAssignee('/srv/abcg1', c)).toBeUndefined();
  expect(dropAssignee('~custom:g1', c)).toBe('g1');
});

test('groupSlot: the line goes before the group whose middle is below the pointer, or after the last', () => {
  const gs = [{ key: 'A', top: 0, bottom: 40 }, { key: 'B', top: 40, bottom: 80 }, { key: 'C', top: 80, bottom: 120 }];
  expect(groupSlot(gs, 'C', 10)).toEqual({ before: 'A', line: { key: 'A', side: 'before' } });
  expect(groupSlot(gs, 'A', 110)).toEqual({ before: null, line: { key: 'C', side: 'after' } });
  expect(groupSlot(gs, 'A', 70)).toEqual({ before: 'C', line: { key: 'C', side: 'before' } });
  // over itself, or just above the group after it: no move, no line
  expect(groupSlot(gs, 'B', 50)).toEqual({ before: 'B', line: null });
  expect(groupSlot(gs, 'B', 70)).toEqual({ before: 'C', line: null });
  expect(groupSlot(gs, 'C', 110)).toEqual({ before: null, line: null });
});

// --- the list: pointer handling ------------------------------------------------------

/** Pin an element's box (jsdom lays nothing out). */
function box(el: Element, top: number, bottom: number) {
  Object.defineProperty(el, 'getBoundingClientRect', {
    configurable: true,
    value: () => ({ top, bottom, left: 0, right: 240, width: 240, height: bottom - top, x: 0, y: top, toJSON() {} }),
  });
}

const pt = (x: number, y: number, over: Partial<PointerEventInit> = {}) =>
  ({ pointerId: 1, isPrimary: true, pointerType: 'mouse', button: 0, clientX: x, clientY: y, ...over });

function drawList(agents: AgentRow[], over = {}) {
  const acts = actions();
  const host = document.createElement('div');
  host.id = 'sidebar-agents';
  document.body.appendChild(host);
  const r = render(<Sidebar state={state(agents, over)} actions={acts} />, { container: host });
  return { el: host, acts, rerender: r.rerender };
}

/** Folder mode: three headed folders (two sessions each), 40px tall, stacked. */
function folders() {
  const rows = ['a', 'b', 'c'].flatMap(f => [win(`${f}1`, { cwd: `/srv/${f}` }), win(`${f}2`, { cwd: `/srv/${f}` })]);
  const d = drawList(rows);
  const groups = [...d.el.querySelectorAll('.side-group')];
  groups.forEach((g, i) => box(g, i * 40, i * 40 + 40));
  box(d.el, 0, 120);
  const head = (k: string) => d.el.querySelector(`.group-head[data-g="/srv/${k}"]`) as HTMLElement;
  return { ...d, head };
}

describe('desktop: a mouse drag with a move threshold', () => {
  test('dragging group C above A drops it before A, with a drop line while it moves', () => {
    const { el, acts, head } = folders();
    fireEvent.pointerDown(head('c'), pt(20, 90));
    fireEvent.pointerMove(window, pt(20, 60));
    fireEvent.pointerMove(window, pt(20, 10));
    expect(el.querySelector('.side-group[data-g="/srv/a"]')?.classList.contains('drop-before')).toBe(true);
    expect(el.querySelector('.side-group[data-g="/srv/c"]')?.classList.contains('dragging')).toBe(true);
    expect(document.body.classList.contains('sidebar-dragging')).toBe(true);
    fireEvent.pointerUp(window, pt(20, 10));
    expect(acts.dropGroup.mock.calls).toEqual([['/srv/c', '/srv/a']]);
    // the click that ends the drag folds nothing
    fireEvent.click(head('c'));
    expect(acts.toggleGroup).not.toHaveBeenCalled();
    expect(document.body.classList.contains('sidebar-dragging')).toBe(false);
    expect(el.querySelector('.drop-before, .drop-after, .dragging')).toBeNull();
  });

  test('only the group being dragged draws as dragging, not every group', () => {
    const { el, head } = folders();
    fireEvent.pointerDown(head('c'), pt(20, 90));
    fireEvent.pointerMove(window, pt(20, 10));
    expect([...el.querySelectorAll('.side-group.dragging')].map(g => (g as HTMLElement).dataset.g)).toEqual(['/srv/c']);
    fireEvent.pointerUp(window, pt(20, 10));
  });

  test('a click under the move threshold still opens the row and folds the group', () => {
    const rows = [win('solo', { cwd: '/srv/a' }), win('x1', { cwd: '/srv/b' }), win('x2', { cwd: '/srv/b' })];
    const { el, acts } = drawList(rows);
    const solo = el.querySelector('.agent-row[data-agent="solo"]') as HTMLElement;
    expect(solo.classList.contains('draggable')).toBe(true); // a bare folder's row moves its folder
    fireEvent.pointerDown(solo, pt(20, 10));
    fireEvent.pointerMove(window, pt(20 + DRAG_THRESHOLD_PX - 1, 10));
    fireEvent.pointerUp(window, pt(20 + DRAG_THRESHOLD_PX - 1, 10));
    fireEvent.click(solo);
    expect(acts.selectAgent.mock.calls).toEqual([['solo']]);
    const head = el.querySelector('.group-head[data-g="/srv/b"]') as HTMLElement;
    fireEvent.pointerDown(head, pt(20, 50));
    fireEvent.pointerUp(window, pt(20, 50));
    fireEvent.click(head);
    expect(acts.toggleGroup.mock.calls).toEqual([['/srv/b']]);
    expect(acts.dropGroup).not.toHaveBeenCalled();
  });

  test('at the threshold it is a drag: the click after it opens nothing', () => {
    const rows = [win('solo', { cwd: '/srv/a' }), win('x1', { cwd: '/srv/b' }), win('x2', { cwd: '/srv/b' })];
    const { el, acts } = drawList(rows);
    const solo = el.querySelector('.agent-row[data-agent="solo"]') as HTMLElement;
    fireEvent.pointerDown(solo, pt(20, 10));
    fireEvent.pointerMove(window, pt(20 + DRAG_THRESHOLD_PX, 10));
    expect(solo.classList.contains('dragging')).toBe(true);
    fireEvent.pointerUp(window, pt(20 + DRAG_THRESHOLD_PX, 10));
    fireEvent.click(solo);
    expect(acts.selectAgent).not.toHaveBeenCalled();
  });

  test('Esc cancels: nothing is dropped, and the release opens nothing', () => {
    const { el, acts, head } = folders();
    fireEvent.pointerDown(head('c'), pt(20, 90));
    fireEvent.pointerMove(window, pt(20, 10));
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(el.querySelector('.drop-before')).toBeNull();
    fireEvent.pointerUp(window, pt(20, 10));
    fireEvent.click(head('c'));
    expect(acts.dropGroup).not.toHaveBeenCalled();
    expect(acts.toggleGroup).not.toHaveBeenCalled();
  });

  test('a pointercancel ends the drag without a drop', () => {
    const { acts, head } = folders();
    fireEvent.pointerDown(head('c'), pt(20, 90));
    fireEvent.pointerMove(window, pt(20, 10));
    fireEvent.pointerCancel(window, pt(20, 10));
    fireEvent.pointerUp(window, pt(20, 10));
    expect(acts.dropGroup).not.toHaveBeenCalled();
    expect(document.body.classList.contains('sidebar-dragging')).toBe(false);
  });

  test('losing the window (a release over a terminal iframe) cancels the drag', () => {
    const { acts, head } = folders();
    fireEvent.pointerDown(head('c'), pt(20, 90));
    fireEvent.pointerMove(window, pt(20, 10));
    expect(document.body.classList.contains('sidebar-dragging')).toBe(true);
    fireEvent.blur(window);
    expect(document.body.classList.contains('sidebar-dragging')).toBe(false);
    fireEvent.pointerUp(window, pt(20, 10));
    expect(acts.dropGroup).not.toHaveBeenCalled();
    // and the next drag starts: nothing was left stuck
    fireEvent.pointerDown(head('c'), pt(20, 90));
    fireEvent.pointerMove(window, pt(20, 10));
    fireEvent.pointerUp(window, pt(20, 10));
    expect(acts.dropGroup.mock.calls).toEqual([['/srv/c', '/srv/a']]);
  });

  test('a right button, the ⋯ menu and the + start no drag', () => {
    const { el, acts, head } = folders();
    fireEvent.pointerDown(head('c'), pt(20, 90, { button: 2 }));
    fireEvent.pointerMove(window, pt(20, 10, { button: 2 }));
    fireEvent.pointerUp(window, pt(20, 10, { button: 2 }));
    for (const sel of ['.group-more', '.group-add']) {
      fireEvent.pointerDown(el.querySelector(`.group-head[data-g="/srv/c"] ${sel}`) as HTMLElement, pt(20, 90));
      fireEvent.pointerMove(window, pt(20, 10));
      fireEvent.pointerUp(window, pt(20, 10));
    }
    expect(acts.dropGroup).not.toHaveBeenCalled();
  });

  test('on the folded icon rail nothing drags, and no grip is drawn there by the CSS', () => {
    document.body.classList.add('sidebar-collapsed');
    const { acts, head } = folders();
    fireEvent.pointerDown(head('c'), pt(20, 90));
    fireEvent.pointerMove(window, pt(20, 10));
    fireEvent.pointerUp(window, pt(20, 10));
    expect(acts.dropGroup).not.toHaveBeenCalled();
  });
});

test('the grip: a Lucide grip-vertical on every movable header and draggable row, none on Dispatched', () => {
  const run = runWin('CMX-9', 'agent');
  const rows = [win('a1', { cwd: '/srv/a' }), win('a2', { cwd: '/srv/a' }), win('solo', { cwd: '/srv/b' }), run];
  const { el } = drawList(rows);
  const grip = (sel: string) => el.querySelector(`${sel} .drag-grip svg.lucide-grip-vertical`);
  expect(grip('.group-head[data-g="/srv/a"]')).toBeTruthy();
  expect(grip('.agent-row[data-agent="solo"]')).toBeTruthy();
  expect(el.querySelector(`.group-head[data-g="${DISPATCHED_KEY}"] .drag-grip`)).toBeNull();
  expect(el.querySelector(`.group-head[data-g="${DISPATCHED_KEY}"]`)?.classList.contains('draggable')).toBe(false);
  // a row of a headed folder is not draggable outside Custom groups
  expect(el.querySelector('.agent-row[data-agent="a1"] .drag-grip')).toBeNull();
  expect(el.querySelector('.drag-grip')?.getAttribute('aria-hidden')).toBe('true');
});

test('Dispatched never drags, and a group dragged below it lands just above it', () => {
  const run = runWin('CMX-9', 'agent');
  const rows = [win('a1', { cwd: '/srv/a' }), win('a2', { cwd: '/srv/a' }), win('b1', { cwd: '/srv/b' }), win('b2', { cwd: '/srv/b' }), run];
  const { el, acts } = drawList(rows);
  [...el.querySelectorAll('.side-group')].forEach((g, i) => box(g, i * 40, i * 40 + 40));
  const disp = el.querySelector(`.group-head[data-g="${DISPATCHED_KEY}"]`) as HTMLElement;
  fireEvent.pointerDown(disp, pt(20, 90));
  fireEvent.pointerMove(window, pt(20, 10));
  fireEvent.pointerUp(window, pt(20, 10));
  expect(acts.dropGroup).not.toHaveBeenCalled();
  const a = el.querySelector('.group-head[data-g="/srv/a"]') as HTMLElement;
  fireEvent.pointerDown(a, pt(20, 10));
  fireEvent.pointerMove(window, pt(20, 115));
  // the line is under the last MOVABLE group, never under Dispatched
  expect(el.querySelector('.side-group[data-g="/srv/b"]')?.classList.contains('drop-after')).toBe(true);
  expect(el.querySelector(`.side-group[data-g="${DISPATCHED_KEY}"]`)?.className).not.toMatch(/drop-/);
  fireEvent.pointerUp(window, pt(20, 115));
  expect(acts.dropGroup.mock.calls).toEqual([['/srv/a', null]]);
});

// --- Custom groups: a session onto a group ---------------------------------------------

function custom() {
  const mine = win('mine', { session_id: 'S-mine' });
  const other = win('other', { session_id: 'S-other' });
  const blocked = win('blocked', { needs_human: true, session_id: 'S-b' });
  const c: Custom = { groups: [{ id: 'g1', name: 'Infra' }, { id: 'g2', name: 'Web' }], assign: { 's:S-other': 'g2' } };
  const d = drawList([mine, other, blocked], { view: normalizeView({ groupBy: 'custom', activity: 'all', sort: 'name' }), custom: c });
  // Needs you, Web, Ungrouped (Infra is empty and hidden)
  const secs = [...d.el.children];
  secs.forEach((g, i) => box(g, i * 40, i * 40 + 40));
  box(d.el, 0, 200);
  const row = (n: string) => d.el.querySelector(`.agent-row[data-agent="${n}"]`) as HTMLElement;
  return { ...d, row, mine, secs };
}

test('a session dropped on a custom group is assigned there, by its stable key', () => {
  const { el, acts, row, mine } = custom();
  expect([...el.children].map(c => (c as HTMLElement).dataset.g ?? 'needs')).toEqual(['needs', customKey('g2'), UNGROUPED_KEY]);
  fireEvent.pointerDown(row('mine'), pt(20, 90));
  fireEvent.pointerMove(window, pt(20, 60));
  expect(el.querySelector(`.side-group[data-g="${customKey('g2')}"]`)?.classList.contains('drop-target')).toBe(true);
  fireEvent.pointerUp(window, pt(20, 60));
  const key = assignKey(buildItems([mine])[0] as never);
  expect(key).toBe('s:S-mine');
  expect(acts.dropOnGroup.mock.calls).toEqual([[key, customKey('g2')]]);
  fireEvent.click(row('mine'));
  expect(acts.selectAgent).not.toHaveBeenCalled();
});

test('only the session being dragged draws as dragging, not every draggable row', () => {
  const { el, row } = custom();
  // negative control: both sessions are draggable rows
  expect([row('mine'), row('other')].map(r => r.classList.contains('draggable'))).toEqual([true, true]);
  fireEvent.pointerDown(row('mine'), pt(20, 90));
  fireEvent.pointerMove(window, pt(20, 60));
  expect([...el.querySelectorAll('.agent-row.dragging')].map(r => (r as HTMLElement).dataset.agent)).toEqual(['mine']);
  fireEvent.pointerUp(window, pt(20, 60));
});

test('dropped on Ungrouped, or on the list below every group, a session is unassigned', () => {
  const { acts, row } = custom();
  fireEvent.pointerDown(row('other'), pt(20, 50));
  fireEvent.pointerMove(window, pt(20, 95));
  fireEvent.pointerUp(window, pt(20, 95));
  fireEvent.pointerDown(row('other'), pt(20, 50));
  fireEvent.pointerMove(window, pt(20, 180)); // on the list, under every group
  fireEvent.pointerUp(window, pt(20, 180));
  expect(acts.dropOnGroup.mock.calls).toEqual([['s:S-other', UNGROUPED_KEY], ['s:S-other', UNGROUPED_KEY]]);
});

test('pinned sections reject a drop: Needs you is marked, and nothing is written', () => {
  const { el, acts, row } = custom();
  fireEvent.pointerDown(row('mine'), pt(20, 90));
  fireEvent.pointerMove(window, pt(20, 20));
  expect(el.querySelector('.side-triage')?.classList.contains('drop-reject')).toBe(true);
  fireEvent.pointerUp(window, pt(20, 20));
  // off the list, and onto its own group: no drop either
  fireEvent.pointerDown(row('mine'), pt(20, 90));
  fireEvent.pointerMove(window, pt(20, 400));
  fireEvent.pointerUp(window, pt(20, 400));
  fireEvent.pointerDown(row('mine'), pt(20, 90));
  fireEvent.pointerMove(window, pt(20, 100));
  fireEvent.pointerUp(window, pt(20, 100));
  expect(acts.dropOnGroup).not.toHaveBeenCalled();
});

test('the Archived section rejects a drop', () => {
  const mine = win('mine', { session_id: 'S-mine' });
  const old = win('old', { session_id: 'S-old', done: true });
  const c: Custom = { groups: [{ id: 'g1', name: 'Infra' }], assign: {} };
  const { el, acts } = drawList([mine, old], {
    view: normalizeView({ groupBy: 'custom', activity: 'all', sort: 'name' }), custom: c, archived: [buildItems([old])[0]?.key ?? ''],
  });
  const secs = [...el.children];
  expect(secs.map(s => (s as HTMLElement).dataset.g)).toEqual([UNGROUPED_KEY, '~archived']);
  secs.forEach((g, i) => box(g, i * 40, i * 40 + 40));
  box(el, 0, 200);
  fireEvent.pointerDown(el.querySelector('.agent-row[data-agent="mine"]') as HTMLElement, pt(20, 10));
  fireEvent.pointerMove(window, pt(20, 60));
  expect(el.querySelector('.side-archived')?.classList.contains('drop-reject')).toBe(true);
  fireEvent.pointerUp(window, pt(20, 60));
  expect(acts.dropOnGroup).not.toHaveBeenCalled();
  // an archived row is not draggable
  expect(el.querySelector('.agent-row[data-agent="old"]')?.classList.contains('draggable')).toBe(false);
});

// --- the phone: a long press starts it, a swipe scrolls --------------------------------

describe('touch', () => {
  beforeEach(() => { vi.useFakeTimers(); });
  const touch = (x: number, y: number) => pt(x, y, { pointerType: 'touch' });

  test(`a long press (${LONG_PRESS_DRAG_MS} ms) starts the drag, and the list stops scrolling while it runs`, () => {
    const { el, acts, head } = folders();
    fireEvent.pointerDown(head('c'), touch(20, 90));
    act(() => { vi.advanceTimersByTime(LONG_PRESS_DRAG_MS - 1); });
    expect(document.body.classList.contains('sidebar-dragging')).toBe(false);
    act(() => { vi.advanceTimersByTime(1); });
    expect(document.body.classList.contains('sidebar-dragging')).toBe(true);
    const tm = new Event('touchmove', { bubbles: true, cancelable: true });
    head('c').dispatchEvent(tm);
    expect(tm.defaultPrevented).toBe(true);
    // the long press's context menu is not opened mid-drag
    const cm = new MouseEvent('contextmenu', { bubbles: true, cancelable: true });
    head('c').dispatchEvent(cm);
    expect(cm.defaultPrevented).toBe(true);
    fireEvent.pointerMove(window, touch(20, 10));
    fireEvent.pointerUp(window, touch(20, 10));
    expect(acts.dropGroup.mock.calls).toEqual([['/srv/c', '/srv/a']]);
    // once it ends, a touch scrolls again
    const after = new Event('touchmove', { bubbles: true, cancelable: true });
    el.dispatchEvent(after);
    expect(after.defaultPrevented).toBe(false);
  });

  // CMX-435: a finger that lands on a list still gliding from a flick gets an UNCANCELABLE
  // touch sequence from the browser, so no drag could hold the list: its long press arms none.
  // A cancelable touchstart (a list at rest) arms one as before.
  for (const cancelable of [true, false]) {
    test(`a long press whose touchstart is ${cancelable ? 'cancelable starts' : 'UNCANCELABLE (it caught a glide) starts no'} drag`, () => {
      const { el, acts, head } = folders();
      fireEvent.pointerDown(head('c'), touch(20, 90));
      head('c').dispatchEvent(new Event('touchstart', { bubbles: true, cancelable }));
      act(() => { vi.advanceTimersByTime(LONG_PRESS_DRAG_MS); });
      expect(document.body.classList.contains('sidebar-dragging')).toBe(cancelable);
      const tm = new Event('touchmove', { bubbles: true, cancelable: true });
      head('c').dispatchEvent(tm);
      expect(tm.defaultPrevented).toBe(cancelable);
      fireEvent.pointerMove(window, touch(20, 10));
      fireEvent.pointerUp(window, touch(20, 10));
      expect(acts.dropGroup.mock.calls).toEqual(cancelable ? [['/srv/c', '/srv/a']] : []);
      expect(document.body.classList.contains('sidebar-dragging')).toBe(false);
      // the next touch, on a list at rest, is judged on its own
      fireEvent.pointerDown(head('c'), touch(20, 90));
      el.dispatchEvent(new Event('touchstart', { bubbles: true, cancelable: true }));
      act(() => { vi.advanceTimersByTime(LONG_PRESS_DRAG_MS); });
      expect(document.body.classList.contains('sidebar-dragging')).toBe(true);
      fireEvent.pointerUp(window, touch(20, 90));
    });
  }

  test('a swipe never starts one: a finger that moves before the long press is a scroll', () => {
    const { acts, head } = folders();
    fireEvent.pointerDown(head('c'), touch(20, 90));
    const tm = new Event('touchmove', { bubbles: true, cancelable: true });
    head('c').dispatchEvent(tm);
    expect(tm.defaultPrevented).toBe(false);
    fireEvent.pointerMove(window, touch(20, 90 - TOUCH_SLOP_PX - 1));
    act(() => { vi.advanceTimersByTime(LONG_PRESS_DRAG_MS * 2); });
    expect(document.body.classList.contains('sidebar-dragging')).toBe(false);
    fireEvent.pointerMove(window, touch(20, 10));
    fireEvent.pointerUp(window, touch(20, 10));
    expect(acts.dropGroup).not.toHaveBeenCalled();
  });

  test('a tap is still a tap: lifted before the long press, it opens the row', () => {
    const rows = [win('solo', { cwd: '/srv/a' }), win('x1', { cwd: '/srv/b' }), win('x2', { cwd: '/srv/b' })];
    const { el, acts } = drawList(rows);
    const solo = el.querySelector('.agent-row[data-agent="solo"]') as HTMLElement;
    fireEvent.pointerDown(solo, touch(20, 10));
    act(() => { vi.advanceTimersByTime(100); });
    fireEvent.pointerUp(window, touch(20, 10));
    fireEvent.click(solo);
    act(() => { vi.advanceTimersByTime(LONG_PRESS_DRAG_MS); });
    expect(acts.selectAgent.mock.calls).toEqual([['solo']]);
    expect(document.body.classList.contains('sidebar-dragging')).toBe(false);
  });
});
