// CMX-78 — THE SIDEBAR'S ARCHIVE GUARDS (nav.js), EACH ASSERTED ON ITS OWN.
//
// The archive is server state (CMX-75, chela/sidebar_archive.py). nav.js keeps this page's
// copy of it and guards every path that reads or writes it:
//
//   * the periodic refresh re-reads the archive BEFORE it renders, so what another device
//     archived or resumed is drawn on this one;
//   * the prune (a hidden row that woke up or whose window is gone is unhidden server-side)
//     never runs before the first /api/sidebar/archive answer, and never twice at once;
//   * a non-archive answer (an error body) never replaces the archive this page holds;
//   * the one-time localStorage import is single-flight, and forgets the local copy only
//     once the server took it.
//
// Each test boots its OWN page (a copy of static/js, so a fresh `_arch`) against the fake
// server (js_helpers/fake_archive.mjs), with the request it is about HELD until the test
// releases it — the in-between state is where every one of these guards earns its keep.
//
// Run: node --test tests/sidebar_archive_guards.test.mjs  (pytest runs it via tests/test_js_suites.py)
import { after, test } from 'node:test';
import assert from 'node:assert/strict';
import { cpSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { activateDom, bootDashboardDom, flush } from './js_helpers/dashboard_dom.mjs';
import { fakeArchiveServer } from './js_helpers/fake_archive.mjs';

const STATIC = join(dirname(fileURLToPath(import.meta.url)), '..', 'chela', 'dashboard', 'static');

const BODY = `
<aside class="sidebar">
  <div class="side-list" id="sidebar-agents"></div>
</aside>
<div id="agent-detail"></div>
<span id="hdr-agents"></span><span id="hdr-next"></span><span id="hdr-updated"></span>`;

const ROWS = [
    { name: 'a', window_id: '@201', online: true, claude_running: true, session_status: 'idle', cwd: '/srv/code/alpha' },
];

const tmp = mkdtempSync(join(tmpdir(), 'chela-cmx78-'));
after(() => rmSync(tmp, { recursive: true, force: true }));

const deferred = () => {
    let resolve;
    const promise = new Promise(r => { resolve = r; });
    return { promise, resolve };
};

let _n = 0;
// A fresh page + its own fake server. `hold` names requests ("METHOD /path") to park until
// `release(name)`; `override(method, path, body)` may answer a request instead of the server.
async function boot({ hidden = [], sessions = [], hold = [], seedLocalStorage, override } = {}) {
    const server = fakeArchiveServer(() => ROWS);
    server.st.hidden.push(...hidden);
    server.st.sessions.push(...sessions);
    const gates = new Map(hold.map(h => [h, deferred()]));
    const sent = [];   // every request nav.js made, in order, at the moment it made it
    function fetchImpl(url, opts) {
        const u = String(url);
        const method = (opts && opts.method) || 'GET';
        const path = u.replace(/^https?:\/\/[^/]+/, '').replace(/\?.*$/, '');
        sent.push({ method, path, body: opts && opts.body ? JSON.parse(opts.body) : null });
        const answer = () => {
            const own = override && override(method, path);
            const body = own !== undefined ? own : server.handle(url, opts);
            const out = body !== undefined ? body : (/\/api\/(agents|summary)/.test(u) ? [] : {});
            return { ok: true, status: 200, json: () => Promise.resolve(out) };
        };
        const gate = gates.get(`${method} ${path}`);
        // a held request reaches the server only when released — it has not happened yet
        return gate ? gate.promise.then(answer) : Promise.resolve(answer());
    }
    const root = join(tmp, `page-${_n++}`);
    for (const d of ['js', 'collab']) cpSync(join(STATIC, d), join(root, d), { recursive: true });
    const { dom, modules } = await bootDashboardDom({
        body: BODY, canvasStub: true, terminalsEnabled: false, fetchImpl, seedLocalStorage,
        jsRoot: pathToFileURL(join(root, 'js') + '/').href,
        extraModules: ['util.js', 'nav.js'],
    });
    const page = {
        dom, server, sent, nav: modules.nav, util: modules.util,
        use() { activateDom(dom); globalThis.fetch = fetchImpl; return page; },
        async release(name) { gates.get(name).resolve(); gates.delete(name); await settle(); },
        posts: path => sent.filter(c => c.method === 'POST' && c.path === path),
    };
    await settle();
    return page;
}

async function settle() { for (let i = 0; i < 8; i++) await flush(); }

const host = () => document.getElementById('sidebar-agents');
// What the sidebar SAYS is archived: "Show archived (N)", or nothing when N is 0.
function archivedCount() {
    const t = host().querySelector('.side-archived-toggle');
    const m = t && /Show archived \((\d+)\)/.exec(t.textContent);
    return m ? Number(m[1]) : 0;
}
const record = sid => ({ key: `s:${sid}`, session_id: sid, name: sid, cwd: '/srv/code/alpha', wid: '@77', archived_at: 1 });

test('the periodic refresh draws what ANOTHER device archived, and what it resumed', async () => {
    const page = (await boot()).use();
    await page.nav.refreshSidebar();
    assert.equal(archivedCount(), 0);

    page.server.st.sessions.push(record('sid-other'));    // the laptop archived a session
    await page.nav.refreshSidebar();                       // the phone's next tick
    assert.equal(archivedCount(), 1, 'the next refresh renders the archive as the server holds it now');

    page.server.st.sessions.length = 0;                    // ...and the laptop resumed it
    await page.nav.refreshSidebar();
    assert.equal(archivedCount(), 0, 'a session resumed elsewhere leaves this page\'s Archived too');
});

test('nothing is pruned before the first archive answer — then the prune runs', async () => {
    // A hidden run whose row is not in the list: once the archive is KNOWN it is pruned.
    // Before that, the page has not seen the archive, and must not act on it.
    const page = (await boot({ hidden: ['run:CMX-9'], hold: ['GET /api/sidebar/archive'] })).use();
    page.util.setAgentsCache(ROWS);
    page.nav.renderSidebarAgents(ROWS);                    // a render before the archive loads
    await settle();
    assert.ok(host().querySelector('.agent-row[data-agent="a"]'), 'the rows still render');
    assert.deepEqual(page.posts('/api/sidebar/unarchive'), [], 'no prune before the first answer');

    await page.release('GET /api/sidebar/archive');        // the archive arrives
    page.nav.renderSidebarAgents(ROWS);
    await settle();
    assert.deepEqual(page.posts('/api/sidebar/unarchive').map(c => c.body.keys), [['run:CMX-9']],
        'once the archive is known, the gone row is pruned (the setup can prune at all)');
});

test('a prune in flight is never sent twice, and the next one goes once it lands', async () => {
    const page = (await boot({ hidden: ['run:CMX-9'], hold: ['POST /api/sidebar/unarchive'] })).use();
    // The boot's own refresh loaded the archive and sent the prune — held, so the server
    // still lists the key: the next refresh re-reads it as hidden.
    assert.equal(page.posts('/api/sidebar/unarchive').length, 1);
    await page.nav.refreshSidebar();
    await settle();
    assert.equal(page.posts('/api/sidebar/unarchive').length, 1, 'the in-flight prune is not resent');

    await page.release('POST /api/sidebar/unarchive');
    assert.deepEqual(page.server.st.hidden, []);
    page.server.st.hidden.push('run:CMX-8');               // another gone row, later
    await page.nav.refreshSidebar();
    await settle();
    assert.deepEqual(page.posts('/api/sidebar/unarchive').map(c => c.body.keys), [['run:CMX-9'], ['run:CMX-8']],
        'a settled prune frees the next one');
});

test('an error answer from the archive route never wipes the archive this page holds', async () => {
    let broken = false;
    const page = (await boot({
        sessions: [record('sid-kept')],
        override: (m, p) => (broken && m === 'GET' && p === '/api/sidebar/archive' ? { error: 'unauthorized' } : undefined),
    })).use();
    await page.nav.refreshSidebar();
    assert.equal(archivedCount(), 1);
    broken = true;
    await page.nav.refreshSidebar();
    assert.equal(archivedCount(), 1, 'a body that is not an archive keeps the last answer');
});

test('the localStorage import is sent ONCE while in flight, and forgotten only once the server took it', async () => {
    const page = (await boot({
        seedLocalStorage: { chela_sb_archived: JSON.stringify(['run:CMX-5']) },
        hold: ['POST /api/sidebar/archive/migrate'],
    })).use();
    assert.equal(page.posts('/api/sidebar/archive/migrate').length, 1);
    await page.nav.refreshArchive();                       // a second tick while it is held
    assert.equal(page.posts('/api/sidebar/archive/migrate').length, 1, 'one import in flight');
    await page.release('POST /api/sidebar/archive/migrate');
    assert.equal(localStorage.getItem('chela_sb_archived'), null, 'imported ⇒ the local copy goes');
    assert.deepEqual(page.posts('/api/sidebar/archive/migrate')[0].body.keys, ['run:CMX-5']);
});

test('a refused localStorage import keeps the local copy, and the next refresh retries it', async () => {
    let refuse = true;
    const page = (await boot({
        seedLocalStorage: { chela_sb_archived: JSON.stringify(['run:CMX-5']) },
        override: (m, p) => (refuse && p === '/api/sidebar/archive/migrate' ? { ok: false, error: 'nope' } : undefined),
    })).use();
    assert.equal(page.posts('/api/sidebar/archive/migrate').length, 1);
    assert.equal(localStorage.getItem('chela_sb_archived'), JSON.stringify(['run:CMX-5']),
        'not imported ⇒ the only copy stays');
    refuse = false;
    await page.nav.refreshArchive();
    assert.equal(page.posts('/api/sidebar/archive/migrate').length, 2, 'the next refresh retries');
    assert.equal(localStorage.getItem('chela_sb_archived'), null);
});
