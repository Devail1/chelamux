// CMX-278: the sidebar's nav rows, the width handle, the long press that shows a whole row,
// and the island roots mountSidebar adds for them.
import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { createBridge } from '../../../shared/bridge/index.ts';
import { LONG_PRESS_MS, Nav, ResizeHandle, Sidebar, mountSidebar } from '../index.ts';
import { SIDEBAR_WIDTH, clampWidth, type NavState } from '../model.ts';
import { actions, state, win } from './fixtures.ts';

afterEach(cleanup);

const NAV: NavState = {
  items: [
    { id: 'terminals', label: 'Wall', lucide: 'layout-grid' },
    { id: 'work', label: 'Work', lucide: 'columns-3', badges: [
      { id: 'side-runs-count', title: 'Active runs', text: '0' },
      { id: 'side-pr-count', cls: 'badge-pr', title: 'Open PRs', text: '0 PR' }] },
  ],
  active: 'work',
  badges: {},
};

test('the nav rows: one per view, in order, its icon and label; the active one lit; a click selects its view', () => {
  const acts = actions();
  const { container } = render(<Nav nav={NAV} actions={acts} />);
  const rows = [...container.querySelectorAll<HTMLElement>('.side-item')];
  expect(rows.map(r => [r.dataset.view, r.title, r.querySelector('.side-item-label')?.textContent])).toEqual([
    ['terminals', 'Wall', 'Wall'], ['work', 'Work', 'Work']]);
  expect(rows[0]?.querySelector('svg.lucide-layout-grid')).toBeTruthy();
  expect(rows[1]?.querySelector('svg.lucide-columns-3')).toBeTruthy();
  expect(rows.map(r => r.classList.contains('active'))).toEqual([false, true]);
  fireEvent.click(rows[0] as HTMLElement);
  expect(acts.selectView.mock.calls).toEqual([['terminals']]);
});

test('the Work badges: their default text until work.js counts, then its counts, by id', () => {
  const { container, rerender } = render(<Nav nav={NAV} actions={actions()} />);
  const badge = (id: string) => container.querySelector<HTMLElement>(`#${id}`);
  expect([badge('side-runs-count')?.textContent, badge('side-pr-count')?.textContent]).toEqual(['0', '0 PR']);
  expect(badge('side-pr-count')?.className).toBe('badge badge-pr');
  expect(badge('side-runs-count')?.title).toBe('Active runs');
  rerender(<Nav nav={{ ...NAV, badges: { 'side-runs-count': '4', 'side-pr-count': '2 PR' } }} actions={actions()} />);
  expect([badge('side-runs-count')?.textContent, badge('side-pr-count')?.textContent]).toEqual(['4', '2 PR']);
  expect(container.querySelectorAll('.side-item')[0]?.querySelector('.side-badges')).toBeNull();
});

// --- the width handle --------------------------------------------------------------------

test('clampWidth: within bounds, rounded; anything not a number is the default', () => {
  expect(SIDEBAR_WIDTH).toEqual({ min: 264, max: 480, default: 288 });
  expect([300.4, 263, 264, 480, 481, -5, Infinity, NaN, '300', null, undefined].map(clampWidth))
    .toEqual([300, 264, 264, 480, 480, 264, 288, 288, 288, 288, 288]);
});

function handle(width: number | null, onCommit = vi.fn<(w: number) => void>()) {
  const { container } = render(<ResizeHandle width={width} onCommit={onCommit} />);
  return { h: container.querySelector('.sidebar-resize') as HTMLElement, onCommit };
}
const css = () => document.documentElement.style.getPropertyValue('--sidebar-w');

test('the handle is a focusable vertical separator carrying the clamped width', () => {
  const { h } = handle(9999);
  expect(h.getAttribute('role')).toBe('separator');
  expect(h.getAttribute('aria-orientation')).toBe('vertical');
  expect([h.getAttribute('aria-valuemin'), h.getAttribute('aria-valuemax'), h.getAttribute('aria-valuenow')]).toEqual(['264', '480', '480']);
  expect(h.tabIndex).toBe(0);
});

test('a drag moves the column live, clamped, and commits ONCE when let go', () => {
  const { h, onCommit } = handle(300);
  fireEvent.pointerDown(h, { clientX: 300, pointerId: 1 });
  expect(document.body.classList.contains('sidebar-resizing')).toBe(true);
  fireEvent.pointerMove(h, { clientX: 340 });
  expect(css()).toBe('340px');
  fireEvent.pointerMove(h, { clientX: 900 });
  expect(css()).toBe('480px');
  expect(onCommit).not.toHaveBeenCalled();
  fireEvent.pointerUp(h, { clientX: 900 });
  expect(onCommit.mock.calls).toEqual([[480]]);
  expect(document.body.classList.contains('sidebar-resizing')).toBe(false);
  fireEvent.pointerMove(h, { clientX: 100 }); // no drag in progress: nothing moves
  fireEvent.pointerUp(h);
  expect(css()).toBe('480px');
  expect(onCommit).toHaveBeenCalledTimes(1);
});

