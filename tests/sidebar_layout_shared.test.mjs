// CMX-82 — THE SIDEBAR LAYOUT IS SHARED ACROSS DEVICES; COLLAPSE STAYS PER DEVICE.
//
// Liav, 2026-10-11: "the sidebar order should persist across devices". The group order,
// the custom groups (and which row is in which) and the View settings are chela state now
// (chela/sidebar_layout.py, /api/sidebar/layout); collapsed groups, the collapsed sidebar
// and the Archived section's open/closed stay in each browser.
//
// TWO REAL PAGES. Each device is its own jsdom window running its own COPY of the
// dashboard's modules (a copy, so the module loader hands it its own instances — its own
// `_layout`, its own localStorage), and both talk to ONE fake server
// (js_helpers/fake_archive.mjs, which keeps the layout the way the real store does). A
// change made on one is asserted off the OTHER page's real DOM after its periodic refresh
// (refreshSidebar) — never off the page that made it.
//
// Run: pnpm exec vitest run tests/sidebar_layout_shared.test.mjs  (pytest runs it via tests/test_js_suites.py)
import { afterAll, beforeAll, beforeEach, test } from 'vitest';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { activateDom, bootDashboardDom, copyDashboard, flush, SIDEBAR, AGENTS_STORE, VIEW_STORE, NAV } from './js_helpers/dashboard_dom.mjs';
import { fakeArchiveServer } from './js_helpers/fake_archive.mjs';

const BODY = `
<aside class="sidebar">
  <div class="side-list" id="sidebar-agents"></div>
</aside>
<div id="agent-detail"></div>
<span id="hdr-agents"></span><span id="hdr-next"></span><span id="hdr-updated"></span>`;

const ROWS = [
    { name: 'a', window_id: '@201', online: true, claude_running: true, session_status: 'idle', cwd: '/srv/code/alpha' },
    { name: 'b', window_id: '@202', online: true, claude_running: true, session_status: 'idle', cwd: '/srv/code/beta' },
    { name: 'c', window_id: '@203', online: true, claude_running: true, session_status: 'idle', cwd: '/srv/code/gamma' },
];
const server = fakeArchiveServer(() => ROWS);

// A GET /api/sidebar/layout that is held until the test releases it — a page whose
// layout has NOT loaded yet, or a slow read. Its answer is what the server held when the
// request was MADE (a slow answer is a stale one). `null` = answer at once.
let layoutGate = null;
function fetchImpl(url, opts) {
    const u = String(url);
    const method = (opts && opts.method) || 'GET';
    const answer = body => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
    if (layoutGate && method === 'GET' && u.endsWith('/api/sidebar/layout')) {
        const body = server.handle(url, opts);
        return layoutGate.promise.then(() => answer(body));
    }
    const faked = server.handle(url, opts);
    if (faked !== undefined) return answer(faked);
    return answer(/\/api\/(agents|summary)/.test(u) ? [] : {});
}

const tmp = mkdtempSync(join(tmpdir(), 'chela-cmx82-'));
afterAll(() => rmSync(tmp, { recursive: true, force: true }));

// A device: its own copy of the front end (copyDashboard), its own window.
async function bootDevice(name) {
    const jsRoot = copyDashboard(join(tmp, name));
    const { dom, modules } = await bootDashboardDom({
        body: BODY, canvasStub: true, terminalsEnabled: false, fetchImpl,
        jsRoot,
        extraModules: [AGENTS_STORE, VIEW_STORE, NAV, SIDEBAR],
    });
    const dev = { name, dom, nav: modules.nav, sidebar: modules.sidebar, agentsStore: modules.agents.agents, viewStore: modules.view.view };
    await settle();
    return dev;
}

// Let every request in flight (fetch → json → apply → render) finish before the globals
// move to another page — a late render must land in its OWN page.
async function settle() { for (let i = 0; i < 8; i++) await flush(); }

let current = null;
function use(dev) {
    activateDom(dev.dom);
    globalThis.fetch = fetchImpl;
    current = dev;
    return dev;
}

// The periodic refresh, as the page runs it (agents + archive + layout, then a render).
async function refresh(dev) {
    use(dev);
    await dev.sidebar.refreshSidebar();
    await settle();
}

