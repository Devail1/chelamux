// CMX-82 — THE SIDEBAR LAYOUT IS SERVER STATE (Liav, 2026-10-11: "the sidebar order should
// persist across devices").
//
// Group order, custom groups + membership and the View settings live in chela state
// (chela/sidebar_layout.py, /api/sidebar/layout); collapsed groups, the folded sidebar and
// the Archived section's open state stay in each browser. These run the REAL nav.js in a
// REAL DOM against a stand-in for the server (tests/js_helpers/fake_archive.mjs) and read
// back the DOM and every request nav.js made.
//
// The page boots with the server's first layout answer HELD, so the "before the first
// load" tests see exactly what a slow first answer looks like.
//
// Run: node --test tests/sidebar_layout.test.mjs  (pytest runs it via tests/test_js_suites.py)
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import { bootDashboardDom, flush } from './js_helpers/dashboard_dom.mjs';
import { fakeArchiveServer } from './js_helpers/fake_archive.mjs';

const BODY = `
<aside class="sidebar">
  <div class="side-head"><span class="side-title">Sessions</span>
    <button class="side-view-btn" id="btn-sidebar-view" onclick="chela.openViewMenu(event)"></button></div>
  <div class="side-list" id="sidebar-agents"></div>
</aside>
<div id="agent-detail"></div>
<span id="hdr-agents"></span><span id="hdr-next"></span><span id="hdr-updated"></span>`;

let nav, util;
const server = fakeArchiveServer(() => util && util._agentsCache);
// The layout GETs, held until released — the page's FIRST answer is late on purpose.
let holdLayout = true;
const heldLayout = [];
const answer = body => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });

// This browser's pre-CMX-82 localStorage layout: Group by Custom groups, one group "Infra"
// holding a live window (@100) AND a window that is gone (@999).
const LEGACY = {
    chela_sb_view: JSON.stringify({ groupBy: 'custom' }),
    chela_sb_custom_groups: JSON.stringify({
        groups: [{ id: 'g1', name: 'Infra' }], assign: { 'w:@100': 'g1', 'w:@999': 'g1' },
    }),
    chela_sb_group_order: JSON.stringify(['~custom:g1', '~ungrouped']),
};

before(async () => {
    ({ modules: { util, nav } } = await bootDashboardDom({
        body: BODY,
        canvasStub: true,
        terminalsEnabled: false,
        seedLocalStorage: LEGACY,
        fetchImpl: (url, opts) => {
            const u = String(url);
            const isLayoutGet = u.replace(/\?.*$/, '').endsWith('/api/sidebar/layout')
                && !((opts && opts.method) || '').match(/POST/i);
            if (isLayoutGet && holdLayout) {
                return new Promise(resolve => heldLayout.push(() => resolve(
                    { ok: true, status: 200, json: () => Promise.resolve(server.handle(url, opts)) })));
            }
            const faked = server.handle(url, opts);
            if (faked !== undefined) return answer(faked);
            return answer(/\/api\/(agents|summary)/.test(u) ? [] : {});
        },
        extraModules: ['util.js', 'nav.js'],
    }));
});

const a100 = { name: 'a', window_id: '@100', online: true, claude_running: true, session_status: 'idle', cwd: '/srv/code/alpha' };
const b101 = { name: 'b', window_id: '@101', online: true, claude_running: true, session_status: 'idle', cwd: '/srv/code/beta' };

function render(rows) {
    util.setAgentsCache(rows);
    nav.renderSidebarAgents(rows);
}
const host = () => document.getElementById('sidebar-agents');
const groupLabels = () => [...host().querySelectorAll('.side-group .group-name')].map(n => n.textContent);
const groupByLabel = label => [...host().querySelectorAll('.side-group')]
    .find(g => (g.querySelector('.group-name') || {}).textContent === label) || null;
const rowsUnder = label => [...groupByLabel(label).querySelectorAll('.agent-row')].map(r => r.dataset.agent);
const layoutWrites = () => server.calls.filter(c => c.method === 'POST' && c.path.startsWith('/api/sidebar/layout'));
const settle = async () => { for (let i = 0; i < 5; i++) await flush(); await new Promise(r => setTimeout(r, 0)); };

// Another device, talking to the same server.
const otherDevice = {
    save: patch => server.handle('http://localhost/api/sidebar/layout', { method: 'POST', body: JSON.stringify(patch) }),
    read: () => server.handle('http://localhost/api/sidebar/layout', { method: 'GET' }),
};

// --- 1. no write-back before the first load ----------------------------------------------

test('a render before the layout has loaded writes NOTHING back — not even the gone-window prune', async () => {
    await settle();
    assert.ok(heldLayout.length >= 1, 'the page never asked the server for its layout on boot');
    render([a100]);   // @999 is gone: a LOADED layout would prune its assignment
    render([a100]);
    await settle();
    assert.deepEqual(layoutWrites(), [], 'a render before the first load wrote to the server');
    assert.equal(server.lay.layout, null, 'the server gained a layout no device saved');
    // meanwhile the page draws from this browser's old keys, untouched
    assert.deepEqual(rowsUnder('Infra'), ['a']);
    assert.deepEqual(JSON.parse(localStorage.getItem('chela_sb_custom_groups')).assign,
        { 'w:@100': 'g1', 'w:@999': 'g1' }, 'the pre-load render pruned this browser\'s copy');
});

