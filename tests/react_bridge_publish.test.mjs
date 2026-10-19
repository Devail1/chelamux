// CMX-178 — the legacy side of the React state bridge: util.js and nav.js publish what they
// still own (the agents list, the sidebar layout) to window.chelaBridge, the stores the
// islands read (chela/dashboard/frontend/src/shared/bridge). The real modules, in jsdom.
//
// Run: pnpm exec vitest run tests/react_bridge_publish.test.mjs  (pytest runs it via tests/test_js_suites.py)
import { beforeAll, beforeEach, test } from 'vitest';
import assert from 'node:assert/strict';
import { bootDashboardDom, flush } from './js_helpers/dashboard_dom.mjs';
import { fakeArchiveServer } from './js_helpers/fake_archive.mjs';

const ROWS = [{ name: 'a', window_id: '@301', online: true, session_status: 'idle', cwd: '/srv/code/alpha' }];
const server = fakeArchiveServer(() => ROWS);
function fetchImpl(url, opts) {
    const faked = server.handle(url, opts);
    const body = faked !== undefined ? faked : (/\/api\/(agents|summary)/.test(String(url)) ? [] : {});
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
}

let util, nav;
const sets = { agents: [], sidebarLayout: [] };
const recorder = name => ({ set: v => sets[name].push(v) });

beforeAll(async () => {
    ({ modules: { util, nav } } = await bootDashboardDom({
        body: '<aside class="sidebar"><div class="side-list" id="sidebar-agents"></div></aside><div id="agent-detail"></div>',
        canvasStub: true, terminalsEnabled: false, fetchImpl, extraModules: ['util.js', 'nav.js'],
    }));
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
    assert.equal(sets.sidebarLayout.length, 0);
});

test('a non-array agents value (a failed fetch\'s body) publishes an empty list, never the body', () => {
    const body = { error: 'boom' };
    util.setAgentsCache(body);
    assert.equal(util._agentsCache, body);
    util.setAgentsCache(null);
    assert.deepEqual(sets.agents, [[], []]);
});

test('with no bridge (the build did not load) publishing is a no-op and the cache still updates', () => {
    delete window.chelaBridge;
    const rows = [{ window_id: '@10' }];
    util.setAgentsCache(rows);
    assert.equal(util._agentsCache, rows);
    window.chelaBridge = { agents: null };
    util.setAgentsCache([]);
    assert.deepEqual(sets.agents, []);
});

test('a layout read from the server is published whole, loaded, without the private gen counter', async () => {
    await nav.refreshSidebar();
    for (let i = 0; i < 8; i++) await flush();
    const last = sets.sidebarLayout.at(-1);
    assert.deepEqual(Object.keys(last).sort(), ['custom', 'loaded', 'order', 'view']);
    assert.equal(last.loaded, true);
});

test('a local layout change is published at once, before the server answers', () => {
    nav.viewMenuAction('group:none');
    assert.equal(sets.sidebarLayout.length, 1);
    assert.equal(sets.sidebarLayout[0].view.groupBy, 'none');
    assert.equal('gen' in sets.sidebarLayout[0], false);
});
