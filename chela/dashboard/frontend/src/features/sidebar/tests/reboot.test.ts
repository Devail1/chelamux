// CMX-378 through the real controller, against a stubbed /api: what a render WRITES of the
// session-keyed custom groups. A reboot (or a tmux restart) brings the sessions back under NEW
// window ids, one by one, while this page may not have the archive yet: nothing may be pruned
// before the archive answers, and every session lands in its group on its first draw.
// This file's FIRST tests run before the archive has ever answered (the archive is module
// state), so keep them first.
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { installBridge } from '../../../shared/bridge/index.ts';
import { moveToGroup, refreshArchive, refreshLayout, renderSidebarAgents } from '../controller.ts';
import { type AgentRow, customKey, sidebarModel } from '../model.ts';
import { win } from './fixtures.ts';

const bridge = installBridge(window);
const DAY = 86400000;

let server: Record<string, unknown> = {};
let archive: Record<string, unknown> = { hidden: [], sessions: [] };
let posts: Record<string, unknown>[] = [];

beforeEach(() => {
  document.body.innerHTML = '<div id="sidebar-agents"></div>';
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
    if (url.endsWith('/api/sidebar/archive') && method === 'GET') {
      return Promise.resolve({ json: () => Promise.resolve(archive) });
    }
    return Promise.reject(new Error(`unexpected ${method} ${url}`));
  });
});

afterEach(() => vi.unstubAllGlobals());

const flush = () => new Promise(r => setTimeout(r, 0));
const GROUPS = [{ id: 'g1', name: 'Infra' }];
/** A Claude process start `m` minutes ago (the row's `created`; recent, so no filter hides it). */
const started = (m: number) => new Date(Date.now() - m * 60000).toISOString();
const T0 = started(90), T1 = started(80), T2 = started(5), T3 = started(4);

async function load(custom: unknown) {
  server = { order: [], view: { groupBy: 'custom' }, custom };
  await refreshLayout();
}
function draw(rows: (AgentRow | null)[]) {
  bridge.agents.set(rows as never);
  renderSidebarAgents(rows as AgentRow[]);
}
const inInfra = () => {
  const s = bridge.sidebar.get();
  return s ? sidebarModel(s).groups.find(g => g.key === customKey('g1'))?.items.map(it => it.agent?.name) ?? [] : [];
};
const assigned = () => (bridge.sidebar.get()?.custom.assign ?? {}) as Record<string, string>;

test('REBOOT, before the archive answers: a long-absent session keeps its group, a resumed one is drawn in its group', async () => {
  // A has been archived for 40 days; B ran until the reboot. Both are in Infra.
  const old = Date.now() - 40 * DAY;
  await load({ groups: GROUPS, assign: { 's:A': 'g1', 's:B': 'g1' }, seen: { 's:A': old, 's:B': Date.now() } });
  // the reboot: B comes back under a window id it never had
  draw([win('b', { window_id: '@1', session_id: 'B' })]);
  await flush();
  expect(inInfra()).toEqual(['b']);
  // the archive has not answered: A is UNKNOWN, not gone, so it is neither pruned nor written
  expect(assigned()).toEqual({ 's:A': 'g1', 's:B': 'g1' });
  expect(posts).toEqual([]);
  // once the archive answers holding A, A is known: kept, its stamp refreshed (one write)
  archive = { hidden: [], sessions: [{ key: 's:A', session_id: 'A' }] };
  await refreshArchive();
  draw([win('b', { window_id: '@1', session_id: 'B' })]);
  await flush();
  expect(assigned()).toEqual({ 's:A': 'g1', 's:B': 'g1' });
  expect(posts.length).toBe(1);
  // and A, unarchived into a new window, is drawn in Infra on its first draw
  draw([win('b', { window_id: '@1', session_id: 'B' }), win('a', { window_id: '@2', session_id: 'A' })]);
  expect(inInfra()).toEqual(['a', 'b']);
});