// --- 2. migration: imported once, never over an existing layout -------------------------

test('the old localStorage layout is imported ONCE into an empty server, then the old keys go', async () => {
    holdLayout = false;
    heldLayout.splice(0).forEach(release => release());
    await settle();
    const migrations = layoutWrites().filter(c => c.path === '/api/sidebar/layout/migrate');
    assert.equal(migrations.length, 1, 'the old layout must be offered exactly once');
    assert.equal(server.lay.layout.view.groupBy, 'custom');
    assert.deepEqual(server.lay.layout.custom.groups, [{ id: 'g1', name: 'Infra' }]);
    assert.deepEqual(server.lay.layout.order, ['~custom:g1', '~ungrouped']);
    for (const k of ['chela_sb_view', 'chela_sb_custom_groups', 'chela_sb_group_order']) {
        assert.equal(localStorage.getItem(k), null, `${k} was kept after the import`);
    }
    // loaded now: the next render drops the gone window's assignment — on the SERVER
    render([a100]);
    await settle();
    assert.deepEqual(server.lay.layout.custom.assign, { 'w:@100': 'g1' });
    // and a further refresh imports nothing again
    await nav.refreshLayout();
    assert.equal(layoutWrites().filter(c => c.path === '/api/sidebar/layout/migrate').length, 1);
});

test('a later device ADOPTS the server\'s layout — its own old keys never overwrite it', async () => {
    const before = JSON.stringify(server.lay.layout);
    // this browser now holds a different old layout (as a second, later device would)
    localStorage.setItem('chela_sb_view', JSON.stringify({ groupBy: 'none' }));
    localStorage.setItem('chela_sb_group_order', JSON.stringify(['/srv/code/zzz']));
    const n = layoutWrites().length;
    await nav.refreshLayout();
    await settle();
    assert.equal(layoutWrites().length, n, 'a device offered its old layout over an existing one');
    assert.equal(JSON.stringify(server.lay.layout), before, 'the server layout was overwritten');
    render([a100]);
    assert.deepEqual(groupLabels(), ['Infra'], 'the page did not adopt the server\'s layout');
    assert.equal(localStorage.getItem('chela_sb_view'), null, 'the stale old key was kept');
});

// --- 3. two devices, one layout ----------------------------------------------------------

test('a reorder, a custom group and a View change on one device are read by the other', async () => {
    // THIS device: a new custom group with a row in it, a reorder, a sort change
    render([a100, b101]);
    const g = nav.newCustomGroup('Apps');
    nav.moveToGroup('w:@101', g.id);
    nav.viewMenuAction('sort:name');
    assert.deepEqual(groupLabels(), ['Infra', 'Apps']);
    nav.groupMenuAction(`~custom:${g.id}`, 'up');
    assert.deepEqual(groupLabels(), ['Apps', 'Infra']);
    await settle();
    const seen = otherDevice.read();
    assert.ok(seen.layout.custom.groups.some(x => x.id === g.id && x.name === 'Apps'), 'the other device never sees the new group');
    assert.equal(seen.layout.custom.assign['w:@101'], g.id, 'the other device never sees the membership');
    assert.equal(seen.layout.view.sort, 'name', 'the other device never sees the View change');
    assert.deepEqual(seen.layout.order.slice(0, 2), [`~custom:${g.id}`, '~custom:g1'], 'the other device never sees the reorder');

    // the OTHER device: Group by Folder, then a custom group renamed — this page re-reads it
    otherDevice.save({ view: { ...seen.layout.view, groupBy: 'folder' } });
    otherDevice.save({ order: ['/srv/code/beta', '/srv/code/alpha'] });
    await nav.refreshLayout();
    render([a100, b101]);
    assert.deepEqual([...host().querySelectorAll('.side-group')].map(x => x.dataset.g),
        ['/srv/code/beta', '/srv/code/alpha'], 'the other device\'s Group by / order did not show up here');
});

// --- 4. collapsed state stays on the device ----------------------------------------------

test('collapsing a group / opening Archived / folding the sidebar never travels to the server', async () => {
    render([a100, b101]);
    const n = layoutWrites().length;
    nav.toggleGroup('/srv/code/alpha');
    nav.toggleArchivedSection();
    window.chela.toggleSidebar && window.chela.toggleSidebar();
    await settle();
    assert.equal(layoutWrites().length, n, 'a collapse was written to the shared layout');
    assert.ok(JSON.parse(localStorage.getItem('chela_grp_collapsed')).includes('/srv/code/alpha'));
    // another device's layout change does not undo this one's collapse
    otherDevice.save({ order: ['/srv/code/alpha', '/srv/code/beta'] });
    await nav.refreshLayout();
    render([a100, b101]);
    const alpha = [...host().querySelectorAll('.side-group')].find(x => x.dataset.g === '/srv/code/alpha');
    assert.ok(alpha.classList.contains('collapsed'), 'a layout refresh un-collapsed this device\'s group');
    assert.ok(!JSON.stringify(server.lay.layout).includes('collapsed'), 'collapsed state reached the server');
});
