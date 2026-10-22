// CMX-215: the jsdom bootstrap boots what Flask serves: the Vite build's entry
// (frontend/src/main.tsx, the manifest's entry), which mounts the page (CMX-248: one React root,
// the shell, in #root) and starts it (frontend/src/boot.ts). A suite's extraModules are the SAME
// module instances that entry loaded, so a test drives the page it booted, not a second copy.
//
// Run: pnpm exec vitest run tests/dashboard_entry_boot.test.mjs (pytest runs it via tests/test_js_suites.py)
import { beforeAll, test } from 'vitest';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { APP_BODY, bootDashboardDom, ENTRY_URL, flush, AGENTS_STORE, VIEW_STORE } from './js_helpers/dashboard_dom.mjs';

const ROWS = [{ name: 'a', window_id: '@501', online: true, session_status: 'idle', cwd: '/srv/code/alpha' }];
function fetchImpl(url) {
    const body = /\/api\/agents$/.test(String(url)) ? ROWS : (/\/api\/(agents|summary)/.test(String(url)) ? [] : {});
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
}

let agentsStore, viewStore;
beforeAll(async () => {
    ({ modules: { agents: { agents: agentsStore }, view: { view: viewStore } } } = await bootDashboardDom({
        body: APP_BODY,
        canvasStub: true, terminalsEnabled: false, fetchImpl, extraModules: [AGENTS_STORE, VIEW_STORE],
    }));
    for (let i = 0; i < 8; i++) await flush();
});

test('the boot entry is the manifest\'s, frontend/src/main.tsx', () => {
    assert.match(ENTRY_URL, /\/chela\/dashboard\/frontend\/src\/main\.tsx$/);
    assert.match(readFileSync(new URL(ENTRY_URL), 'utf8'), /import \{ mountPage, start \} from '\.\/boot\.ts';\s+mountPage\(\);\s+start\(\);/);
});

test('the entry mounted the shell in #root, installed the real bridge, and published the agents it fetched to it', () => {
    assert.ok(document.querySelector('#root > .app > aside.sidebar'), 'the shell is not in #root');
    assert.equal(typeof window.chelaBridge?.agents?.subscribe, 'function', 'no bridge: the entry did not run');
    assert.deepEqual(window.chelaBridge.agents.get(), ROWS);
    assert.ok(document.querySelector('#sidebar-agents .agent-row[data-agent="a"]'), 'the sidebar did not draw the fetched agents');
});

test('window.chela is the panes\' contract and nothing else (FRONTEND_PORT_PLAN.md §6.3)', () => {
    assert.deepEqual(Object.keys(window.chela).sort(), ['openPalette', 'toggleSettings']);
});

test('extraModules share the entry\'s instances: a cache write reaches the same bridge', () => {
    const rows = [{ window_id: '@502' }];
    agentsStore.set(rows);
    assert.equal(window.chelaBridge.agents.get(), rows);
});
