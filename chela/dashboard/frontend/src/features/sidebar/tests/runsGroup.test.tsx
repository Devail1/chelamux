// CMX-438: grouped by Custom groups, every dispatched run lands in ONE automatic "Runs" group,
// pinned at the top, whether or not a custom group exists (Liav, 11-18). A run is never in
// Ungrouped nor in a custom group, cannot be dragged or moved into one, and its judge rides
// inside its row. The orchestrator is a session, not a run. No runs, or dispatch off by
// choice (CMX-388): no Runs header.
import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { installBridge } from '../../../shared/bridge/index.ts';
import { BOOT } from '../../../shared/api/index.ts';
import { dropOnGroup, moveToGroup, refreshLayout, renderSidebarAgents } from '../controller.ts';
import { Sidebar } from '../index.ts';
import {
  type AgentRow, type Custom, DISPATCHED_KEY, UNGROUPED_KEY, customKey, groupSidebar, isRunsRow, buildItems,
  moveGroup, normalizeView, placeGroup, sidebarModel,
} from '../model.ts';
import { NOW, actions, runWin, state, wants, win } from './fixtures.ts';

const bridge = installBridge(window);

afterEach(() => {
  cleanup();
  document.body.innerHTML = '';
});

const G: Custom['groups'] = [{ id: 'g1', name: 'Infra' }, { id: 'g2', name: 'Web' }];
const names = (it: { agent: AgentRow | null; run?: { task_id?: string } }) => it.run?.task_id ?? it.agent?.name;
const layout = (agents: AgentRow[], over: Record<string, unknown> = {}) =>
  groupSidebar(agents, { mode: 'custom', wants, now: NOW, custom: { groups: G, assign: {} }, ...over }).groups
    .map(g => [g.label, g.items.map(names)]);

// --- the model --------------------------------------------------------------------------

test('ACCEPTANCE: under Custom groups a run lands in Runs, pinned on top, never in Ungrouped or a custom group', () => {
  const mine = win('mine', { session_id: 'S-mine' });
  const infra = win('infra', { session_id: 'S-infra' });
  const run = runWin('CMX-1', 'agent');
  const judge = runWin('CMX-1', 'judge');
  const assigned = runWin('CMX-2', 'agent');
  // CMX-2 carries a stale assignment from before (a run used to be assignable): ignored
  const custom = { groups: G, assign: { 's:S-infra': 'g1', 'run:CMX-2': 'g2' } };
  // negative control: today's behaviour put CMX-1 in Ungrouped and CMX-2 in Web
  expect(layout([mine, infra, run, judge, assigned], { custom })).toEqual([
    ['Runs', ['CMX-1', 'CMX-2']],
    ['Infra', ['infra']],
    ['Ungrouped', ['mine']],
  ]);
});

test('Runs is there whether or not a custom group exists, and is still on top', () => {
  const out = layout([win('mine'), runWin('CMX-1', 'agent')], { custom: { groups: [], assign: {} } });
  expect(out).toEqual([['Runs', ['CMX-1']], ['Ungrouped', ['mine']]]);
});

test('a stored group order that names Runs never lifts a custom group above it', () => {
  const c = { groups: G, assign: { 's:S-a': 'g2' } };
  const out = groupSidebar([win('a', { session_id: 'S-a' }), runWin('CMX-1', 'agent')], {
    mode: 'custom', wants, now: NOW, custom: c, order: [customKey('g2'), UNGROUPED_KEY, DISPATCHED_KEY],
  }).groups.map(g => g.key);
  expect(out).toEqual([DISPATCHED_KEY, customKey('g2')]);
});

test("a run's judge gets no group of its own: it rides inside the run's row", () => {
  const groups = groupSidebar([runWin('CMX-1', 'agent'), runWin('CMX-1', 'judge')],
    { mode: 'custom', wants, now: NOW, custom: { groups: G, assign: {} } }).groups;
  expect(groups.map(g => g.key)).toEqual([DISPATCHED_KEY]);
  const [row] = groups[0]?.items ?? [];
  expect(row?.judge?.name).toBe('judge-liavacc/cmx-1');
});