test('a drag narrower than the floor stops at it', () => {
  const { h, onCommit } = handle(288);
  fireEvent.pointerDown(h, { clientX: 288 });
  fireEvent.pointerMove(h, { clientX: 0 });
  fireEvent.pointerCancel(h);
  expect(onCommit.mock.calls).toEqual([[264]]);
});

test('keys step it by 16 px and jump to the bounds; a double-click restores the default', () => {
  const { h, onCommit } = handle(300);
  for (const key of ['ArrowLeft', 'ArrowRight', 'Home', 'End', 'a']) fireEvent.keyDown(h, { key });
  fireEvent.doubleClick(h);
  expect(onCommit.mock.calls).toEqual([[284], [316], [264], [480], [288]]);
  cleanup();
  const edge = handle(470);
  fireEvent.keyDown(edge.h, { key: 'ArrowRight' });
  expect(edge.onCommit.mock.calls).toEqual([[480]]);
});

// --- a long press shows the whole row ----------------------------------------------------

test('a long press shows the whole row and does not also open the session; a tap still opens it', () => {
  vi.useFakeTimers();
  try {
    const acts = actions();
    const { container } = render(<Sidebar state={state([win('a-very-long-session-title')])} actions={acts} />);
    const row = container.querySelector('.agent-row') as HTMLElement;
    fireEvent.touchStart(row);
    act(() => { vi.advanceTimersByTime(LONG_PRESS_MS - 1); });
    expect(row.classList.contains('show-full')).toBe(false);
    act(() => { vi.advanceTimersByTime(1); });
    expect(row.classList.contains('show-full')).toBe(true);
    fireEvent.touchEnd(row);
    fireEvent.click(row);
    expect(acts.selectAgent).not.toHaveBeenCalled();
    // the next touch puts the row back; a short tap opens the session
    fireEvent.touchStart(row);
    expect(row.classList.contains('show-full')).toBe(false);
    fireEvent.touchEnd(row);
    act(() => { vi.advanceTimersByTime(LONG_PRESS_MS); });
    fireEvent.click(row);
    expect(acts.selectAgent.mock.calls).toEqual([['a-very-long-session-title']]);
    expect(row.classList.contains('show-full')).toBe(false);
    // a press that turns into a scroll is not a long press
    fireEvent.touchStart(row);
    fireEvent.touchMove(row);
    act(() => { vi.advanceTimersByTime(LONG_PRESS_MS); });
    expect(row.classList.contains('show-full')).toBe(false);
  } finally {
    vi.useRealTimers();
  }
});

test('the hover and long-press rule wraps the title instead of truncating it (style.css)', async () => {
  const sheet = (await import('../../../../../static/style.css?raw')).default;
  expect(sheet).toMatch(/\.agent-row\.rich:hover \.agent-row-name,\s*\.agent-row\.rich\.show-full \.agent-row-name \{ white-space: normal;/);
  expect(sheet).toMatch(/grid-template-columns: var\(--sidebar-w, 288px\) 1fr;/);
});

// --- the island roots ---------------------------------------------------------------------

test('mountSidebar draws the nav, Recent and the handle in their slots, each repainting on its own store', () => {
  document.body.innerHTML = '<div id="side-nav"></div><div id="sidebar-agents"></div>'
    + '<div id="side-recent-island"></div><div id="sidebar-resize"></div>';
  const bridge = createBridge();
  const acts = actions();
  const root = mountSidebar(window, bridge, acts);
  expect(document.querySelector('#side-nav')?.innerHTML).toBe('');
  expect(document.querySelector('#side-recent-island')?.innerHTML).toBe('');
  expect(document.querySelector('.sidebar-resize')?.getAttribute('aria-valuenow')).toBe('288');

  bridge.nav.set(NAV);
  expect(document.querySelectorAll('#side-nav .side-item')).toHaveLength(2);
  bridge.recent.set({ rows: [{ store: 's', wid: '@5', session_id: 'sid', label: 'five' }], dispatcher_rows: [], revealed: false, undo: null });
  expect(document.querySelector('#side-recent-island .agent-row-name')?.textContent).toBe('five');
  bridge.agents.set([{ window_id: '@9', session_id: 'sid' }]); // that session is live now
  expect(document.querySelector('#side-recent-island')?.innerHTML).toBe('');
  bridge.sidebarLayout.set({ order: [], custom: null, view: null, width: 400, loaded: true });
  const h = document.querySelector('.sidebar-resize') as HTMLElement;
  expect(h.getAttribute('aria-valuenow')).toBe('400');
  fireEvent.keyDown(h, { key: 'End' });
  expect(acts.setSidebarWidth.mock.calls).toEqual([[480]]);
  act(() => root?.unmount());
  document.body.innerHTML = '';
});
