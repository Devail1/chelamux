// CMX-380: what a drop PERSISTS, through the real controller against a stubbed /api: the
// group order a drag writes is the one Move up / Move down write (the server layout's
// `order`), a session dropped on a custom group is the row menu's Move to group, and a
// failed write snaps the list back and says so.
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { installBridge } from '../../../shared/bridge/index.ts';
import {
  dropGroup, dropOnGroup, groupMenuAction, moveToGroup, refreshLayout, renderSidebarAgents,
} from '../controller.ts';
import { type AgentRow, UNGROUPED_KEY, customKey, sidebarModel } from '../model.ts';
import { win } from './fixtures.ts';

const bridge = installBridge(window);

let server: Record<string, unknown> = {};
let posts: Record<string, unknown>[] = [];
/** What the next layout POST answers: the saved layout, or a failure. */
let answer: 'ok' | 'refused' | 'down' = 'ok';
const alerts = vi.fn();

const ROWS: AgentRow[] = ['a', 'b', 'c'].flatMap(f => [
  win(`${f}1`, { cwd: `/srv/${f}`, session_id: `S-${f}1` }), win(`${f}2`, { cwd: `/srv/${f}`, session_id: `S-${f}2` }),
]);

beforeEach(async () => {
  document.body.innerHTML = '<div id="sidebar-agents"></div>';
  posts = [];
  answer = 'ok';
  alerts.mockReset();
  vi.stubGlobal('alert', alerts);
  vi.stubGlobal('fetch', (url: string, opts?: RequestInit) => {
    const method = opts?.method ?? 'GET';
    if (url.endsWith('/api/sidebar/layout') && method === 'GET') {
      return Promise.resolve({ json: () => Promise.resolve({ layout: server }) });
    }
    if (url.endsWith('/api/sidebar/layout') && method === 'POST') {
      const body = JSON.parse(String(opts?.body)) as Record<string, unknown>;
      posts.push(body);
      if (answer === 'down') return Promise.reject(new Error('offline'));
      if (answer === 'refused') return Promise.resolve({ json: () => Promise.resolve({ ok: false, error: 'disk full' }) });
      server = { ...server, ...body };
      return Promise.resolve({ json: () => Promise.resolve({ ok: true, layout: server }) });
    }
    return Promise.reject(new Error(`unexpected ${method} ${url}`));
  });
});

afterEach(() => vi.unstubAllGlobals());

const flush = () => new Promise(r => setTimeout(r, 0));

/** Load `layout` as the server's and draw the rows. */
async function load(layout: Record<string, unknown>) {
  server = { order: [], ...layout };
  await refreshLayout();
  bridge.agents.set(ROWS as never); // what a rerender reads the rows from
  renderSidebarAgents(ROWS);
}

const drawnOrder = () => {
  const s = bridge.sidebar.get();
  return s ? sidebarModel(s).groups.map(g => g.key) : [];
};
const lastOrder = () => posts.filter(p => 'order' in p).at(-1)?.order;

test('dragging group C above A persists the SAME order as two Move up\'s', async () => {
  await load({});
  expect(drawnOrder()).toEqual(['/srv/a', '/srv/b', '/srv/c']);
  groupMenuAction('/srv/c', 'up');
  await flush();
  groupMenuAction('/srv/c', 'up');
  await flush();
  const viaMenu = lastOrder();
  expect(viaMenu).toEqual(['/srv/c', '/srv/a', '/srv/b']);

  posts = [];
  await load({});
  expect(drawnOrder()).toEqual(['/srv/a', '/srv/b', '/srv/c']);
  expect(dropGroup('/srv/c', '/srv/a')).toBe(true);
  // optimistic: drawn before the server answers
  expect(drawnOrder()).toEqual(['/srv/c', '/srv/a', '/srv/b']);
  await flush();
  expect(posts).toEqual([{ order: viaMenu }]);
  expect(server.order).toEqual(viaMenu);
  // and a reload (a fresh read of the server's layout) draws it
  await load({ order: server.order });
  expect(drawnOrder()).toEqual(['/srv/c', '/srv/a', '/srv/b']);
});

test('a drop that changes nothing writes nothing', async () => {
  await load({});
  expect(dropGroup('/srv/a', '/srv/b')).toBe(false);
  expect(dropGroup('/srv/a', '/srv/a')).toBe(false);
  await flush();
  expect(posts).toEqual([]);
});

