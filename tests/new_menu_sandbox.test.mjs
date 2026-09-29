// NEW SESSION → SANDBOXED (CMX-403) — the launcher entry for the only kind of window a
// share guest may type into, in BOTH new-session menus, over the REAL index.html shell
// and the REAL module graph (same idiom as tests/mobile_new_fab.test.mjs).
//
// Guards (each corrupt→RED, recorded in the PR's self-check experiments):
//   - the desktop sidebar "New session" button AND the phone "+" each open a menu that
//     contains "Sandboxed session…";
//   - clicking it (its REAL onclick attribute) asks for a project directory and POSTs
//     it to /api/agents/spawn-sandboxed — the share_sandbox launcher's route, never the
//     plain /api/agents/spawn a shell window uses (negative control: Shell window does).
//
// Run: node --test tests/new_menu_sandbox.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `pnpm install` for jsdom).
import { test, before, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { bootDashboardDom, sliceTemplate, flush } from './js_helpers/dashboard_dom.mjs';

let win, doc;
let calls = [];

before(async () => {
    const body = sliceTemplate('<div class="app">',
        'onclick="chela.hideNewMenu(); chela.newShellWindow()">Shell window</div>\n</div>');
    const fetchImpl = (url, opts) => {
        calls.push({ url: String(url), opts: opts || {} });
        const b = String(url).endsWith('/api/launcher') ? { recent: [{ path: '/work/proj' }], favorites: [] } : { ok: true };
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(b) });
    };
    const { dom } = await bootDashboardDom({ body, fetchImpl });
    win = dom.window;
    doc = win.document;
    win.prompt = () => '/work/proj';
    globalThis.prompt = win.prompt;
    win.alert = globalThis.alert = () => {};
});

beforeEach(() => { calls = []; win.chela.hideNewMenu(); });

function click(el) {
    const code = el.getAttribute('onclick');
    assert.ok(code, `${el.id || el.className} has no onclick attribute`);
    return new Function('chela', 'event', code).call(el, win.chela, { stopPropagation() {}, currentTarget: el, target: el });
}

function openedMenuItem(trigger) {
    click(trigger);
    const menu = doc.getElementById('new-menu');
    assert.notEqual(menu.style.display, 'none', `${trigger.id || trigger.className} did not open #new-menu`);
    return menu.querySelector('#new-sandbox-item');
}

test('the desktop "New session" menu offers Sandboxed session…', () => {
    const item = openedMenuItem(doc.querySelector('.sidebar-new-btn'));
    assert.ok(item, 'no Sandboxed session entry in the desktop New session menu');
    assert.match(item.textContent, /Sandboxed session/);
});

test('the phone "+" menu offers Sandboxed session…', () => {
    const item = openedMenuItem(doc.getElementById('btn-new-mobile'));
    assert.ok(item, 'no Sandboxed session entry in the phone "+" menu');
});

test('Sandboxed session… POSTs the project to the sandboxed launcher route', async () => {
    const item = openedMenuItem(doc.querySelector('.sidebar-new-btn'));
    await click(item);
    await flush();
    const post = calls.find(c => c.opts.method === 'POST');
    assert.ok(post, 'no launch request');
    assert.match(post.url, /\/api\/agents\/spawn-sandboxed$/);
    assert.deepEqual(JSON.parse(post.opts.body), { cwd: '/work/proj' });
});

test('negative control: Shell window uses the plain spawn route, not the sandboxed one', async () => {
    await click(doc.getElementById('new-shell-item'));
    await flush();
    const post = calls.find(c => c.opts.method === 'POST');
    assert.match(post.url, /\/api\/agents\/spawn$/);
});