const host = () => document.getElementById('sidebar-agents');
const groupKeys = () => [...host().querySelectorAll('.side-group')].map(g => g.dataset.g);
const groupLabels = () => [...host().querySelectorAll('.side-group .group-name')].map(n => n.textContent);
const group = key => [...host().querySelectorAll('.side-group')].find(g => g.dataset.g === key) || null;
const layoutPosts = () => server.calls.filter(c => c.method === 'POST' && c.path === '/api/sidebar/layout');

let phone, laptop;
beforeAll(async () => {
    phone = await bootDevice('phone');
    laptop = await bootDevice('laptop');
});

// Each test starts from "no layout saved anywhere", both pages having loaded that.
beforeEach(async () => {
    layoutGate = null;
    server.reset();
    for (const dev of [phone, laptop]) {
        use(dev);
        localStorage.clear();
        await refresh(dev);
    }
    server.calls.length = 0;
});

test('a group REORDER on one device is read by the other', async () => {
    await refresh(phone);
    assert.deepEqual(groupKeys(), ['/srv/code/alpha', '/srv/code/beta', '/srv/code/gamma']);
    phone.sidebar.groupMenuAction('/srv/code/gamma', 'up');
    phone.sidebar.groupMenuAction('/srv/code/gamma', 'up');
    assert.deepEqual(groupKeys(), ['/srv/code/gamma', '/srv/code/alpha', '/srv/code/beta']);
    await settle();

    await refresh(laptop);
    assert.deepEqual(groupKeys(), ['/srv/code/gamma', '/srv/code/alpha', '/srv/code/beta'],
        'the laptop did not pick up the phone\'s group order');
});

test('a CUSTOM GROUP and its membership made on one device show on the other', async () => {
    use(laptop);
    laptop.sidebar.viewMenuAction('group:custom');
    const g = laptop.sidebar.newCustomGroup('Infra');
    laptop.sidebar.moveToGroup('w:@202', g.id);
    await settle();

    await refresh(phone);
    assert.deepEqual(groupLabels(), ['Infra', 'Ungrouped'], 'the phone did not pick up the custom group');
    const infra = [...host().querySelectorAll('.side-group')].find(x => x.querySelector('.group-name').textContent === 'Infra');
    const rows = [...infra.querySelectorAll('.agent-row')].map(r => r.dataset.agent);
    assert.deepEqual(rows, ['b'], 'the phone did not pick up the membership');
});

test('a VIEW change on one device is read by the other', async () => {
    use(phone);
    phone.sidebar.viewMenuAction('group:none');
    await settle();

    await refresh(laptop);
    assert.equal(host().querySelectorAll('.group-head').length, 0,
        'the laptop is still grouped — it did not pick up Group by ▸ None');
});

test('COLLAPSED groups do NOT travel: per device, in localStorage, never sent to the server', async () => {
    await refresh(phone);
    phone.sidebar.toggleGroup('/srv/code/beta');
    assert.ok(group('/srv/code/beta').classList.contains('collapsed'));
    assert.deepEqual(JSON.parse(localStorage.getItem('chela_grp_collapsed')), ['/srv/code/beta'],
        'the phone keeps its collapsed groups in its own localStorage');
    await settle();
    assert.equal(layoutPosts().length, 0, 'collapsing a group wrote to the shared layout');

    await refresh(laptop);
    assert.ok(!group('/srv/code/beta').classList.contains('collapsed'), 'the phone\'s collapse reached the laptop');
    assert.equal(localStorage.getItem('chela_grp_collapsed'), null);
});

