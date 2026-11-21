// CMX-390 (Liav, 11-17): a session another session launched is drawn ONLY under its parent
// (CMX-389's `parent_session_id`), the parent's archive takes its subtree along, and a pinned
// session sits in Pinned at the top and is never archived.
//
// The model half is pure; the list half renders the island; the persisted half drives the real
// controller against a stubbed /api (as manualSort.test.tsx does). The server half, the cascade
// itself and the pin refusal, is tests/test_sidebar_tree.py.
import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { installBridge } from '../../../shared/bridge/index.ts';
import {
  archiveKeys, dropRow, refreshArchive, refreshLayout, renderSidebarAgents, rowMenuAction, toggleGroup, unarchiveKeys,
} from '../controller.ts';
import { Sidebar } from '../index.ts';
import {
  type AgentRow, type GroupOptions, INDENT_LEVELS, type Item, MAX_TREE_DEPTH, PINNED_NO_ARCHIVE, type SidebarModel, type SessionRecord, buildItems,
  customKey, descendants, groupSidebar, isArchivable, itemParentSid, itemSid, kidsKey, nestItems, pinKey, sessionItem,
  sidebarModel, sidebarWids, treeKey, visibleRows,
} from '../model.ts';
import { NOW, actions, state, wants, win } from './fixtures.ts';

const bridge = installBridge(window);
const HOUR = 3600e3;

/** The orchestrator P in /srv/x launched C1 (in /srv/y) and C2; C1 launched G. `epoch`
 * renumbers the windows, as a reboot's `--resume` does: the sessions stay. */
function family(epoch = 0, over: Record<string, Partial<AgentRow>> = {}): AgentRow[] {
  const at = (n: number) => new Date(NOW - n * HOUR).toISOString();
  const row = (name: string, i: number, parent: string | null, cwd: string, act: number) => ({
    ...win(name, { cwd, session_id: `S-${name}`, parent_session_id: parent, created: at(10), last_activity: at(act) }),
    window_id: `@${epoch * 100 + i + 1}`, ...over[name],
  });
  return [row('P', 0, null, '/srv/x', 1), row('C1', 1, 'S-P', '/srv/y', 3), row('C2', 2, 'S-P', '/srv/x', 2),
    row('G', 3, 'S-C1', '/srv/y', 4)];
}
const name = (it: Item) => it.agent?.name ?? it.record?.name ?? it.key;
const names = (list: Item[]) => list.map(name);
const model = (agents: AgentRow[], over: Partial<GroupOptions> = {}) =>
  groupSidebar(agents, { wants, sort: 'activity', now: NOW, ...over });
/** `{parent: [children]}` by name, for every parent in the model. */
const tree = (m: SidebarModel) => {
  const all = [...m.needsYou, ...m.pinned, ...m.groups.flatMap(g => g.items), ...m.archived];
  const every = all.flatMap(it => [it, ...descendants(it, m.children)]);
  return Object.fromEntries(Object.entries(m.children).map(([k, v]) => [name(every.find(x => x.key === k) as Item), names(v)]));
};

// --- the model ---------------------------------------------------------------------------

