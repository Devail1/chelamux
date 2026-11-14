// CMX-381: the Manual sort. Under Manual (grouped by folder, custom group or none) the rows of
// a group drag into any order, kept per group in the server layout's `rows` by the rows'
// STABLE keys (CMX-378: the Claude session, never the window id), so the order survives a
// reload and a reboot. Under any other sort, or grouped by state or date, rows do not reorder;
// a session still drags onto a custom group (CMX-380).
//
// The model half is pure; the persisted half drives the real controller against a stubbed
// /api, as tests/dragWrite.test.ts does; the list half renders the island over that state.
import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { installBridge } from '../../../shared/bridge/index.ts';
import {
  deleteCustomGroup, dropOnGroup, dropRow, moveToGroup, refreshArchive, refreshLayout, renderSidebarAgents, viewMenuAction,
} from '../controller.ts';
import { LONG_PRESS_DRAG_MS, Sidebar } from '../index.ts';
import {
  type AgentRow, DISPATCHED_KEY, type Item, NEEDS_YOU_KEY, type RowOrder, ARCHIVED_SECTION_KEY, SORTS, UNGROUPED_KEY,
  buildItems, canOrderRows, customKey, groupSidebar, manualSort, normalizeView, placeRow, rowOrderKey, sidebarModel,
} from '../model.ts';
import { NOW, actions, wants, win } from './fixtures.ts';

const bridge = installBridge(window);
const HOUR = 3600e3;

// --- the model ---------------------------------------------------------------------------

test('Manual is a fourth sort, after Last activity / Name / Created, and a stored "manual" survives normalizeView', () => {
  expect(SORTS).toEqual(['activity', 'name', 'created', 'manual']);
  expect(normalizeView({ sort: 'manual' }).sort).toBe('manual');
});

test('canOrderRows: only under Manual, only by folder / custom group / none, never a pinned section', () => {
  for (const groupBy of ['folder', 'custom', 'none']) {
    expect([groupBy, canOrderRows({ groupBy, sort: 'manual' }, '/srv/a')]).toEqual([groupBy, true]);
  }
  for (const groupBy of ['state', 'date']) {
    expect([groupBy, canOrderRows({ groupBy, sort: 'manual' }, '~x')]).toEqual([groupBy, false]);
  }
  for (const sort of ['activity', 'name', 'created']) {
    expect([sort, canOrderRows({ groupBy: 'folder', sort }, '/srv/a')]).toEqual([sort, false]);
  }
  for (const k of [DISPATCHED_KEY, ARCHIVED_SECTION_KEY, NEEDS_YOU_KEY]) {
    expect([k, canOrderRows({ groupBy: 'folder', sort: 'manual' }, k)]).toEqual([k, false]);
  }
  expect(canOrderRows({ groupBy: 'folder', sort: 'manual' })).toBe(true);
});

/** Rows in one folder, each `name: [created h ago, active h ago]` before NOW. */
function items(spec: Record<string, [number, number]>, sid = true): Item[] {
  return buildItems(Object.entries(spec).map(([n, [c, a]]) => win(n, {
    cwd: '/srv/x', created: new Date(NOW - c * HOUR).toISOString(), last_activity: new Date(NOW - a * HOUR).toISOString(),
    ...(sid ? { session_id: `S-${n}` } : {}),
  })));
}
const names = (list: Item[]) => list.map(it => it.agent?.name);
const byActivity = (list: Item[]) => [...list].sort((a, b) => Date.parse(String(b.agent?.last_activity)) - Date.parse(String(a.agent?.last_activity)));

test('manualSort: the stored order first; a row created after it on top, any other unknown row below, both by Last activity', () => {
  const its = items({ a: [10, 3], b: [10, 2], c: [10, 1], fresh: [1, 1], fresh2: [0.5, 0.2], moved: [20, 0.1], moved2: [20, 5] });
  const order: RowOrder = { keys: ['s:S-c', 's:S-a', 's:S-b'], at: NOW - 2 * HOUR };
  expect(names(manualSort(its, order, byActivity))).toEqual(['fresh2', 'fresh', 'c', 'a', 'b', 'moved', 'moved2']);
});

