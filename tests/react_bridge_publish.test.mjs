// CMX-178: the sidebar controller (CMX-278) publishes the sidebar layout to the bridge store the
// sidebar draws (chela/dashboard/frontend/src/shared/bridge), through window.chelaBridge. The
// agents and view stores are plain imports since CMX-217, and util.js's facade over them went
// with util.js (CMX-248). The real modules, in jsdom.
//
// Run: pnpm exec vitest run tests/react_bridge_publish.test.mjs  (pytest runs it via tests/test_js_suites.py)
import { beforeAll, beforeEach, test } from 'vitest';
import assert from 'node:assert/strict';
import { bootDashboardDom, flush, SIDEBAR, AGENTS_STORE } from './js_helpers/dashboard_dom.mjs';
import { fakeArchiveServer } from './js_helpers/fake_archive.mjs';

const ROWS = [{ name: 'a', window_id: '@301', online: true, session_status: 'idle', cwd: '/srv/code/alpha' }];
const server = fakeArchiveServer(() => ROWS);
function fetchImpl(url, opts) {
    const faked = server.handle(url, opts);
    const body = faked !== undefined ? faked : (/\/api\/(agents|summary)/.test(String(url)) ? [] : {});
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
}

let agentsStore, agents, sidebar;
const sets = { agents: [], sidebarLayout: [] };
const recorder = name => ({ set: v => sets[name].push(v) });

beforeAll(async () => {
    ({ modules: { agents: { agents: agentsStore }, sidebar } } = await bootDashboardDom({
        body: '<aside class="sidebar"><div class="side-list" id="sidebar-agents"></div></aside><div id="agent-detail"></div>',
        canvasStub: true, terminalsEnabled: false, fetchImpl, extraModules: [AGENTS_STORE, SIDEBAR],
    }));
    ({ agents } = await import('../chela/dashboard/frontend/src/shared/store/agents.ts'));
    agents.subscribe(() => sets.agents.push(agents.get()));
    for (let i = 0; i < 8; i++) await flush();
});

beforeEach(() => {
    window.chelaBridge = { agents: recorder('agents'), sidebarLayout: recorder('sidebarLayout') };
    sets.agents.length = sets.sidebarLayout.length = 0;
});

test('with no bridge on window, the layout publish is a no-op and the agents store still updates', () => {
    delete window.chelaBridge;
    const rows = [{ window_id: '@10' }];
    agentsStore.set(rows);
    assert.equal(agentsStore.get(), rows);
    assert.equal(agents.get(), rows);
    window.chelaBridge = { sidebarLayout: null };
    sidebar.viewMenuAction('group:none');
    assert.deepEqual(sets.sidebarLayout, []);
});

test('a layout read from the server is published whole, loaded, without the private gen counter', async () => {
    // The page already holds the server's layout (boot read it), so another device's change
    // is what a refresh publishes. CMX-278: an unchanged layout is not re-published.
    await sidebar.refreshSidebar();
    for (let i = 0; i < 8; i++) await flush();
    assert.deepEqual(sets.sidebarLayout, [], 'an unchanged layout was re-published');
    await fetchImpl('/api/sidebar/layout', { method: 'POST', body: JSON.stringify({ order: ['/srv/elsewhere'] }) });
    await sidebar.refreshSidebar();
    for (let i = 0; i < 8; i++) await flush();
    const last = sets.sidebarLayout.at(-1);
    assert.deepEqual(last.order, ['/srv/elsewhere']);
    assert.deepEqual(Object.keys(last).sort(), ['custom', 'loaded', 'order', 'view', 'width']);
    assert.equal(last.loaded, true);
});

test('a local layout change is published at once, before the server answers', () => {
    sidebar.viewMenuAction('group:none');
    assert.equal(sets.sidebarLayout.length, 1);
    assert.equal(sets.sidebarLayout[0].view.groupBy, 'none');
    assert.equal('gen' in sets.sidebarLayout[0], false);
});