describe('nesting', () => {
  test('a parent with two children: ONE top-level row, and the children nowhere else', () => {
    const m = model(family());
    expect(m.groups.map(g => [g.key, names(g.items)])).toEqual([['/srv/x', ['P']]]);
    expect(tree(m)).toEqual({ P: ['C2', 'C1'], C1: ['G'] });
    // C1 and G live in /srv/y, yet there is no /srv/y group: a child is drawn under its parent only
    expect(sidebarWids(m)).toEqual(['@1', '@3', '@2', '@4']);
  });

  test('every grouping mode: the children ride with the parent, never in their own group', () => {
    const custom = { groups: [{ id: 'g1', name: 'Infra' }], assign: { 's:S-C1': 'g1', 's:S-P': 'g1' } };
    for (const mode of ['folder', 'date', 'state', 'custom', 'none']) {
      const m = model(family(), { mode, custom });
      expect([mode, m.groups.flatMap(g => names(g.items))]).toEqual([mode, ['P']]);
      expect([mode, tree(m)]).toEqual([mode, { P: ['C2', 'C1'], C1: ['G'] }]);
    }
    expect(model(family(), { mode: 'custom', custom }).groups.map(g => g.key)).toEqual([customKey('g1')]);
  });

  test('a child whose parent is not drawn is a top-level row of its own folder', () => {
    const m = model(family().filter(a => a.name !== 'P'));
    expect(m.groups.map(g => [g.key, names(g.items)]).sort()).toEqual([['/srv/x', ['C2']], ['/srv/y', ['C1']]]);
    expect(tree(m)).toEqual({ C1: ['G'] });
  });

  test('siblings follow the active sort, and under Manual their own order (`~kids:<parent>`)', () => {
    expect(tree(model(family(), { sort: 'activity' })).P).toEqual(['C2', 'C1']);
    expect(tree(model(family(), { sort: 'name' })).P).toEqual(['C1', 'C2']);
    const P = buildItems(family()).find(it => it.agent?.name === 'P') as Item;
    const rows = { [kidsKey(P)]: { keys: ['s:S-C1', 's:S-C2'], at: NOW } };
    expect(tree(model(family(), { sort: 'manual', rows })).P).toEqual(['C1', 'C2']);
    expect(tree(model(family(), { sort: 'activity', rows })).P).toEqual(['C2', 'C1']);
  });

  test('a child blocked on you is lifted to Needs you, a top-level row there', () => {
    const m = model(family(0, { C2: { needs_human: true } }));
    expect(names(m.needsYou)).toEqual(['C2']);
    expect(tree(m)).toEqual({ P: ['C1'], C1: ['G'] });
  });

  test('a parent blocked on you keeps its children under it in Needs you', () => {
    const m = model(family(0, { P: { needs_human: true } }));
    expect(names(m.needsYou)).toEqual(['P']);
    expect(m.groups).toEqual([]);
    expect(tree(m)).toEqual({ P: ['C2', 'C1'], C1: ['G'] });
  });

  test('the keys: a tree and its children list by SESSION, which a reboot keeps', () => {
    const [p0] = buildItems(family(0)) as [Item];
    const [p1] = buildItems(family(7)) as [Item];
    expect(p0.key).not.toBe(p1.key);
    expect([treeKey(p0), kidsKey(p0), pinKey(p0)]).toEqual(['~tree:s:S-P', '~kids:s:S-P', 's:S-P']);
    expect([treeKey(p1), kidsKey(p1), pinKey(p1)]).toEqual([treeKey(p0), kidsKey(p0), pinKey(p0)]);
  });
});

describe('nestItems', () => {
  const it3 = (spec: [string, string | null][]) =>
    buildItems(spec.map(([n, p]) => win(n, { session_id: n, parent_session_id: p })));

  test('a loop in the data is cut: every row in it, and every row whose chain runs into it, is a top-level row', () => {
    const { roots, children } = nestItems(it3([['a', 'b'], ['b', 'a'], ['c', 'a'], ['d', null], ['e', 'd']]));
    expect(names(roots)).toEqual(['a', 'b', 'c', 'd']);
    expect([...children.values()].map(names)).toEqual([['e']]);
  });

  test('a row that names itself is a top-level row', () => {
    expect(names(nestItems(it3([['a', 'a']])).roots)).toEqual(['a']);
  });

  test(`a chain deeper than ${MAX_TREE_DEPTH} is cut there`, () => {
    const spec: [string, string | null][] = [['r0', null]];
    for (let i = 1; i <= MAX_TREE_DEPTH + 1; i++) spec.push([`r${i}`, `r${i - 1}`]);
    const { roots } = nestItems(it3(spec));
    expect(names(roots)).toEqual(['r0', `r${MAX_TREE_DEPTH + 1}`]);
  });

  test('forceRoot keeps a row at the top and hands it its ancestors, nearest first', () => {
    const seen: string[][] = [];
    const { roots } = nestItems(it3([['a', null], ['b', 'a'], ['c', 'b']]), (it, up) => {
      seen.push([name(it), ...names(up)]);
      return name(it) === 'b';
    });
    expect(names(roots)).toEqual(['a', 'b']);
    expect(seen).toEqual([['b', 'a'], ['c', 'b', 'a']]);
  });

  test('an archived record nests by its own parent field; a run has no session', () => {
    const rec: SessionRecord = { key: 's:K', session_id: 'K', parent_session_id: 'P' };
    expect([itemSid(sessionItem(rec)), itemParentSid(sessionItem(rec))]).toEqual(['K', 'P']);
    const run = buildItems([win('r', { run: { task_id: 'CMX-1', role: 'agent' }, session_id: 'X', parent_session_id: 'Y' })])[0] as Item;
    expect([itemSid(run), itemParentSid(run), pinKey(run)]).toEqual([null, null, null]);
  });
});