test('manualSort: a row that left keeps no hole, and no stored order is plain Last activity', () => {
  const its = items({ a: [10, 3], c: [10, 1] });
  expect(names(manualSort(its, { keys: ['s:S-c', 's:S-gone', 's:S-a'], at: NOW }, byActivity))).toEqual(['c', 'a']);
  expect(names(manualSort(its, null, byActivity))).toEqual(['c', 'a']);
  expect(names(manualSort(its, { keys: [], at: NOW }, byActivity))).toEqual(['c', 'a']);
});

test('manualSort: a row stored under its window key (no session id yet) is still found by it', () => {
  const its = items({ a: [10, 1], b: [10, 2] }, false);
  const [a, b] = its as [Item, Item];
  expect(names(manualSort(its, { keys: [b.key, a.key], at: NOW }, byActivity))).toEqual(['b', 'a']);
});

test('placeRow: the drawn order by session key, the hidden stored keys kept after it, a gone one dropped', () => {
  const its = items({ a: [10, 3], b: [10, 2], c: [10, 1] });
  const [a, b, c] = its as [Item, Item, Item];
  const stored: RowOrder = { keys: ['s:S-hidden', 's:S-gone'], at: 1 };
  const AT = NOW - 10 * HOUR; // the newest `created` among the rows
  expect(placeRow(its, stored, c.key, a.key, NOW, k => k !== 's:S-gone'))
    .toEqual({ keys: ['s:S-c', 's:S-a', 's:S-b', 's:S-hidden'], at: AT });
  expect(placeRow(its, null, a.key, null, NOW)).toEqual({ keys: ['s:S-b', 's:S-c', 's:S-a'], at: AT });
  expect(placeRow(its, null, c.key, b.key, NOW)).toEqual({ keys: ['s:S-a', 's:S-c', 's:S-b'], at: AT });
});

test('placeRow: `at` is the newest row\'s server `created`, never this browser\'s clock; `now` only when none is dated', () => {
  const its = items({ a: [10, 3], b: [2, 2], c: [7, 1] });
  const [a, , c] = its as [Item, Item, Item];
  expect(placeRow(its, null, c.key, a.key, NOW + 99 * HOUR)?.at).toBe(NOW - 2 * HOUR);
  const undated = buildItems([win('u', { cwd: '/srv/x' }), win('v', { cwd: '/srv/x' })]);
  const [u, v] = undated as [Item, Item];
  expect(placeRow(undated, null, v.key, u.key, 1234)?.at).toBe(1234);
});

test('placeRow: a drawn row\'s old window key is dropped, never kept beside its session key', () => {
  const its = items({ a: [10, 3], b: [10, 2] });
  const [a, b] = its as [Item, Item];
  expect(placeRow(its, { keys: [a.key, 's:S-other'], at: 1 }, b.key, a.key, NOW)?.keys).toEqual(['s:S-b', 's:S-a', 's:S-other']);
});

test('placeRow: a drop that changes nothing, or names a row not in the group, is null', () => {
  const its = items({ a: [10, 3], b: [10, 2], c: [10, 1] });
  const [a, b, c] = its as [Item, Item, Item];
  expect(placeRow(its, null, a.key, b.key, NOW)).toBeNull(); // already just above b
  expect(placeRow(its, null, c.key, null, NOW)).toBeNull(); // already last
  expect(placeRow(its, null, a.key, a.key, NOW)).toBeNull();
  expect(placeRow(its, null, 'w:@nope', a.key, NOW)).toBeNull();
  expect(placeRow(its, null, a.key, 'w:@nope', NOW)).toBeNull();
});

test('groupSidebar: Manual reads `rows` per group; by state or date, Manual is Last activity', () => {
  const rows = [
    win('a', { cwd: '/srv/x', session_id: 'S-a', last_activity: new Date(NOW - 3 * HOUR).toISOString() }),
    win('b', { cwd: '/srv/x', session_id: 'S-b', last_activity: new Date(NOW - 2 * HOUR).toISOString() }),
  ];
  const order = { '/srv/x': { keys: ['s:S-a', 's:S-b'], at: NOW }, '~state:idle': { keys: ['s:S-a', 's:S-b'], at: NOW },
    '~date:today': { keys: ['s:S-a', 's:S-b'], at: NOW } };
  const drawn = (mode: string, sort: string) => groupSidebar(rows, { mode, wants, sort, now: NOW, rows: order })
    .groups.map(g => [g.key, g.items.map(it => it.agent?.name)]);
  expect(drawn('folder', 'manual')).toEqual([['/srv/x', ['a', 'b']]]);
  expect(drawn('folder', 'activity')).toEqual([['/srv/x', ['b', 'a']]]);
  expect(drawn('state', 'manual')).toEqual([['~state:idle', ['b', 'a']]]);
  expect(drawn('date', 'manual')).toEqual([['~date:today', ['b', 'a']]]);
});