test('a session the dispatcher owns (a worktree, a dispatched window) is in Runs too', () => {
  const wt = win('wt', { cwd: '/home/u/.chela/worktrees/x/CMX-9' });
  const flagged = win('flagged', { dispatched: true });
  expect(layout([wt, flagged, win('mine')])).toEqual([['Runs', ['wt', 'flagged']], ['Ungrouped', ['mine']]]);
});

test('the orchestrator is a session, not a run: it stays where it was put', () => {
  // even one sitting in a worktree, carrying the dispatched flag
  const orch = win('orch', { session_id: 'S-o', dispatched: true });
  const custom = { groups: G, assign: { 's:S-o': 'g1' } };
  const out = groupSidebar([orch, runWin('CMX-1', 'agent')],
    { mode: 'custom', wants, now: NOW, custom, orchWid: orch.window_id }).groups.map(g => [g.label, g.items.map(names)]);
  expect(out).toEqual([['Runs', ['CMX-1']], ['Infra', ['orch']]]);
  const [it] = buildItems([orch]);
  expect(it && isRunsRow(it, { orchWid: orch.window_id })).toBe(false);
  expect(it && isRunsRow(it, {})).toBe(true);
});

test('no runs: no Runs header, even with Show empty groups on', () => {
  expect(layout([win('mine')])).toEqual([['Ungrouped', ['mine']]]);
  expect(layout([win('mine')], { showEmpty: true }).map(([l]) => l)).toEqual(['Infra', 'Web', 'Ungrouped']);
});

test('dispatch off by choice: no Runs header; a leftover run is a session like any other', () => {
  const run = runWin('CMX-1', 'agent');
  const custom = { groups: G, assign: { 'run:CMX-1': 'g2' } };
  expect(layout([win('mine'), run], { dispatchOff: true, custom })).toEqual([
    ['Web', ['CMX-1']], ['Ungrouped', ['mine']],
  ]);
  expect(layout([win('mine'), run], { dispatchOff: true, showEmpty: true }).map(([l]) => l))
    .toEqual(['Infra', 'Web', 'Ungrouped']);
  const [it] = buildItems([run]);
  expect(it && isRunsRow(it, { dispatchOff: true })).toBe(false);
});

test('grouped by folder nothing changes: Dispatched keeps its name and stays last', () => {
  const g = groupSidebar([win('mine', { cwd: '/srv/a' }), runWin('CMX-1', 'agent')],
    { mode: 'folder', wants, now: NOW, order: [DISPATCHED_KEY, '/srv/a'] }).groups;
  expect(g.map(x => [x.key, x.label])).toEqual([['/srv/a', 'a'], [DISPATCHED_KEY, 'Dispatched']]);
  // an archived run leaves its folder group empty: Show empty still draws Dispatched there
  const done = runWin('CMX-2', 'agent', { status: 'done' }, { claude_running: false });
  const [it] = buildItems([done]);
  const empty = groupSidebar([win('mine', { cwd: '/srv/a' }), done],
    { mode: 'folder', wants, now: NOW, showEmpty: true, archived: new Set([it?.key ?? '']) });
  expect(empty.groups.map(x => [x.key, x.items.length])).toEqual([['/srv/a', 1], [DISPATCHED_KEY, 0]]);
});

// --- the pinned group never moves, and nothing moves above it ------------------------------

test('placeGroup with Runs on top: a group dropped before Runs lands first below it; Runs never moves', () => {
  const keys = [DISPATCHED_KEY, customKey('a'), customKey('b'), UNGROUPED_KEY];
  expect(placeGroup(keys, [], UNGROUPED_KEY, DISPATCHED_KEY))
    .toEqual([DISPATCHED_KEY, UNGROUPED_KEY, customKey('a'), customKey('b')]);
  expect(placeGroup(keys, [], customKey('a'), null))
    .toEqual([DISPATCHED_KEY, customKey('b'), UNGROUPED_KEY, customKey('a')]);
  expect(placeGroup(keys, [], customKey('b'), customKey('a')))
    .toEqual([DISPATCHED_KEY, customKey('b'), customKey('a'), UNGROUPED_KEY]);
  expect(placeGroup(keys, [], customKey('a'), DISPATCHED_KEY)).toBeNull(); // already first below it
  expect(placeGroup(keys, [], DISPATCHED_KEY, null)).toBeNull();
  // no pinned group at all: a plain list
  expect(placeGroup(['/a', '/b'], [], '/b', '/a')).toEqual(['/b', '/a']);
});