test('MIGRATION: the first device\'s localStorage layout is imported once; a later device adopts it, never overwrites it', async () => {
    server.reset();   // nothing saved on the server yet
    use(phone);
    localStorage.setItem('chela_sb_group_order', JSON.stringify(['/srv/code/beta', '/srv/code/alpha']));
    localStorage.setItem('chela_sb_view', JSON.stringify({ groupBy: 'folder', sort: 'name' }));
    await refresh(phone);
    assert.deepEqual(server.st.layout.order, ['/srv/code/beta', '/srv/code/alpha'], 'the phone\'s layout was not imported');
    assert.equal(server.st.layout.view.sort, 'name');
    assert.equal(localStorage.getItem('chela_sb_group_order'), null, 'an imported key is spent — removed');
    assert.equal(localStorage.getItem('chela_sb_view'), null);
    assert.deepEqual(groupKeys(), ['/srv/code/beta', '/srv/code/alpha', '/srv/code/gamma']);

    use(laptop);
    localStorage.setItem('chela_sb_group_order', JSON.stringify(['/srv/code/gamma']));
    localStorage.setItem('chela_sb_view', JSON.stringify({ groupBy: 'none' }));
    await refresh(laptop);
    assert.deepEqual(server.st.layout.order, ['/srv/code/beta', '/srv/code/alpha'],
        'a later device\'s old layout overwrote the server\'s');
    assert.equal(server.st.layout.view.groupBy, 'folder');
    assert.deepEqual(groupKeys(), ['/srv/code/beta', '/srv/code/alpha', '/srv/code/gamma'],
        'the later device did not adopt the server\'s layout');
    assert.equal(localStorage.getItem('chela_sb_group_order'), null, 'the later device\'s old keys are spent too');

    // and once is once: the phone, refreshed again, imports nothing more
    const before = layoutPosts().length;
    await refresh(phone);
    assert.equal(layoutPosts().length, before);
});

test('MIGRATION RACE: two devices both saw "no layout"; the second one\'s import never overwrites the first', async () => {
    server.reset();
    // the laptop reads the server while it is still empty — and its answer is slow
    use(laptop);
    localStorage.setItem('chela_sb_group_order', JSON.stringify(['/srv/code/gamma']));
    let release;
    layoutGate = { promise: new Promise(r => { release = r; }) };
    const slow = laptop.sidebar.refreshLayout();   // will answer "layout: null"
    layoutGate = null;
    await settle();
    // meanwhile the phone imports its layout first
    use(phone);
    localStorage.setItem('chela_sb_group_order', JSON.stringify(['/srv/code/beta', '/srv/code/alpha']));
    await refresh(phone);
    assert.deepEqual(server.st.layout.order, ['/srv/code/beta', '/srv/code/alpha']);
    // the laptop's stale "nothing saved" answer arrives: it tries its import
    use(laptop);
    release();
    await slow;
    await settle();
    assert.deepEqual(server.st.layout.order, ['/srv/code/beta', '/srv/code/alpha'],
        'the second device\'s import overwrote the first device\'s layout');
    laptop.sidebar.renderSidebarAgents(ROWS);
    assert.deepEqual(groupKeys(), ['/srv/code/beta', '/srv/code/alpha', '/srv/code/gamma'],
        'the second device did not adopt the winner\'s layout');
    assert.equal(localStorage.getItem('chela_sb_group_order'), null);
});

test('NO WRITE-BACK before the first load: renders and choices on an unloaded page never reach the server', async () => {
    // another device saved a layout
    server.st.layout = {
        order: ['/srv/code/gamma', '/srv/code/beta', '/srv/code/alpha'],
        custom: { groups: [{ id: 'g1', name: 'Ops' }], assign: { 'w:@201': 'g1', 'w:@999': 'g1' } },
        view: { groupBy: 'folder', sort: 'activity' },
    };
    const saved = JSON.parse(JSON.stringify(server.st.layout));
    // a page whose first layout answer has not arrived: a fresh page load
    let release;
    layoutGate = { promise: new Promise(r => { release = r; }) };
    const fresh = await bootDevice('fresh');
    use(fresh);
    fresh.agentsStore.set(ROWS);
    fresh.sidebar.renderSidebarAgents(ROWS);
    fresh.sidebar.groupMenuAction('/srv/code/beta', 'down');
    fresh.sidebar.viewMenuAction('sort:name');
    fresh.sidebar.newCustomGroup('Scratch');
    fresh.sidebar.renderSidebarAgents(ROWS);
    await settle();
    assert.equal(layoutPosts().length, 0, 'an unloaded page wrote its defaults over the saved layout');
    assert.deepEqual(server.st.layout, saved);

    // the answer arrives: the page shows the SAVED layout, and only now may a render
    // write — off what it loaded (the gone @999's assignment pruned, nothing else touched)
    release();
    layoutGate = null;
    await settle();
    fresh.sidebar.renderSidebarAgents(ROWS);
    await settle();
    assert.deepEqual(groupKeys(), ['/srv/code/gamma', '/srv/code/beta', '/srv/code/alpha']);
    assert.deepEqual(server.st.layout, {
        ...saved, custom: { groups: [{ id: 'g1', name: 'Ops' }], assign: { 'w:@201': 'g1' } },
    });
});