describe('pins', () => {
  test('a pinned parent: in Pinned with its children, and in no group', () => {
    const m = model(family(), { pins: ['s:S-P'] });
    expect(names(m.pinned)).toEqual(['P']);
    expect(m.groups).toEqual([]);
    expect(tree(m)).toEqual({ P: ['C2', 'C1'], C1: ['G'] });
  });

  test('a pinned child of an unpinned parent sits in Pinned, its own children with it', () => {
    const m = model(family(), { pins: ['s:S-C1'] });
    expect(names(m.pinned)).toEqual(['C1']);
    expect(tree(m)).toEqual({ P: ['C2'], C1: ['G'] });
  });

  test('a pinned child of a pinned parent stays under it, never twice', () => {
    const m = model(family(), { pins: ['s:S-P', 's:S-C1'] });
    expect(names(m.pinned)).toEqual(['P']);
    expect(sidebarWids(m)).toEqual(['@1', '@3', '@2', '@4']);
  });

  test('a pinned row is never filtered out, and the Archived status shows no Pinned', () => {
    const old = family(0, { P: { last_activity: new Date(NOW - 90 * 24 * HOUR).toISOString(), created: undefined } });
    expect(names(model(old, { pins: ['s:S-P'], activityDays: 7, env: ['dispatched'] }).pinned)).toEqual(['P']);
    expect(model(old, { pins: ['s:S-P'], status: 'archived' }).pinned).toEqual([]);
  });

  test('isArchivable: a pinned row never is; the same row unpinned is', () => {
    const [P] = buildItems(family()) as [Item];
    expect(isArchivable(P, { wants, pins: ['s:S-P'] })).toBe(false);
    expect(isArchivable(P, { wants, pins: ['s:S-other'] })).toBe(true);
    expect(isArchivable(P, { wants })).toBe(true);
  });

  test('a pinned child of an ARCHIVED parent stays visible under Pinned', () => {
    // the server archived P and C2 (closed: records now), and left the pinned C1 and G running
    const sessions: SessionRecord[] = [
      { key: 's:S-P', name: 'P', session_id: 'S-P', parent_session_id: null },
      { key: 's:S-C2', name: 'C2', session_id: 'S-C2', parent_session_id: 'S-P' },
    ];
    const live = family().filter(a => a.name === 'C1' || a.name === 'G');
    const m = model(live, { pins: ['s:S-C1'], sessions, status: 'all' });
    expect(names(m.pinned)).toEqual(['C1']);
    expect(tree(m)).toEqual({ P: ['C2'], C1: ['G'] });
    expect(names(m.archived)).toEqual(['P']);
  });
});

describe('the Archived section keeps a subtree nested', () => {
  test('records nest by their parent; the order stays newest archived first', () => {
    const sessions: SessionRecord[] = [
      { key: 's:S-C2', name: 'C2', session_id: 'S-C2', parent_session_id: 'S-P', archived_at: 3 },
      { key: 's:S-P', name: 'P', session_id: 'S-P', parent_session_id: null, archived_at: 2 },
      { key: 's:S-C1', name: 'C1', session_id: 'S-C1', parent_session_id: 'S-P', archived_at: 1 },
      { key: 's:S-x', name: 'x', session_id: 'S-x', parent_session_id: 'S-gone', archived_at: 0 },
    ];
    const m = model([win('other')], { sessions, status: 'all' });
    expect(names(m.archived)).toEqual(['P', 'x']);
    expect(tree(m)).toEqual({ P: ['C2', 'C1'] });
  });
});

