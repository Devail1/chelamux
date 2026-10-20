// CMX-215 — the jsdom bootstrap boots what Flask serves: the Vite build's entry
// (frontend/src/main.tsx, the manifest's entry), which installs the islands' bridge and then
// boots the legacy graph (frontend/src/boot.ts). A suite's extraModules are the SAME module instances that
// entry loaded, so a test drives the page it booted, not a second copy.
//
// Run: pnpm exec vitest run tests/dashboard_entry_boot.test.mjs (pytest runs it via tests/test_js_suites.py)
import { beforeAll, test } from 'vitest';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { bootDashboardDom, ENTRY_URL, flush } from './js_helpers/dashboard_dom.mjs';

const ROWS = [{ name: 'a', window_id: '@501', online: true, session_status: 'idle', cwd: '/srv/code/alpha' }];
function fetchImpl(url) {
    const body = /\/api\/agents$/.test(String(url)) ? ROWS : (/\/api\/(agents|summary)/.test(String(url)) ? [] : {});
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
}

let util;
beforeAll(async () => {
    ({ modules: { util } } = await bootDashboardDom({
        body: '<aside class="sidebar"><div class="side-list" id="sidebar-agents"></div></aside><div id="agent-detail"></div>',
        canvasStub: true, terminalsEnabled: false, fetchImpl, extraModules: ['util.js'],
    }));
    for (let i = 0; i < 8; i++) await flush();
});

test('the boot entry is the manifest\'s, frontend/src/main.tsx', () => {
    assert.match(ENTRY_URL, /\/chela\/dashboard\/frontend\/src\/main\.tsx$/);
    assert.match(readFileSync(new URL(ENTRY_URL), 'utf8'), /import '\.\/boot\.ts';/);
});

test('the entry installed the real bridge, and legacy boot published the agents it fetched to it', () => {
    assert.equal(typeof window.chelaBridge?.agents?.subscribe, 'function', 'no islands bridge: the entry did not run');
    assert.deepEqual(window.chelaBridge.agents.get(), ROWS);
});

test('extraModules share the entry\'s instances: a cache write reaches the same bridge', () => {
    const rows = [{ window_id: '@502' }];
    util.setAgentsCache(rows);
    assert.equal(window.chelaBridge.agents.get(), rows);
    assert.equal(typeof window.chela.closeModal, 'function', 'the legacy graph never registered window.chela');
});