test('moveGroup: nothing trades places with the pinned group, top or bottom', () => {
  expect(moveGroup([DISPATCHED_KEY, '/a', '/b'], [], '/a', -1)).toBeNull();
  expect(moveGroup(['/a', '/b', DISPATCHED_KEY], [], '/b', 1)).toBeNull();
  expect(moveGroup(['/a', DISPATCHED_KEY], [], DISPATCHED_KEY, -1)).toBeNull();
  expect(moveGroup([DISPATCHED_KEY, '/a', '/b'], [], '/a', 1)).toEqual([DISPATCHED_KEY, '/b', '/a']);
});

// --- the list: header, drag, menu ------------------------------------------------------------

function box(el: Element, top: number, bottom: number) {
  Object.defineProperty(el, 'getBoundingClientRect', {
    configurable: true,
    value: () => ({ top, bottom, left: 0, right: 240, width: 240, height: bottom - top, x: 0, y: top, toJSON() {} }),
  });
}
const pt = (x: number, y: number) =>
  ({ pointerId: 1, isPrimary: true, pointerType: 'mouse', button: 0, clientX: x, clientY: y });
const customView = normalizeView({ groupBy: 'custom', activity: 'all', sort: 'name' });

function drawList(agents: AgentRow[], over = {}) {
  const acts = actions();
  const host = document.createElement('div');
  host.id = 'sidebar-agents';
  document.body.appendChild(host);
  render(<Sidebar state={state(agents, { view: customView, custom: { groups: G, assign: { 's:S-o': 'g1' } }, ...over })}
    actions={acts} />, { container: host });
  return { el: host, acts };
}
const heads = (el: Element) => [...el.querySelectorAll('.group-head .group-name')].map(n => n.textContent);

test('the list draws a Runs header on top, with its count; none with no runs or dispatch off', () => {
  const agents = [win('other', { session_id: 'S-o' }), win('mine'), runWin('CMX-1', 'agent'), runWin('CMX-2', 'agent')];
  let { el } = drawList(agents);
  expect(heads(el)).toEqual(['Runs', 'Infra', 'Ungrouped']);
  expect(el.querySelector(`.side-group[data-g="${DISPATCHED_KEY}"] .group-count`)?.textContent).toBe('2');
  cleanup();
  ({ el } = drawList([win('other', { session_id: 'S-o' }), win('mine')]));
  expect(heads(el)).toEqual(['Infra', 'Ungrouped']);
  cleanup();
  ({ el } = drawList(agents, { dispatchOff: true }));
  expect(heads(el)).toEqual(['Infra', 'Ungrouped']);
  expect(el.textContent).not.toContain('Runs');
});

test('ACCEPTANCE: a run dragged onto a custom group is refused, and stays in Runs', () => {
  const run = runWin('CMX-1', 'agent');
  const { el, acts } = drawList([win('other', { session_id: 'S-o' }), run]);
  const secs = [...el.children];
  expect(secs.map(s => (s as HTMLElement).dataset.g)).toEqual([DISPATCHED_KEY, customKey('g1')]);
  secs.forEach((g, i) => box(g, i * 40, i * 40 + 40));
  box(el, 0, 200);
  const row = el.querySelector(`.agent-row[data-agent="${run.name}"]`) as HTMLElement;
  expect(row.closest('.side-group')?.getAttribute('data-g')).toBe(DISPATCHED_KEY);
  fireEvent.pointerDown(row, pt(20, 20));
  fireEvent.pointerMove(window, pt(20, 60));
  expect(el.querySelector('.drop-target')).toBeNull();
  fireEvent.pointerUp(window, pt(20, 60));
  expect(acts.dropOnGroup).not.toHaveBeenCalled();
  // the drag never started, so the press is still a click
  fireEvent.click(row);
  expect(acts.selectAgent).toHaveBeenCalled();
  // while a session in the same list still drags onto a group
  const other = el.querySelector('.agent-row[data-agent="other"]') as HTMLElement;
  fireEvent.pointerDown(other, pt(20, 60));
  fireEvent.pointerMove(window, pt(20, 150));
  fireEvent.pointerUp(window, pt(20, 150));
  expect(acts.dropOnGroup.mock.calls).toEqual([['s:S-o', UNGROUPED_KEY]]);
});