// --- the list ----------------------------------------------------------------------------

describe('the list', () => {
  afterEach(cleanup);
  const draw = (agents: AgentRow[], over = {}) => {
    const acts = actions();
    const s = state(agents, { view: { ...state([]).view, sort: 'activity' }, ...over });
    const { container } = render(<Sidebar state={s} actions={acts} />);
    const row = (n: string) => container.querySelectorAll(`.agent-row[data-agent="${n}"]`);
    return { el: container, acts, row, s };
  };

  test('1 parent row and its 2 children indented under it, each drawn once', () => {
    const { el, row } = draw(family().filter(a => a.name !== 'G'));
    for (const n of ['P', 'C1', 'C2']) expect([n, row(n).length]).toEqual([n, 1]);
    const top = el.querySelector('.side-group[data-g="/srv/x"] .group-rows') as HTMLElement;
    expect([...top.children].map(c => c.className.split(' ')[0])).toEqual(['agent-row', 'tree-kids']);
    const kids = top.querySelector(':scope > .tree-kids') as HTMLElement;
    expect([...kids.children].map(c => (c as HTMLElement).dataset.agent)).toEqual(['C2', 'C1']);
    expect([...kids.children].every(c => c.classList.contains('nested'))).toBe(true);
    expect(row('P')[0]?.classList.contains('nested')).toBe(false);
    expect(row('C1')[0]?.querySelector('.term-status-dot')).not.toBeNull();
    expect(row('C1')[0]?.querySelector('.row-more')).not.toBeNull();
    const toggle = row('P')[0]?.querySelector('.tree-toggle') as HTMLElement;
    expect([toggle.tagName, toggle.getAttribute('aria-expanded'), toggle.textContent]).toEqual(['BUTTON', 'true', '2']);
    expect(row('C1')[0]?.querySelector('.tree-toggle')).toBeNull();
    expect(el.querySelector('.side-group[data-g="/srv/y"]')).toBeNull();
  });

  test('the chevron counts the whole subtree, and a click folds by the SESSION key without opening the row', () => {
    const { acts, row } = draw(family());
    const toggle = row('P')[0]?.querySelector('.tree-toggle') as HTMLElement;
    expect(toggle.textContent).toBe('3');
    expect(toggle.getAttribute('aria-label')).toBe('Hide 3 child sessions');
    fireEvent.click(toggle);
    expect(acts.toggleGroup.mock.calls).toEqual([['~tree:s:S-P']]);
    expect(acts.selectAgent).not.toHaveBeenCalled();
  });

  test('folded: the children are not drawn, and the chevron says so', () => {
    const { row } = draw(family(), { collapsed: ['~tree:s:S-P'] });
    expect([row('C1').length, row('C2').length, row('G').length]).toEqual([0, 0, 0]);
    const toggle = row('P')[0]?.querySelector('.tree-toggle') as HTMLElement;
    expect([toggle.getAttribute('aria-expanded'), toggle.getAttribute('aria-label')]).toEqual(['false', 'Show 3 child sessions']);
  });

  test('a grandchild nests one level further, and the indent stops growing at the cap', () => {
    const { el } = draw(family());
    const depths = [...el.querySelectorAll<HTMLElement>('.tree-kids')].map(k => k.dataset.depth);
    expect(depths).toEqual(['1', '2']);
    expect(INDENT_LEVELS).toBe(3);
    const deep: [string, string | null][] = [['a', null], ['b', 'a'], ['c', 'b'], ['d', 'c'], ['e', 'd']];
    const { el: el2 } = draw(deep.map(([n, p]) => win(n, { session_id: n, parent_session_id: p })));
    expect([...el2.querySelectorAll<HTMLElement>('.tree-kids')].map(k => k.dataset.depth)).toEqual(['1', '2', '3', '3']);
  });

  test('the folder line of a child names its folder only where it differs from its parent\'s', () => {
    const { row } = draw(family().filter(a => a.name !== 'G'));
    expect(row('C1')[0]?.querySelector('.ar-folder-line')?.textContent).toBe('y');
    expect(row('C2')[0]?.querySelector('.ar-folder-line')).toBeNull();
  });

  test('Pinned is the first section; its Archive is disabled with "Pinned: unpin to archive"', () => {
    const { el, acts, row } = draw(family(), { pins: ['s:S-P'] });
    const first = el.firstElementChild as HTMLElement;
    expect([first.className, first.dataset.g]).toEqual(['side-triage side-pinned', '~pinned']);
    expect(first.querySelector('.triage-head')?.textContent).toBe(' Pinned 4');
    expect([...first.querySelectorAll<HTMLElement>('.agent-row')].map(r => r.dataset.agent)).toEqual(['P', 'C2', 'C1', 'G']);
    fireEvent.keyDown(row('P')[0]?.querySelector('.row-more') as Element, { key: 'Enter' });
    const entries = [...document.querySelectorAll<HTMLElement>('[role="menu"] [role^="menuitem"]')];
    expect(entries.map(e => e.textContent)).toEqual(['Rename title…', 'Unpin', `Archive${PINNED_NO_ARCHIVE}`]);
    expect(entries[2]?.hasAttribute('data-disabled')).toBe(true);
    fireEvent.keyDown(entries[1] as HTMLElement, { key: 'Enter' });
    expect(acts.rowMenuAction.mock.calls).toEqual([[(row('P')[0] as HTMLElement).dataset.item, 'unpin']]);
  });

  test('an unpinned session offers Pin; a row with no session offers none', () => {
    const { acts, row } = draw([...family().slice(0, 1), win('nosid')]);
    fireEvent.keyDown(row('P')[0]?.querySelector('.row-more') as Element, { key: 'Enter' });
    const menu = () => [...document.querySelectorAll<HTMLElement>('[role="menu"] [role^="menuitem"]')];
    // P is its folder's one session (CMX-379): its menu leads with the folder's actions
    expect(menu().map(e => e.textContent).slice(-2)).toEqual(['Pin', 'Archive']);
    fireEvent.keyDown(menu().find(e => e.textContent === 'Pin') as HTMLElement, { key: 'Enter' });
    expect(acts.rowMenuAction.mock.calls).toEqual([[(row('P')[0] as HTMLElement).dataset.item, 'pin']]);
    fireEvent.keyDown(row('nosid')[0]?.querySelector('.row-more') as Element, { key: 'Enter' });
    expect(menu().map(e => e.textContent)).not.toContain('Pin');
    expect(menu().map(e => e.textContent).at(-1)).toBe('Archive');
  });

  test('the Archived section draws an archived subtree nested', () => {
    const sessions: SessionRecord[] = [
      { key: 's:S-P', name: 'P', session_id: 'S-P', parent_session_id: null, archived_at: 2 },
      { key: 's:S-C1', name: 'C1', session_id: 'S-C1', parent_session_id: 'S-P', archived_at: 1 },
    ];
    const { el } = draw([win('other')], { sessions, collapsed: ['~archived:open'] });
    expect(el.querySelector('.side-archived .archived-count')?.textContent).toBe('2');
    const box = el.querySelector('.side-archived .group-rows') as HTMLElement;
    expect([...box.children].map(c => c.className)).toEqual(['agent-row rich archived session-row tree-parent', 'tree-kids']);
    expect(box.querySelector('.tree-kids .session-row.nested')?.getAttribute('data-item')).toBe('s:S-C1');
  });

  test('visibleRows: a folded parent hides its subtree', () => {
    const m = model(family());
    const roots = m.groups.flatMap(g => g.items);
    expect(names(visibleRows(roots, m.children, []))).toEqual(['P', 'C2', 'C1', 'G']);
    expect(names(visibleRows(roots, m.children, ['~tree:s:S-C1']))).toEqual(['P', 'C2', 'C1']);
    expect(names(visibleRows(roots, m.children, ['~tree:s:S-P']))).toEqual(['P']);
  });

  test('Alt numbers the rows a user can see: a folded parent\'s children are skipped', () => {
    const { el } = draw(family(), { altHeld: true, collapsed: ['~tree:s:S-C1'] });
    const nums = [...el.querySelectorAll<HTMLElement>('.agent-row')].map(r => [r.dataset.agent, r.querySelector('.ar-altnum')?.textContent]);
    expect(nums).toEqual([['P', '1'], ['C2', '2'], ['C1', '3']]);
  });
});

