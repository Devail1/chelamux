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

const BASE = [
    { name: 'a', window_id: '@201', online: true, claude_running: true, session_status: 'idle', cwd: '/srv/code/alpha' },
    { name: 'b', window_id: '@202', online: true, claude_running: true, session_status: 'idle', cwd: '/srv/code/beta' },
    { name: 'c', window_id: '@203', online: true, claude_running: true, session_status: 'idle', cwd: '/srv/code/gamma' },
];
// A dispatched run, its JUDGE window listed BEFORE its agent window: the run's ONE row is
// drawn from its primary window (the agent, @204), so the agent's pill must lead the judge's.
const runRows = (status = 'running') => [
    { name: 'j', window_id: '@205', online: true, claude_running: false, session_status: 'idle', cwd: '/srv/code/delta',
        dispatched: true, run: { task_id: 'CMX-7', status, role: 'judge' } },
    { name: 'w', window_id: '@204', online: true, claude_running: status === 'running', session_status: 'idle', cwd: '/srv/code/delta',
        dispatched: true, run: { task_id: 'CMX-7', status, role: 'agent' } },
];
let ROWS = BASE;
// The select's order: none of the layouts below. @299, @298: windows the sidebar has not
// drawn yet (/api/agents does not list them), so the tail must read @299, @298. A run window
// that falls out of the sidebar order lands between them, where no expected strip has it.
const OPTIONS = ['@299', '@205', '@204', '@203', '@202', '@201', '@298'];
const TAIL = ['@299', '@298'];

// The REAL terminals panel (index.html), so a switch's renderTerminals runs on the shipped
// markup. Its #term-agent is re-seeded with OPTIONS before each test.
const PANEL = sliceTemplate('<div class="panel active" id="panel-terminals">', '    </div>\n    {% endif %}').replace('{% endif %}', '');
const BODY = `
<aside class="sidebar"><div class="side-list" id="sidebar-agents"></div></aside>
<div id="agent-detail"></div>
<span id="hdr-agents"></span><span id="hdr-next"></span><span id="hdr-updated"></span>
${PANEL}`;
// #term-agent holds a pane for every listed window plus the two undrawn ones, in OPTIONS'
// order; useRows() swaps the /api/agents answer and re-seeds the select to match.
const seedOptions = () => {
    const sel = document.getElementById('term-agent');
    const keep = sel.value;
    const listed = new Set([...ROWS.map(a => a.window_id), ...TAIL]);
    sel.innerHTML = OPTIONS.filter(w => listed.has(w)).map(w => `<option value="${w}">${w}</option>`).join('');
    if (keep) sel.value = keep;
};
const useRows = rows => { ROWS = rows; seedOptions(); };

const server = fakeArchiveServer(() => ROWS);
const json = body => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
function fetchImpl(url, opts) {
    const faked = server.handle(url, opts);
    const u = String(url);
    const body = faked !== undefined ? faked
        : /\/api\/agents$/.test(u) ? ROWS
        : /\/api\/agents\/context/.test(u) ? []
        : /\/api\/summary/.test(u) ? [] : {};
    return json(body);
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
    useRows(BASE);
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
    assert.deepEqual(pills(), ['@202', '@201', '@203', ...TAIL]);
    const options = [...document.getElementById('term-agent').options].map(o => o.value);
    assert.notDeepEqual(pills(), options, 'the pills must not follow #term-agent');
});

test('a group order saved in the layout orders the pills', async () => {
    await setLayout({ order: ['/srv/code/gamma', '/srv/code/alpha', '/srv/code/beta'] });
    assert.deepEqual(groupKeys(), ['/srv/code/gamma', '/srv/code/alpha', '/srv/code/beta']);
    assert.deepEqual(pills(), ['@203', '@201', '@202', ...TAIL]);
});

test('wids the sidebar has not drawn are appended at the END, in their own order, never dropped', async () => {
    assert.deepEqual(chela.sidebarOrder(), ['@201', '@202', '@203']);
    assert.deepEqual(pills(), ['@201', '@202', '@203', ...TAIL]);
});

test('Move up in the sidebar re-orders the pills on the next render, no reload', async () => {
    assert.deepEqual(pills(), ['@201', '@202', '@203', ...TAIL]);
    chela.groupMenuAction('/srv/code/gamma', 'up');
    await settle();
    assert.deepEqual(groupKeys(), ['/srv/code/alpha', '/srv/code/gamma', '/srv/code/beta']);
    assert.deepEqual(pills(), ['@201', '@203', '@202', ...TAIL]);
});