test('a run row offers no Move to group…; a session row still does', () => {
  const run = runWin('CMX-1', 'agent');
  const { el } = drawList([win('other', { session_id: 'S-o' }), run]);
  const items = () => [...document.querySelectorAll('[role="menu"] [role^="menuitem"]')].map(i => i.textContent);
  fireEvent.contextMenu(el.querySelector(`.side-group[data-g="${DISPATCHED_KEY}"] .agent-row`) as Element);
  expect(items()).not.toContain('Move to group…');
  expect(items()).toContain('Open judge pane');
  cleanup();
  const again = drawList([win('other', { session_id: 'S-o' }), run]);
  fireEvent.contextMenu(again.el.querySelector('.agent-row[data-agent="other"]') as Element);
  expect(items()).toContain('Move to group…');
});

// --- the controller: no write ever assigns a run --------------------------------------------

let posts: Record<string, unknown>[] = [];
let server: Record<string, unknown> = {};

beforeEach(() => {
  posts = [];
  vi.stubGlobal('alert', vi.fn());
  vi.stubGlobal('fetch', (url: string, opts?: RequestInit) => {
    const method = opts?.method ?? 'GET';
    if (url.endsWith('/api/sidebar/layout') && method === 'GET') {
      return Promise.resolve({ json: () => Promise.resolve({ layout: server }) });
    }
    if (url.endsWith('/api/sidebar/layout') && method === 'POST') {
      const body = JSON.parse(String(opts?.body)) as Record<string, unknown>;
      posts.push(body);
      server = { ...server, ...body };
      return Promise.resolve({ json: () => Promise.resolve({ ok: true, layout: server }) });
    }
    return Promise.reject(new Error(`unexpected ${method} ${url}`));
  });
});
afterEach(() => {
  vi.unstubAllGlobals();
  BOOT.dispatchOffByChoice = false;
});

const flush = () => new Promise(r => setTimeout(r, 0));
const ROWS: AgentRow[] = [win('a1', { cwd: '/srv/a', session_id: 'S-a1' }), runWin('CMX-1', 'agent')];

async function load() {
  document.body.innerHTML = '<div id="sidebar-agents"></div>';
  server = { order: [], view: { groupBy: 'custom' }, custom: { groups: G, assign: {} } };
  await refreshLayout();
  bridge.agents.set(ROWS as never);
  renderSidebarAgents(ROWS);
}
const runsNow = () => {
  const s = bridge.sidebar.get();
  return s ? sidebarModel(s).groups.find(g => g.key === DISPATCHED_KEY)?.items.map(names) : undefined;
};

test('ACCEPTANCE: dropping a run on a custom group writes nothing, and the run stays in Runs', async () => {
  await load();
  expect(runsNow()).toEqual(['CMX-1']);
  expect(dropOnGroup('run:CMX-1', customKey('g1'))).toBe(false);
  expect(dropOnGroup('run:CMX-1', UNGROUPED_KEY)).toBe(false);
  moveToGroup('run:CMX-1', 'g2');
  await flush();
  expect(posts.filter(p => 'custom' in p)).toEqual([]);
  expect(runsNow()).toEqual(['CMX-1']);
  // negative control on the same page: a session's drop is written
  expect(dropOnGroup('s:S-a1', customKey('g1'))).toBe(true);
  await flush();
  expect(posts.filter(p => 'custom' in p)).toHaveLength(1);
});

test('dispatch off by choice: a leftover run is a session, and may be put in a group', async () => {
  BOOT.dispatchOffByChoice = true;
  await load();
  expect(runsNow()).toBeUndefined();
  expect(dropOnGroup('run:CMX-1', customKey('g1'))).toBe(true);
  await flush();
  const c = posts.filter(p => 'custom' in p).at(-1)?.custom as { assign?: unknown } | undefined;
  expect(c?.assign).toEqual({ 'run:CMX-1': 'g1' });
});