// --- persisted: the real controller against a stubbed /api ------------------------------

describe('the controller', () => {
  let server: Record<string, unknown> = {};
  let layoutPosts: Record<string, unknown>[] = [];
  let archivePosts: { url: string; body: Record<string, unknown> }[] = [];
  let archive: { hidden: string[]; sessions: SessionRecord[] } = { hidden: [], sessions: [] };
  /** What the archive POST answers next (the server's cascade). */
  let archiveAnswer: Record<string, unknown> = {};

  beforeEach(() => {
    document.body.innerHTML = '<div id="sidebar-agents"></div>';
    localStorage.clear();
    layoutPosts = [];
    archivePosts = [];
    archive = { hidden: [], sessions: [] };
    vi.stubGlobal('alert', vi.fn());
    vi.stubGlobal('fetch', (url: string, opts?: RequestInit) => {
      const method = opts?.method ?? 'GET';
      const json = (v: unknown) => Promise.resolve({ json: () => Promise.resolve(v) });
      if (url.endsWith('/api/sidebar/archive') && method === 'GET') return json(archive);
      if ((url.endsWith('/api/sidebar/archive') || url.endsWith('/api/sidebar/unarchive')) && method === 'POST') {
        archivePosts.push({ url: url.replace(/^.*\/api/, '/api'), body: JSON.parse(String(opts?.body)) });
        return json(archiveAnswer);
      }
      if (url.endsWith('/api/sidebar/layout') && method === 'GET') return json({ layout: server });
      if (url.endsWith('/api/sidebar/layout') && method === 'POST') {
        const body = JSON.parse(String(opts?.body)) as Record<string, unknown>;
        layoutPosts.push(body);
        server = { ...server, ...body };
        return json({ ok: true, layout: server });
      }
      return Promise.reject(new Error(`unexpected ${method} ${url}`));
    });
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });
  const flush = () => new Promise(r => setTimeout(r, 0));

  /** A reload: fresh reads of the server's layout and archive, then a draw of `rows`. */
  async function load(layout: Record<string, unknown>, rows: AgentRow[]) {
    server = { order: [], ...layout };
    await Promise.all([refreshLayout(), refreshArchive()]);
    bridge.agents.set(rows as never);
    renderSidebarAgents(rows);
  }
  const m = () => sidebarModel(bridge.sidebar.get() as never);
  const keyOf = (n: string) => buildItems(bridge.agents.get() as never).find(it => it.agent?.name === n)?.key ?? '';

  test('collapsing hides the children, and survives a reload and a reboot (new window ids)', async () => {
    await load({ view: { activity: 'all' } }, family());
    toggleGroup('~tree:s:S-P');
    expect(m().groups.flatMap(g => visibleRows(g.items, m().children, bridge.sidebar.get()?.collapsed ?? [])).map(name)).toEqual(['P']);
    await load(server, family(5));   // a reload, and every window renumbered
    const s = bridge.sidebar.get();
    expect(s?.collapsed).toContain('~tree:s:S-P');
    expect(names(visibleRows(m().groups.flatMap(g => g.items), m().children, s?.collapsed ?? []))).toEqual(['P']);
    expect(tree(m())).toEqual({ P: ['C2', 'C1'], C1: ['G'] });
    expect(sidebarWids(m())).toEqual(['@501', '@503', '@502', '@504']);
  });

  test('pin: written to the server layout by session, it survives a reboot, and the archive is refused', async () => {
    await load({ view: { activity: 'all' } }, family());
    await rowMenuAction(keyOf('P'), 'pin');
    await flush();
    expect(layoutPosts).toEqual([{ pins: ['s:S-P'] }]);
    await load(server, family(3));
    expect(names(m().pinned)).toEqual(['P']);
    expect(tree(m())).toEqual({ P: ['C2', 'C1'], C1: ['G'] });
    expect(rowMenuAction(keyOf('P'), 'archive')).toBeNull();
    expect(archivePosts).toEqual([]);
    await rowMenuAction(keyOf('P'), 'unpin');
    await flush();
    expect(layoutPosts.at(-1)).toEqual({ pins: [] });
    expect(m().pinned).toEqual([]);
  });

  test('a row with no session cannot be pinned: nothing is written', async () => {
    await load({}, [win('nosid')]);
    expect(rowMenuAction(keyOf('nosid'), 'pin')).toBe(false);
    expect(layoutPosts).toEqual([]);
  });

  test('archive the parent: ONE request, and every window the server closed leaves the list', async () => {
    await load({ view: { activity: 'all' } }, family());
    const recs: SessionRecord[] = [
      { key: 's:S-P', name: 'P', session_id: 'S-P', parent_session_id: null },
      { key: 's:S-C1', name: 'C1', session_id: 'S-C1', parent_session_id: 'S-P' },
      { key: 's:S-C2', name: 'C2', session_id: 'S-C2', parent_session_id: 'S-P' },
      { key: 's:S-G', name: 'G', session_id: 'S-G', parent_session_id: 'S-C1' },
    ];
    archiveAnswer = { ok: true, archived: recs.map(r => r.key), closed: ['@1', '@2', '@3', '@4'], refused: [],
      state: { hidden: [], sessions: recs } };
    await rowMenuAction(keyOf('P'), 'archive');
    expect(archivePosts).toEqual([{ url: '/api/sidebar/archive', body: { keys: ['w:@1'] } }]);
    expect(m().groups).toEqual([]);
    expect(names(m().archived)).toEqual(['P']);
    expect(tree(m())).toEqual({ P: ['C1', 'C2'], C1: ['G'] });
    archiveAnswer = { ok: true, unhidden: [], resumed: recs.map(r => ({ key: r.key })), refused: [],
      state: { hidden: [], sessions: [] } };
    await unarchiveKeys(['s:S-P']);
    expect(archivePosts.at(-1)).toEqual({ url: '/api/sidebar/unarchive', body: { keys: ['s:S-P'] } });
    await load(server, family(2));
    expect(m().archived).toEqual([]);
    expect(tree(m())).toEqual({ P: ['C2', 'C1'], C1: ['G'] });
  });

  test('Archive all on a group skips a pinned row', async () => {
    await load({ view: { activity: 'all' }, pins: ['s:S-C2'] }, family().filter(a => a.name === 'C2').concat(win('free', { cwd: '/srv/x' })));
    archiveAnswer = { ok: true, archived: [], closed: [], refused: [], state: archive };
    await archiveKeys(m().groups.flatMap(g => g.items).filter(it => isArchivable(it, { wants, pins: ['s:S-C2'] })).map(it => it.key));
    expect(archivePosts).toEqual([{ url: '/api/sidebar/archive', body: { keys: [keyOf('free')] } }]);
  });

  test('Manual: a child reorders among its siblings, kept under its parent by session', async () => {
    await load({ view: { activity: 'all', sort: 'manual' } }, family());
    const list = '~kids:s:S-P';
    expect(dropRow(list, keyOf('C1'), keyOf('C2'))).toBe(true);
    await flush();
    expect((layoutPosts.at(-1)?.rows as Record<string, { keys: string[] }>)[list]?.keys).toEqual(['s:S-C1', 's:S-C2']);
    await load(server, family(4));
    expect(tree(m()).P).toEqual(['C1', 'C2']);
    expect(dropRow('~kids:s:S-nobody', keyOf('C1'), null)).toBe(false);
  });
});
