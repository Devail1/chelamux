// CMX-165 — THE PHONE'S SESSION PILLS FOLLOW THE SIDEBAR'S ORDER.
//
// Liav, 2026-10-16 (phone screenshot): the pill strip over the phone's single terminal
// listed sessions in #term-agent's option order, not the sidebar's. The sidebar's order
// (group order, Move up / Move down, custom groups, each group's sort) is the one order;
// nav.js exposes it (chela.sidebarOrder) and terminals.js sorts the pills by it.
//
// The real dashboard boots in jsdom at phone width, in single mode; the sidebar renders off
// the fake layout server (js_helpers/fake_archive.mjs), and the pills are read off the real
// #term-switcher. #term-agent's options are deliberately in a DIFFERENT order from every
// layout below, so a strip that follows the options fails each test.
//
// Run: node --test tests/mobile_pills_sidebar_order.test.mjs  (pytest runs it via tests/test_js_suites.py)
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { bootDashboardDom, flush, sliceTemplate } from './js_helpers/dashboard_dom.mjs';
import { fakeArchiveServer } from './js_helpers/fake_archive.mjs';

const ROWS = [
    { name: 'a', window_id: '@201', online: true, claude_running: true, session_status: 'idle', cwd: '/srv/code/alpha' },
    { name: 'b', window_id: '@202', online: true, claude_running: true, session_status: 'idle', cwd: '/srv/code/beta' },
    { name: 'c', window_id: '@203', online: true, claude_running: true, session_status: 'idle', cwd: '/srv/code/gamma' },
];
// The select's order: none of the layouts below. @299 is a window the sidebar has not
// drawn yet (spawned since its last refresh): /api/agents does not list it.
const OPTIONS = ['@299', '@203', '@202', '@201'];

// The REAL terminals panel (index.html), so a switch's renderTerminals runs on the shipped
// markup. Its #term-agent is re-seeded with OPTIONS before each test.
const PANEL = sliceTemplate('<div class="panel active" id="panel-terminals">', '    </div>\n    {% endif %}').replace('{% endif %}', '');
const BODY = `
<aside class="sidebar"><div class="side-list" id="sidebar-agents"></div></aside>
<div id="agent-detail"></div>
<span id="hdr-agents"></span><span id="hdr-next"></span><span id="hdr-updated"></span>
${PANEL}`;
const seedOptions = () => {
    document.getElementById('term-agent').innerHTML = OPTIONS.map(w => `<option value="${w}">${w}</option>`).join('');
};

const server = fakeArchiveServer(() => ROWS);
function fetchImpl(url, opts) {
    const faked = server.handle(url, opts);
    const u = String(url);
    const body = faked !== undefined ? faked
        : /\/api\/agents$/.test(u) ? ROWS
        : /\/api\/agents\/context/.test(u) ? []
        : /\/api\/summary/.test(u) ? [] : {};
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
}

let nav, terminals, chela;
before(async () => {
    const booted = await bootDashboardDom({
        body: BODY, phone: true, canvasStub: true, fetchImpl,
        seedLocalStorage: { pc_term_mode: 'single' },
        extraModules: ['nav.js', 'terminals.js'],
    });
    nav = booted.modules.nav;
    terminals = booted.modules.terminals;
    chela = booted.chela;
    globalThis.fetch = fetchImpl;
    await settle();   // the boot's own refresh lands before any test seeds the options
});

async function settle() { for (let i = 0; i < 8; i++) await flush(); }
async function setLayout(fields) {
    server.handle('/api/sidebar/layout', { method: 'POST', body: JSON.stringify(fields) });
    await nav.refreshSidebar();
    await settle();
}
const pills = () => [...document.querySelectorAll('#term-switcher .term-pill')].map(p => p.dataset.wid);
const groupKeys = () => [...document.querySelectorAll('#sidebar-agents .side-group')].map(g => g.dataset.g);

beforeEach(async () => {
    server.reset();
    seedOptions();
    document.getElementById('term-agent').value = '@201';
    await setLayout({ order: [], custom: { groups: [], assign: {} }, view: { groupBy: 'folder' } });
});

test('custom groups: the pills come out in the sidebar order, not the options order', async () => {
    await setLayout({
        view: { groupBy: 'custom' },
        custom: { groups: [{ id: 'x', name: 'First' }, { id: 'y', name: 'Second' }], assign: { 'w:@202': 'x', 'w:@201': 'y' } },
    });
    // sidebar: First [b], Second [a], Ungrouped [c]
    assert.deepEqual(chela.sidebarOrder(), ['@202', '@201', '@203']);
    assert.deepEqual(pills(), ['@202', '@201', '@203', '@299']);
    assert.notDeepEqual(pills(), OPTIONS, 'the pills must not follow #term-agent');
});

test('a group order saved in the layout orders the pills', async () => {
    await setLayout({ order: ['/srv/code/gamma', '/srv/code/alpha', '/srv/code/beta'] });
    assert.deepEqual(groupKeys(), ['/srv/code/gamma', '/srv/code/alpha', '/srv/code/beta']);
    assert.deepEqual(pills(), ['@203', '@201', '@202', '@299']);
});

test('a wid the sidebar has not drawn is appended at the END, never dropped', async () => {
    assert.deepEqual(chela.sidebarOrder(), ['@201', '@202', '@203']);
    assert.deepEqual(pills(), ['@201', '@202', '@203', '@299']);
});

test('Move up in the sidebar re-orders the pills on the next render, no reload', async () => {
    assert.deepEqual(pills(), ['@201', '@202', '@203', '@299']);
    chela.groupMenuAction('/srv/code/gamma', 'up');
    await settle();
    assert.deepEqual(groupKeys(), ['/srv/code/alpha', '/srv/code/gamma', '/srv/code/beta']);
    assert.deepEqual(pills(), ['@201', '@203', '@202', '@299']);
});

test('a collapsed group keeps its pills, at the group position', async () => {
    await setLayout({ order: ['/srv/code/beta', '/srv/code/alpha', '/srv/code/gamma'] });
    chela.toggleGroup('/srv/code/beta');
    await settle();
    assert.ok(document.querySelector('#sidebar-agents .side-group.collapsed'), 'the group is collapsed');
    assert.deepEqual(pills(), ['@202', '@201', '@203', '@299']);
    chela.toggleGroup('/srv/code/beta');
});

test('the header swipe steps through the sessions in the sidebar order', async () => {
    await setLayout({ order: ['/srv/code/gamma', '/srv/code/alpha', '/srv/code/beta'] });
    const sel = document.getElementById('term-agent');
    sel.value = '@203';
    terminals.stepAgentMobile(1);   // next after gamma's @203 is alpha's @201 (options: @202)
    assert.equal(sel.value, '@201');
    terminals.stepAgentMobile(-1);
    assert.equal(sel.value, '@203');
    await settle();   // let the switch's renderTerminals finish inside this test
});