test('a refused order write snaps the list back and says why', async () => {
  await load({ order: ['/srv/a', '/srv/b', '/srv/c'] });
  answer = 'refused';
  expect(dropGroup('/srv/c', '/srv/a')).toBe(true);
  expect(drawnOrder()).toEqual(['/srv/c', '/srv/a', '/srv/b']);
  await flush();
  expect(drawnOrder()).toEqual(['/srv/a', '/srv/b', '/srv/c']);
  expect(bridge.sidebarLayout.get().order).toEqual(['/srv/a', '/srv/b', '/srv/c']);
  expect(alerts.mock.calls).toEqual([['Moving the group failed: disk full. It was put back.']]);
});

test('an unanswered order write snaps back too, and Move up shares the path', async () => {
  await load({ order: ['/srv/a', '/srv/b', '/srv/c'] });
  answer = 'down';
  groupMenuAction('/srv/b', 'up');
  expect(drawnOrder()).toEqual(['/srv/b', '/srv/a', '/srv/c']);
  await flush();
  expect(drawnOrder()).toEqual(['/srv/a', '/srv/b', '/srv/c']);
  expect(alerts.mock.calls).toEqual([['Moving the group failed: the dashboard did not answer. It was put back.']]);
});

test('a failed write never reverts over a NEWER one', async () => {
  await load({ order: ['/srv/a', '/srv/b', '/srv/c'] });
  answer = 'refused';
  dropGroup('/srv/c', '/srv/a');
  answer = 'ok';
  dropGroup('/srv/a', null);
  await flush();
  expect(drawnOrder()).toEqual(['/srv/c', '/srv/b', '/srv/a']);
  expect(alerts).toHaveBeenCalledTimes(1);
});

const CUSTOM = { view: { groupBy: 'custom' }, custom: { groups: [{ id: 'g1', name: 'Infra' }, { id: 'g2', name: 'Web' }], assign: {} } };
/** The last custom write, its groups and assignments (its `seen` stamps are the clock's). */
const lastCustom = () => {
  const c = posts.filter(p => 'custom' in p).at(-1)?.custom as { groups?: unknown; assign?: unknown } | undefined;
  return c && { groups: c.groups, assign: c.assign };
};

test('dropping a session on a custom group writes the same assignment as Move to group', async () => {
  await load(CUSTOM);
  moveToGroup('s:S-a1', 'g2');
  await flush();
  const viaMenu = lastCustom();
  expect(viaMenu).toEqual({ groups: CUSTOM.custom.groups, assign: { 's:S-a1': 'g2' } });

  posts = [];
  await load(CUSTOM);
  expect(dropOnGroup('s:S-a1', customKey('g2'))).toBe(true);
  await flush();
  expect(lastCustom()).toEqual(viaMenu);
  const s = bridge.sidebar.get();
  const g2 = s ? sidebarModel(s).groups.find(g => g.key === customKey('g2')) : undefined;
  expect(g2?.items.map(it => it.agent?.name)).toEqual(['a1']);
});

test('dropped on Ungrouped or on no group, a session is unassigned, as Move to group → Ungrouped', async () => {
  for (const target of [UNGROUPED_KEY, null]) {
    posts = [];
    await load({ ...CUSTOM, custom: { ...CUSTOM.custom, assign: { 's:S-a1': 'g1' } } });
    expect(dropOnGroup('s:S-a1', target)).toBe(true);
    await flush();
    expect(lastCustom()).toEqual({ groups: CUSTOM.custom.groups, assign: {} });
  }
});

test('a pinned section, a gone group or a folder is no drop target: nothing is written', async () => {
  await load(CUSTOM);
  for (const k of ['~needs-you', '~archived', '~dispatched', customKey('gone'), '/srv/a']) {
    expect([k, dropOnGroup('s:S-a1', k)]).toEqual([k, false]);
  }
  await flush();
  expect(posts).toEqual([]);
});

test('a refused assignment snaps the row back to its group and says why', async () => {
  await load(CUSTOM);
  answer = 'refused';
  dropOnGroup('s:S-a1', customKey('g2'));
  const inG2 = () => {
    const s = bridge.sidebar.get();
    return s ? sidebarModel(s).groups.find(g => g.key === customKey('g2'))?.items.length ?? 0 : 0;
  };
  expect(inG2()).toBe(1);
  await flush();
  expect(inG2()).toBe(0);
  expect(bridge.sidebarLayout.get().custom).toEqual(CUSTOM.custom);
  expect(alerts.mock.calls).toEqual([['Moving the session failed: disk full. It was put back.']]);
});