// --- persisted: the real controller against a stubbed /api ------------------------------

let server: Record<string, unknown> = {};
let posts: Record<string, unknown>[] = [];
let answer: 'ok' | 'refused' = 'ok';
/** What GET /api/sidebar/archive answers (the server's archive). */
let archive: Record<string, unknown> = { hidden: [], sessions: [] };
const alerts = vi.fn();

beforeEach(() => {
  document.body.innerHTML = '<div id="sidebar-agents"></div>';
  posts = [];
  answer = 'ok';
  alerts.mockReset();
  vi.stubGlobal('alert', alerts);
  vi.stubGlobal('fetch', (url: string, opts?: RequestInit) => {
    const method = opts?.method ?? 'GET';
    if (url.endsWith('/api/sidebar/archive') && method === 'GET') {
      return Promise.resolve({ json: () => Promise.resolve(archive) });
    }
    if (url.endsWith('/api/sidebar/layout') && method === 'GET') {
      return Promise.resolve({ json: () => Promise.resolve({ layout: server }) });
    }
    if (url.endsWith('/api/sidebar/layout') && method === 'POST') {
      const body = JSON.parse(String(opts?.body)) as Record<string, unknown>;
      posts.push(body);
      if (answer === 'refused') return Promise.resolve({ json: () => Promise.resolve({ ok: false, error: 'disk full' }) });
      server = { ...server, ...body };
      return Promise.resolve({ json: () => Promise.resolve({ ok: true, layout: server }) });
    }
    return Promise.reject(new Error(`unexpected ${method} ${url}`));
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const flush = () => new Promise(r => setTimeout(r, 0));

/** Three sessions in /srv/x, most recently active first: a, b, c (Last activity's order).
 * `epoch` renumbers the windows, as a reboot's `--resume` does; the sessions stay. */
function sessions(epoch = 0, extra: AgentRow[] = []): AgentRow[] {
  return [
    ...['a', 'b', 'c'].map((n, i) => ({
      ...win(n, { cwd: '/srv/x', session_id: `S-${n}`, created: new Date(NOW - 10 * HOUR).toISOString() }),
      window_id: `@${epoch * 100 + i + 1}`, last_activity: new Date(NOW - (i + 1) * HOUR).toISOString(),
    })),
    ...extra,
  ];
}

const MANUAL = { view: { groupBy: 'folder', sort: 'manual', activity: 'all' } };

/** Load `layout` as the server's (a reload) and draw `rows`. */
async function load(layout: Record<string, unknown>, rows = sessions()) {
  server = { order: [], ...layout };
  await refreshLayout();
  bridge.agents.set(rows as never);
  renderSidebarAgents(rows);
}

const drawn = (key = '/srv/x') => {
  const s = bridge.sidebar.get();
  return s ? sidebarModel(s).groups.find(g => g.key === key)?.items.map(it => it.agent?.name) : undefined;
};
const itemOf = (name: string) => {
  const s = bridge.sidebar.get();
  return s ? sidebarModel(s).groups.flatMap(g => g.items).find(it => it.agent?.name === name)?.key ?? '' : '';
};
const lastRows = () => posts.filter(p => 'rows' in p).at(-1)?.rows as Record<string, RowOrder> | undefined;

/** The list as the island draws the controller's state, each section and row 30px tall. */
function drawList(over: Partial<ReturnType<typeof actions>> = {}) {
  const acts = { ...actions(), ...over };
  const host = document.getElementById('sidebar-agents') as HTMLElement;
  const s = bridge.sidebar.get();
  render(<Sidebar state={s} actions={acts} />, { container: host });
  let y = 0;
  for (const sec of [...host.children]) {
    const top = y;
    const head = sec.querySelector('.group-head');
    if (head) y += 30;
    for (const r of sec.querySelectorAll('.agent-row')) { box(r, y, y + 30); y += 30; }
    box(sec, top, y);
  }
  box(host, 0, y + 100);
  const row = (n: string) => host.querySelector(`.agent-row[data-agent="${n}"]`) as HTMLElement;
  return { el: host, acts, row };
}

function box(el: Element, top: number, bottom: number) {
  Object.defineProperty(el, 'getBoundingClientRect', {
    configurable: true,
    value: () => ({ top, bottom, left: 0, right: 240, width: 240, height: bottom - top, x: 0, y: top, toJSON() {} }),
  });
}

const pt = (x: number, y: number, over: Partial<PointerEventInit> = {}) =>
  ({ pointerId: 1, isPrimary: true, pointerType: 'mouse', button: 0, clientX: x, clientY: y, ...over });

/** Drag row `from` to (y) and let go. */
function drag(row: HTMLElement, from: number, to: number) {
  fireEvent.pointerDown(row, pt(20, from));
  fireEvent.pointerMove(window, pt(20, (from + to) / 2));
  fireEvent.pointerMove(window, pt(20, to));
  fireEvent.pointerUp(window, pt(20, to));
}

describe('Manual + folder grouping', () => {
  test('drag row C above row A; after a reload the order is still C, A, B', async () => {
    await load(MANUAL);
    expect(drawn()).toEqual(['a', 'b', 'c']); // nothing ordered yet: Last activity
    const { el, row } = drawList({ dropRow: vi.fn(dropRow) });
    // header 0-30, a 30-60, b 60-90, c 90-120: c to the top half of a
    fireEvent.pointerDown(row('c'), pt(20, 105));
    fireEvent.pointerMove(window, pt(20, 70));
    fireEvent.pointerMove(window, pt(20, 35));
    expect(row('a').classList.contains('drop-before')).toBe(true);
    expect(row('c').classList.contains('dragging')).toBe(true);
    fireEvent.pointerUp(window, pt(20, 35));
    expect(el.querySelector('.drop-before, .drop-after')).toBeNull();
    // optimistic: drawn before the server answers
    expect(drawn()).toEqual(['c', 'a', 'b']);
    await flush();
    expect(posts).toEqual([{ rows: { '/srv/x': { keys: ['s:S-c', 's:S-a', 's:S-b'], at: expect.any(Number) } } }]);
    // the click that ends the drag opens nothing
    fireEvent.click(row('c'));
    await load(server); // a reload: a fresh read of the server's layout
    expect(drawn()).toEqual(['c', 'a', 'b']);
  });

  test('the grip shows on every row of a headed folder, and Esc cancels a row drag', async () => {
    await load(MANUAL);
    const { el, acts, row } = drawList();
    expect(['a', 'b', 'c'].map(n => !!row(n).querySelector('.drag-grip'))).toEqual([true, true, true]);
    fireEvent.pointerDown(row('c'), pt(20, 105));
    fireEvent.pointerMove(window, pt(20, 35));
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(el.querySelector('.drop-before')).toBeNull();
    fireEvent.pointerUp(window, pt(20, 35));
    expect(acts.dropRow).not.toHaveBeenCalled();
  });

  test('on a phone a long press starts the row drag, as a group drag does', async () => {
    await load(MANUAL);
    vi.useFakeTimers();
    try {
      const { acts, row } = drawList();
      const touch = (y: number) => pt(20, y, { pointerType: 'touch' });
      fireEvent.pointerDown(row('c'), touch(105));
      fireEvent.pointerMove(window, touch(104)); // within the slop: still a press
      act(() => { vi.advanceTimersByTime(LONG_PRESS_DRAG_MS); });
      expect(document.body.classList.contains('sidebar-dragging')).toBe(true);
      fireEvent.pointerMove(window, touch(35));
      fireEvent.pointerUp(window, touch(35));
      expect(acts.dropRow.mock.calls).toEqual([['/srv/x', itemOf('c'), itemOf('a')]]);
    } finally {
      vi.useRealTimers();
    }
  });

  test('a row dropped on another folder is refused: nothing is written', async () => {
    const other = [win('y1', { cwd: '/srv/y' }), win('y2', { cwd: '/srv/y' })];
    await load(MANUAL, sessions(0, other));
    const { el, acts, row } = drawList();
    const y = el.querySelector('.side-group[data-g="/srv/y"] .agent-row') as HTMLElement;
    const yTop = y.getBoundingClientRect().top;
    fireEvent.pointerDown(row('a'), pt(20, 45));
    fireEvent.pointerMove(window, pt(20, yTop + 5));
    expect(el.querySelector('.side-group[data-g="/srv/y"]')?.classList.contains('drop-reject')).toBe(true);
    fireEvent.pointerUp(window, pt(20, yTop + 5));
    expect(acts.dropRow).not.toHaveBeenCalled();
    expect(acts.dropOnGroup).not.toHaveBeenCalled();
  });

  test('a new session goes to the top of its group; a session that leaves keeps no hole', async () => {
    await load(MANUAL);
    expect(dropRow('/srv/x', itemOf('c'), itemOf('a'))).toBe(true);
    await flush();
    const newer = win('n', { cwd: '/srv/x', session_id: 'S-n', created: new Date(Date.now() + 1000).toISOString() });
    await load(server, [...sessions().filter(a => a.name !== 'a'), newer]);
    expect(drawn()).toEqual(['n', 'c', 'b']);
  });

  test('switching to another sort and back to Manual restores the saved order', async () => {
    await load(MANUAL);
    dropRow('/srv/x', itemOf('c'), itemOf('a'));
    await flush();
    viewMenuAction('sort:name');
    await flush();
    expect(drawn()).toEqual(['a', 'b', 'c']);
    viewMenuAction('sort:manual');
    await flush();
    expect(drawn()).toEqual(['c', 'a', 'b']);
    await load(server);
    expect(drawn()).toEqual(['c', 'a', 'b']);
  });

  test('a refused write snaps the rows back and says why', async () => {
    await load(MANUAL);
    answer = 'refused';
    dropRow('/srv/x', itemOf('c'), itemOf('a'));
    expect(drawn()).toEqual(['c', 'a', 'b']);
    await flush();
    expect(drawn()).toEqual(['a', 'b', 'c']);
    expect(alerts.mock.calls).toEqual([['Moving the session failed: disk full. It was put back.']]);
  });

  test('a stored key of a session that is gone is dropped from the next write; a filtered-out live one is kept', async () => {
    // h is a live judge window: the default Environment filter hides it
    const hidden = win('judge-h', { cwd: '/srv/x', session_id: 'S-h' });
    const rows = { '/srv/x': { keys: ['s:S-gone', 's:S-arch', 's:S-h'], at: 1 } };
    // before the archive has answered nothing is known to be gone: every key is kept
    await load({ ...MANUAL, rows }, sessions(0, [hidden]));
    expect(drawn()).toEqual(['a', 'b', 'c']);
    expect(dropRow('/srv/x', itemOf('c'), itemOf('a'))).toBe(true);
    await flush();
    expect(lastRows()?.['/srv/x']?.keys).toEqual(['s:S-c', 's:S-a', 's:S-b', 's:S-gone', 's:S-arch', 's:S-h']);
    // once it has, a gone one is dropped; a live hidden one and an archived one are kept
    archive = { hidden: [], sessions: [{ key: 's:S-arch', session_id: 'S-arch' }] };
    await refreshArchive();
    await load({ ...MANUAL, rows }, sessions(0, [hidden]));
    expect(dropRow('/srv/x', itemOf('c'), itemOf('a'))).toBe(true);
    await flush();
    expect(lastRows()?.['/srv/x']?.keys).toEqual(['s:S-c', 's:S-a', 's:S-b', 's:S-arch', 's:S-h']);
    archive = { hidden: [], sessions: [] };
    await refreshArchive(); // no Archived section in the tests after this one
  });

  test('deleting a custom group drops its manual row order with it', async () => {
    const groups = [{ id: 'g1', name: 'Infra' }];
    const rows = { [customKey('g1')]: { keys: ['s:S-a'], at: 1 }, '/srv/x': { keys: ['s:S-b'], at: 1 } };
    await load({ view: { groupBy: 'custom', sort: 'manual', activity: 'all' }, custom: { groups, assign: { 's:S-a': 'g1' } }, rows });
    deleteCustomGroup('g1');
    await flush();
    const del = posts.filter(p => (p.custom as { groups?: unknown[] } | undefined)?.groups?.length === 0);
    expect(del[0]?.rows).toEqual({ '/srv/x': { keys: ['s:S-b'], at: 1 } });
    expect(server.rows).toEqual({ '/srv/x': { keys: ['s:S-b'], at: 1 } });
  });
});

describe('the reboot (window ids change, session ids stay)', () => {
  test('the manual order holds, keyed by the session', async () => {
    await load(MANUAL, sessions(0));
    dropRow('/srv/x', itemOf('c'), itemOf('a'));
    await flush();
    expect(lastRows()?.['/srv/x']?.keys).toEqual(['s:S-c', 's:S-a', 's:S-b']);
    // every window renumbered: @1..@3 → @901..@903
    await load(server, sessions(9));
    expect(bridge.sidebar.get()?.agents.map(a => a.window_id)).toEqual(['@901', '@902', '@903']);
    expect(drawn()).toEqual(['c', 'a', 'b']);
  });

  test('rowOrderKey is the session key, never the window', () => {
    const it = buildItems([win('a', { session_id: 'S-a' })])[0] as Item;
    expect(rowOrderKey(it)).toBe('s:S-a');
    expect(rowOrderKey(it)).not.toBe(it.key);
  });
});

describe('no row reordering under another sort, or grouped by state', () => {
  test('Name sort: rows show no grip, and a row drag is refused', async () => {
    await load({ view: { groupBy: 'folder', sort: 'name', activity: 'all' } });
    const { el, acts, row } = drawList({ dropRow: vi.fn(dropRow) });
    expect(el.querySelector('.agent-row .drag-grip')).toBeNull();
    expect(row('a').classList.contains('draggable')).toBe(false);
    drag(row('c'), 105, 35);
    expect(el.querySelector('.drop-before, .drop-after')).toBeNull();
    expect(acts.dropRow).not.toHaveBeenCalled();
    // and the controller refuses it too: nothing is written
    expect(dropRow('/srv/x', itemOf('c'), itemOf('a'))).toBe(false);
    await flush();
    expect(posts).toEqual([]);
  });

  test('State grouping + Manual: the row drag is refused, and assigning to a custom group still works', async () => {
    const groups = [{ id: 'g1', name: 'Infra' }];
    await load({ view: { groupBy: 'state', sort: 'manual', activity: 'all' }, custom: { groups, assign: {} } });
    const { el, acts, row } = drawList({ dropRow: vi.fn(dropRow) });
    expect(el.querySelector('.agent-row .drag-grip')).toBeNull();
    drag(row('c'), 105, 35);
    expect(acts.dropRow).not.toHaveBeenCalled();
    expect(dropRow('~state:idle', itemOf('c'), itemOf('a'))).toBe(false);
    await flush();
    expect(posts).toEqual([]);
    moveToGroup('s:S-c', 'g1');
    await flush();
    expect(posts.map(p => (p.custom as { assign?: unknown } | undefined)?.assign)).toEqual([{ 's:S-c': 'g1' }]);
  });

  test('Custom groups + Name: a session still drags onto a custom group, but not within its own', async () => {
    const groups = [{ id: 'g1', name: 'Infra' }, { id: 'g2', name: 'Web' }];
    await load({ view: { groupBy: 'custom', sort: 'name', activity: 'all' }, custom: { groups, assign: { 's:S-a': 'g2' } } });
    const { el, acts, row } = drawList();
    expect([...el.children].map(c => (c as HTMLElement).dataset.g)).toEqual([customKey('g2'), UNGROUPED_KEY]);
    // Web 0-60 (head, a), Ungrouped 60-150 (head, b, c): c within Ungrouped is no reorder
    drag(row('c'), 135, 95);
    expect(acts.dropRow).not.toHaveBeenCalled();
    expect(acts.dropOnGroup).not.toHaveBeenCalled();
    drag(row('c'), 135, 45);
    expect(acts.dropOnGroup.mock.calls).toEqual([['s:S-c', customKey('g2')]]);
    expect(dropOnGroup('s:S-c', customKey('g1'))).toBe(true);
  });

  test('Custom groups + Manual: a row reorders within its group AND still drops onto another', async () => {
    const groups = [{ id: 'g2', name: 'Web' }];
    await load({ view: { groupBy: 'custom', sort: 'manual', activity: 'all' }, custom: { groups, assign: { 's:S-a': 'g2' } } });
    const { acts, row } = drawList();
    // Web 0-60 (head, a), Ungrouped 60-150 (head, b 90-120, c 120-150)
    drag(row('c'), 135, 95);
    expect(acts.dropRow.mock.calls).toEqual([[UNGROUPED_KEY, itemOf('c'), itemOf('b')]]);
    drag(row('c'), 135, 45);
    expect(acts.dropOnGroup.mock.calls).toEqual([['s:S-c', customKey('g2')]]);
  });
});
