// NEW SESSION → SANDBOXED (CMX-403) — the launcher entry for the only kind of window a
// share guest may type into, in BOTH new-session menus, over the REAL index.html shell
// and the REAL module graph (same idiom as tests/mobile_new_fab.test.mjs).
//
// Guards (each corrupt→RED, recorded in the PR's self-check experiments):
//   - the sidebar "New session" button (the drawer's, on a phone too: CMX-313 removed the "+") opens the launch menu
//     (the launcher island since CMX-219), which contains "Sandboxed session…";
//   - clicking it asks for a project directory and POSTs it to /api/agents/spawn-sandboxed,
//     the share_sandbox launcher's route, never the plain /api/agents/spawn a shell window
//     uses (negative control: Shell window does).
//
// Run: pnpm exec vitest run tests/new_menu_sandbox.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `pnpm install` for jsdom).
import { test, beforeAll, beforeEach } from 'vitest';
import assert from 'node:assert/strict';
import { APP_BODY, bootDashboardDom, clickIsland, flush } from './js_helpers/dashboard_dom.mjs';

let win, doc;
let calls = [];

beforeAll(async () => {
    const fetchImpl = (url, opts) => {
        calls.push({ url: String(url), opts: opts || {} });
        const b = String(url).endsWith('/api/launcher') ? { recent: [{ path: '/work/proj' }], favorites: [] } : { ok: true };
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(b) });
    };
    const { dom } = await bootDashboardDom({ body: APP_BODY, fetchImpl });
    win = dom.window;
    doc = win.document;
    win.prompt = () => '/work/proj';
    globalThis.prompt = win.prompt;
    win.alert = globalThis.alert = () => {};
});

const menu = () => doc.querySelector('.popover.launch-menu');
beforeEach(async () => {
    const { closeLaunchMenu } = await import('../chela/dashboard/frontend/src/features/launcher/index.ts');
    closeLaunchMenu();
    await flush();
    calls = [];
});

async function click(el) {
    clickIsland(el);
    await flush();
}

async function openedMenuItem(trigger) {
    assert.ok(trigger, 'the trigger is missing from the page');
    await click(trigger);
    assert.ok(menu(), `${trigger.id || trigger.className} did not open the launch menu`);
    return menu().querySelector('#new-sandbox-item');
}

test('the desktop "New session" menu offers Sandboxed session…', async () => {
    const item = await openedMenuItem(doc.querySelector('.sidebar-new-btn'));
    assert.ok(item, 'no Sandboxed session entry in the desktop New session menu');
    assert.match(item.textContent, /Sandboxed session/);
});

test('Sandboxed session… POSTs the project to the sandboxed launcher route', async () => {
    const item = await openedMenuItem(doc.querySelector('.sidebar-new-btn'));
    await click(item);
    await flush();
    const post = calls.find(c => c.opts.method === 'POST');
    assert.ok(post, 'no launch request');
    assert.match(post.url, /\/api\/agents\/spawn-sandboxed$/);
    assert.deepEqual(JSON.parse(post.opts.body), { cwd: '/work/proj' });
});

test('negative control: Shell window uses the plain spawn route, not the sandboxed one', async () => {
    await openedMenuItem(doc.querySelector('.sidebar-new-btn'));
    await click(doc.getElementById('new-shell-item'));
    await flush();
    const post = calls.find(c => c.opts.method === 'POST');
    assert.match(post.url, /\/api\/agents\/spawn$/);
});

// CMX-418: "Sandboxed session · allow web access…" — the same launcher route, with
// `web: true`, and ONLY after the operator confirms the web-access warning.
test('the New session menu offers Sandboxed session · allow web access…', async () => {
    await openedMenuItem(doc.querySelector('.sidebar-new-btn'));
    const item = menu().querySelector('#new-sandbox-web-item');
    assert.ok(item, 'no web-access sandboxed entry in the New session menu');
    assert.match(item.textContent, /web access/);
});

test('the web entry POSTs web:true after the confirm', async () => {
    let asked = '';
    win.confirm = globalThis.confirm = (msg) => { asked = msg; return true; };
    await openedMenuItem(doc.querySelector('.sidebar-new-btn'));
    await click(doc.getElementById('new-sandbox-web-item'));
    await flush();
    const post = calls.find(c => c.opts.method === 'POST');
    assert.ok(post, 'no launch request');
    assert.match(post.url, /\/api\/agents\/spawn-sandboxed$/);
    assert.deepEqual(JSON.parse(post.opts.body), { cwd: '/work/proj', web: true });
    assert.match(asked, /PUBLIC/);
});

test('declining the web-access confirm launches nothing', async () => {
    win.confirm = globalThis.confirm = () => false;
    await openedMenuItem(doc.querySelector('.sidebar-new-btn'));
    await click(doc.getElementById('new-sandbox-web-item'));
    await flush();
    assert.equal(calls.filter(c => c.opts.method === 'POST').length, 0);
});

test('negative control: the plain Sandboxed entry never sends web', async () => {
    win.confirm = globalThis.confirm = () => true;
    await openedMenuItem(doc.querySelector('.sidebar-new-btn'));
    await click(doc.getElementById('new-sandbox-item'));
    await flush();
    const post = calls.find(c => c.opts.method === 'POST');
    assert.deepEqual(JSON.parse(post.opts.body), { cwd: '/work/proj' });
});