test('a collapsed group keeps its pills, at the group position', async () => {
    await setLayout({ order: ['/srv/code/beta', '/srv/code/alpha', '/srv/code/gamma'] });
    chela.toggleGroup('/srv/code/beta');
    await settle();
    assert.ok(document.querySelector('#sidebar-agents .side-group.collapsed'), 'the group is collapsed');
    assert.deepEqual(pills(), ['@202', '@201', '@203', ...TAIL]);
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

test('a row blocked on you is lifted to Needs you, and its pill leads the strip', async () => {
    useRows(BASE.map(a => a.window_id === '@203' ? { ...a, session_status: 'waiting' } : a));
    await setLayout({ order: [] });
    assert.ok(document.querySelector('#sidebar-agents .side-needs-you [data-item="w:@203"]'), '@203 is in Needs you');
    assert.deepEqual(pills(), ['@203', '@201', '@202', ...TAIL]);
});

test("a dispatched run's pills sit at its group's position, its agent window before its judge", async () => {
    useRows([...BASE, ...runRows()]);
    await setLayout({ order: ['/srv/code/beta', '~dispatched', '/srv/code/alpha', '/srv/code/gamma'] });
    assert.deepEqual(groupKeys(), ['/srv/code/beta', '~dispatched', '/srv/code/alpha', '/srv/code/gamma']);
    assert.deepEqual(pills(), ['@202', '@204', '@205', '@201', '@203', ...TAIL]);
});

test('a session the View filters out of the sidebar keeps its pill at its group position', async () => {
    useRows([...BASE, ...runRows()]);
    await setLayout({
        order: ['/srv/code/beta', '~dispatched', '/srv/code/alpha', '/srv/code/gamma'],
        view: { groupBy: 'folder', env: ['interactive'] },   // Environment: Dispatched agents off
    });
    assert.deepEqual(groupKeys(), ['/srv/code/beta', '/srv/code/alpha', '/srv/code/gamma'], 'the run is not drawn');
    assert.deepEqual(pills(), ['@202', '@204', '@205', '@201', '@203', ...TAIL]);
});

test('an archived run keeps its pills, at the Archived section (the bottom), not its old group', async () => {
    useRows([...BASE, ...runRows('done')]);
    await setLayout({ order: ['~dispatched', '/srv/code/alpha', '/srv/code/beta', '/srv/code/gamma'] });
    assert.deepEqual(pills(), ['@204', '@205', '@201', '@202', '@203', ...TAIL]);
    server.handle('/api/sidebar/archive', { method: 'POST', body: JSON.stringify({ keys: ['run:CMX-7'] }) });
    await nav.refreshSidebar();
    await settle();
    assert.ok(!groupKeys().includes('~dispatched'), 'the run left its group');
    assert.deepEqual(pills(), ['@201', '@202', '@203', '@204', '@205', ...TAIL]);
});

test('the active pill stays the active one, and centred, after a re-order', async () => {
    // jsdom does no layout: give every pill a box (100px apart, 80px wide) in a 200px strip,
    // and record what the strip is scrolled to.
    const W = document.defaultView;
    const proto = W.HTMLElement.prototype;
    const saved = ['offsetLeft', 'clientWidth'].map(k => [k, Object.getOwnPropertyDescriptor(proto, k)]);
    const isPill = el => el.classList.contains('term-pill');
    Object.defineProperty(proto, 'offsetLeft', { configurable: true,
        get() { return isPill(this) ? [...this.parentNode.children].indexOf(this) * 100 : 0; } });
    Object.defineProperty(proto, 'clientWidth', { configurable: true,
        get() { return isPill(this) ? 80 : this.id === 'term-switcher' ? 200 : 0; } });
    const host = document.getElementById('term-switcher');
    let scrolled = null;
    Object.defineProperty(host, 'scrollLeft', { configurable: true, get: () => scrolled, set: v => { scrolled = v; } });
    try {
        document.getElementById('term-agent').value = '@203';
        await setLayout({ order: [] });
        const active = () => [...document.querySelectorAll('#term-switcher .term-pill.active')].map(p => p.dataset.wid);
        assert.deepEqual(pills(), ['@201', '@202', '@203', ...TAIL]);
        assert.deepEqual(active(), ['@203']);
        assert.equal(scrolled, 2 * 100 - (200 - 80) / 2, '@203 (3rd) is centred');
        chela.groupMenuAction('/srv/code/gamma', 'up');
        await settle();
        assert.deepEqual(pills(), ['@201', '@203', '@202', ...TAIL]);
        assert.deepEqual(active(), ['@203']);
        assert.equal(scrolled, 1 * 100 - (200 - 80) / 2, '@203, now 2nd, is centred again');
    } finally {
        delete host.scrollLeft;
        for (const [k, d] of saved) { if (d) Object.defineProperty(proto, k, d); else delete proto[k]; }
    }
});

test('View ▸ Status ▸ Archived (only the archive drawn) keeps every live pill in its group order', async () => {
    await setLayout({ order: ['/srv/code/gamma', '/srv/code/alpha', '/srv/code/beta'], view: { groupBy: 'folder', status: 'archived' } });
    assert.deepEqual(groupKeys().filter(k => k.startsWith('/')), [], 'no folder group is drawn');
    assert.deepEqual(pills(), ['@203', '@201', '@202', ...TAIL]);
});

test('View ▸ Last activity hides a stale row from the sidebar, not its pill', async () => {
    const old = (Date.now() - 10 * 86400000) / 1000;   // 10 days ago, outside "1d"
    useRows(BASE.map(a => a.window_id === '@202' ? { ...a, last_activity: old } : a));
    await setLayout({ order: ['/srv/code/beta', '/srv/code/gamma', '/srv/code/alpha'], view: { groupBy: 'folder', activity: '1d' } });
    assert.ok(!groupKeys().includes('/srv/code/beta'), 'the stale row is not drawn');
    assert.deepEqual(pills(), ['@202', '@203', '@201', ...TAIL]);
});