test('once loaded, a slow refresh that started BEFORE a write never undoes it', async () => {
    await refresh(phone);
    let release;
    layoutGate = { promise: new Promise(r => { release = r; }) };
    const slow = phone.sidebar.refreshLayout();   // reads the server as it is NOW (no order yet)
    layoutGate = null;
    phone.sidebar.groupMenuAction('/srv/code/gamma', 'up');
    await settle();
    assert.deepEqual(server.st.layout.order, ['/srv/code/alpha', '/srv/code/gamma', '/srv/code/beta']);
    release();
    await slow;
    phone.sidebar.renderSidebarAgents(ROWS);
    assert.deepEqual(groupKeys(), ['/srv/code/alpha', '/srv/code/gamma', '/srv/code/beta'],
        'a stale read put the old order back over the write');
});

// The same rule on the OTHER branch: here the server already HOLDS a layout when the
// slow read starts, so its stale answer is a saved layout (not "nothing saved") — the
// `if (st.layout)` path, which must check freshness too.
test('once loaded, a slow read of a SAVED layout that started before a write never undoes it', async () => {
    await refresh(phone);
    phone.sidebar.groupMenuAction('/srv/code/gamma', 'up');
    await settle();
    assert.deepEqual(server.st.layout.order, ['/srv/code/alpha', '/srv/code/gamma', '/srv/code/beta']);
    let release;
    layoutGate = { promise: new Promise(r => { release = r; }) };
    const slow = phone.sidebar.refreshLayout();   // reads the SAVED layout as it is NOW
    layoutGate = null;
    phone.sidebar.groupMenuAction('/srv/code/gamma', 'up');
    await settle();
    assert.deepEqual(server.st.layout.order, ['/srv/code/gamma', '/srv/code/alpha', '/srv/code/beta']);
    release();
    await slow;
    phone.sidebar.renderSidebarAgents(ROWS);
    assert.deepEqual(groupKeys(), ['/srv/code/gamma', '/srv/code/alpha', '/srv/code/beta'],
        'a stale read of the saved layout put the old order back over the write');
});

// CMX-89 (red on CI): ONE page, TWO refreshes in flight at once — selectView() calls
// refresh() and main.js calls it again on load. Both read "no layout yet"; the first
// imports this browser's old keys and spends them; the second then found no old keys
// and drew the DEFAULTS over the layout just imported, until the next 30s tick.
test('CONCURRENT refreshes on one page: the second never resets the layout the first imported', async () => {
    use(phone);
    localStorage.setItem('chela_sb_view', JSON.stringify({ groupBy: 'custom' }));
    localStorage.setItem('chela_sb_custom_groups', JSON.stringify({ groups: [{ id: 'g1', name: 'Infra' }], assign: {} }));
    const first = phone.sidebar.refreshLayout();          // answers "layout: null" at once
    let release;
    layoutGate = { promise: new Promise(r => { release = r; }) };
    const second = phone.sidebar.refreshLayout();         // also "layout: null", but late
    layoutGate = null;
    await first;
    await settle();
    assert.equal(server.st.layout.view.groupBy, 'custom', 'the first refresh did not import the old keys');
    release();
    await second;
    await settle();
    phone.sidebar.renderSidebarAgents(ROWS);
    // only Custom groups draws an "Ungrouped" group (an empty "Infra" is not drawn);
    // the defaults would draw the folders alpha/beta/gamma
    assert.deepEqual(groupLabels(), ['Ungrouped'],
        `the second refresh reset the imported Custom groups layout — groups drawn: ${groupLabels()}`);
});
