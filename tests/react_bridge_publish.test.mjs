// CMX-178 — the legacy side of the React state bridge: util.js and (CMX-278) the sidebar controller publish what they
// still own (the agents list, the sidebar layout) to the stores the islands read
// (chela/dashboard/frontend/src/shared/bridge). CMX-217: util.js's `_agentsCache` and
// `currentTab` are a facade over the store's `agents` and `view` slices, imported, not looked
// up on window.chelaBridge; the sidebar layout still goes through it. The real modules, in jsdom.
//
// Run: pnpm exec vitest run tests/react_bridge_publish.test.mjs  (pytest runs it via tests/test_js_suites.py)
import { beforeAll, beforeEach, test } from 'vitest';
import assert from 'node:assert/strict';
import { bootDashboardDom, flush, SIDEBAR } from './js_helpers/dashboard_dom.mjs';
import { fakeArchiveServer } from './js_helpers/fake_archive.mjs';

const ROWS = [{ name: 'a', window_id: '@301', online: true, session_status: 'idle', cwd: '/srv/code/alpha' }];
const server = fakeArchiveServer(() => ROWS);
function fetchImpl(url, opts) {
    const faked = server.handle(url, opts);
    const body = faked !== undefined ? faked : (/\/api\/(agents|summary)/.test(String(url)) ? [] : {});
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
}

let util, nav, agents, view, sidebar;
const sets = { agents: [], sidebarLayout: [] };
const recorder = name => ({ set: v => sets[name].push(v) });

beforeAll(async () => {
    ({ modules: { util, nav, sidebar } } = await bootDashboardDom({
        body: '<aside class="sidebar"><div class="side-list" id="sidebar-agents"></div></aside><div id="agent-detail"></div>',
        canvasStub: true, terminalsEnabled: false, fetchImpl, extraModules: ['util.js', 'nav.js', SIDEBAR],
    }));
    ({ agents } = await import('../chela/dashboard/frontend/src/shared/store/agents.ts'));
    ({ view } = await import('../chela/dashboard/frontend/src/shared/store/view.ts'));
    agents.subscribe(() => sets.agents.push(agents.get()));
    for (let i = 0; i < 8; i++) await flush();
});

beforeEach(() => {
    window.chelaBridge = { agents: recorder('agents'), sidebarLayout: recorder('sidebarLayout') };
    sets.agents.length = sets.sidebarLayout.length = 0;
});

test('setAgentsCache publishes the very list it caches to the agents store', () => {
    const rows = [{ window_id: '@9' }];
    util.setAgentsCache(rows);
    assert.equal(sets.agents.length, 1);
    assert.equal(sets.agents[0], rows);
    assert.equal(util._agentsCache, rows);
    assert.equal(sets.sidebarLayout.length, 0);
});

test('a non-array agents value (a failed fetch\'s body) caches an empty list, never the body', () => {
    util.setAgentsCache([{ window_id: '@8' }]);
    util.setAgentsCache({ error: 'boom' });
    assert.deepEqual(util._agentsCache, []);
    util.setAgentsCache(null);
    assert.deepEqual(sets.agents, [[{ window_id: '@8' }], [], []]);
});

test('the facade follows the store: a write by a ported module reaches the legacy bindings', () => {
    const rows = [{ window_id: '@11' }];
    agents.set(rows);
    assert.equal(util._agentsCache, rows);
    view.set('agent-detail');
    assert.equal(util.currentTab, 'agent-detail');
    util.setCurrentTab('work');
    assert.equal(view.get(), 'work');
    assert.equal(util.currentTab, 'work');
});

test('with no bridge on window, the layout publish is a no-op and the agents cache still updates', () => {
    delete window.chelaBridge;
    const rows = [{ window_id: '@10' }];
    util.setAgentsCache(rows);
    assert.equal(util._agentsCache, rows);
    assert.equal(agents.get(), rows);
    window.chelaBridge = { sidebarLayout: null };
    sidebar.viewMenuAction('group:none');
    assert.deepEqual(sets.sidebarLayout, []);
});

test('a layout read from the server is published whole, loaded, without the private gen counter', async () => {
    await sidebar.refreshSidebar();
    for (let i = 0; i < 8; i++) await flush();
    const last = sets.sidebarLayout.at(-1);
    assert.deepEqual(Object.keys(last).sort(), ['custom', 'loaded', 'order', 'view', 'width']);
    assert.equal(last.loaded, true);
});

test('a local layout change is published at once, before the server answers', () => {
    sidebar.viewMenuAction('group:none');
    assert.equal(sets.sidebarLayout.length, 1);
    assert.equal(sets.sidebarLayout[0].view.groupBy, 'none');
    assert.equal('gen' in sets.sidebarLayout[0], false);
});