test('once the archive HAS answered, a session neither live nor archived for 30+ days is pruned', async () => {
  // (the archive answered in the test above, without B: that is what makes the prune legal)
  archive = { hidden: [], sessions: [] };
  await refreshArchive();
  await load({ groups: GROUPS, assign: { 's:B': 'g1' }, seen: { 's:B': Date.now() - 40 * DAY } });
  draw([win('c', { window_id: '@3', session_id: 'C' })]);
  await flush();
  expect(assigned()).toEqual({});
  expect(posts.map(p => (p.custom as { assign: unknown }).assign)).toEqual([{}]);
});

test('a legacy window key migrates to the session key on the first draw, and a null hole in the rows is skipped', async () => {
  await load({ groups: GROUPS, assign: { 'w:@7': 'g1' } });
  draw([null, win('m', { window_id: '@7', session_id: 'S' })]);
  await flush();
  expect(Object.keys(assigned())).toEqual(['s:S']);
  expect(inInfra()).toEqual(['m']);
});

test('a /clear in place carries the group to the new session, even past a null hole in the rows', async () => {
  await load({ groups: GROUPS, assign: { 's:A': 'g1' }, seen: { 's:A': Date.now() } });
  // /clear starts a new session in the SAME Claude process: the window's `created` holds
  draw([null, win('m', { window_id: '@5', session_id: 'A', created: T0 })]);
  draw([null, win('m', { window_id: '@5', session_id: 'B', created: T0 })]);
  await flush();
  expect(assigned()).toEqual({ 's:B': 'g1' });
  expect(inInfra()).toEqual(['m']);
});

test('a /clear still carries the group when either render could not read the process start (null)', async () => {
  for (const [before, after] of [[null, T0], [T0, null], [null, null]] as const) {
    await load({ groups: GROUPS, assign: { 's:A': 'g1' }, seen: { 's:A': Date.now() } });
    draw([win('m', { window_id: '@6', session_id: 'A', created: before })]);
    draw([win('m', { window_id: '@6', session_id: 'B', created: after })]);
    await flush();
    expect([before, after, assigned()]).toEqual([before, after, { 's:B': 'g1' }]);
  }
});

test('REBOOT: a session resumed into a RECYCLED window id never takes the group of the one that had it', async () => {
  await load({ groups: GROUPS, assign: { 's:A': 'g1' }, seen: { 's:A': Date.now() } });
  draw([win('a', { window_id: '@1', session_id: 'A', created: T0 }),
    win('b', { window_id: '@2', session_id: 'B', created: T1 })]);
  draw([]); // tmux is down: "No agents"
  // tmux restarted: B is resumed FIRST, into @1 (a new Claude process); A is not back yet
  draw([win('b', { window_id: '@1', session_id: 'B', created: T2 })]);
  await flush();
  expect(assigned()).toEqual({ 's:A': 'g1' });
  expect(inInfra()).toEqual([]);
  expect(posts).toEqual([]);
  // and A, resumed into @2, is back in Infra
  draw([win('b', { window_id: '@1', session_id: 'B', created: T2 }),
    win('a', { window_id: '@2', session_id: 'A', created: T3 })]);
  expect(inInfra()).toEqual(['a']);
});

test('a malformed `assign` or `seen` from the server reads as empty (never spread into keys)', async () => {
  for (const bad of ['xy', ['g1'], 5, null]) {
    posts = [];
    await load({ groups: GROUPS, assign: bad, seen: bad });
    draw([win('m', { window_id: '@8', session_id: 'S' })]);
    await flush();
    // nothing to reconcile: an empty assign and seen is already what the server would hold
    expect([bad, posts]).toEqual([bad, []]);
    expect(assigned()).toEqual({});
    moveToGroup('s:S', 'g1');
    await flush();
    const c = posts.at(-1)?.custom as { assign: unknown; seen: Record<string, unknown> };
    expect([bad, c.assign, Object.keys(c.seen)]).toEqual([bad, { 's:S': 'g1' }, ['s:S']]);
  }
});
