// CMX-166: the wall's first load. Measured 2026-10-16 on a 7-tile wall, the iframes did
// not START until ~1.7 s (each loads in ~50 ms): main.js booted refresh() twice (selectView
// runs it too) plus enter*() re-seeds, so almost every boot read ran 2-5×, and refresh()
// chained summary → sidebar → /api/restore → … → overrides with the wall's tick LAST.
// Real index.html + main.js graph; each boot is a fresh page from its own static/js copy.
import { after, test } from 'node:test';
import assert from 'node:assert/strict';
import { cpSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { bootDashboardDom, flush, sliceTemplate } from './js_helpers/dashboard_dom.mjs';

const STATIC = join(dirname(fileURLToPath(import.meta.url)), '..', 'chela', 'dashboard', 'static');
const END = '<script>window.CHELA_ASSET_VERSION';
const BODY = sliceTemplate('<div class="app">', END, { terminals_enabled: true, wall_tile_dispatched: false })
    .slice(0, -END.length);
const WIDS = ['@1', '@2', '@3'];
const AGENTS = WIDS.map((w, i) => ({ name: `agent-${i}`, window_id: w, online: true, session_status: 'idle' }));
const tmp = mkdtempSync(join(tmpdir(), 'chela-cmx166-'));
after(() => rmSync(tmp, { recursive: true, force: true }));

const GRID = new Proxy({ engine: { nodes: [] }, save: () => [], getGridItems: () => [] },
    { get: (t, k) => (k in t ? t[k] : el => el) });   // GridStack: the tile DOM is real
const BODIES = { '/api/agents/context': [], '/api/restore': { sessions: [] }, '/api/rooms': { rooms: {}, pending: [] } };

// hold(path): never answers. fail(path): rejects. onFetch(path): runs as the request is made.
function makeFetch(log, { hold = () => false, fail = () => false, agents = AGENTS, onFetch = () => {} } = {}) {
    return url => {
        const path = String(url).split('?')[0];
        log.push(String(url));
        onFetch(path);
        if (hold(path)) return new Promise(() => {});
        if (fail(path)) return Promise.reject(new TypeError('Failed to fetch'));
        const body = path === '/api/agents' ? agents
            : path === '/api/term/ready' ? { ready: true } : (BODIES[path] || {});
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
    };
}

let _n = 0;
async function boot(fetchOpts = {}) {
    const root = join(tmp, `page-${_n++}`);
    for (const d of ['js', 'collab']) cpSync(join(STATIC, d), join(root, d), { recursive: true });
    const log = [];
    globalThis.GridStack = { init: () => GRID };
    const { modules } = await bootDashboardDom({
        body: BODY, canvasStub: true, fetchImpl: makeFetch(log, fetchOpts),
        jsRoot: pathToFileURL(join(root, 'js') + '/').href,
        extraModules: ['main.js', 'util.js', 'nav.js', 'terminals.js'],
    });
    for (let i = 0; i < 30; i++) await flush();
    return { log, ...modules };
}

const frames = () => [...document.querySelectorAll('#term-stage iframe.term-frame')];
const rows = () => document.querySelectorAll('#sidebar-agents .agent-row').length;
const n = (log, path) => log.filter(u => u === path).length;

test('one boot fetches every boot endpoint exactly once', async () => {
    const { log } = await boot();
    const counts = {};   // /api/term/ready?agent=@N is one read per pane: keyed with its query
    for (const u of log) { const k = u.startsWith('/api/term/ready') ? u : u.split('?')[0]; counts[k] = (counts[k] || 0) + 1; }
    for (const ep of ['/api/agents', '/api/agents/context', '/api/summary', '/api/restore',
        '/api/orchestrator/status', '/api/log', '/api/resources', '/api/agents/status_health',
        '/api/share-requests', '/api/overrides', '/api/launcher', '/api/sidebar/archive',
        '/api/sidebar/layout', '/api/update/status', '/api/version',
        ...WIDS.map(w => `/api/term/ready?agent=${encodeURIComponent(w)}`)]) {
        assert.ok(counts[ep], `${ep} was never fetched on boot: the harness is not booting the page`);
    }
    const twice = Object.entries(counts).filter(([, c]) => c > 1);
    assert.deepEqual(twice, [], `boot endpoints fetched more than once: ${JSON.stringify(twice)}`);
    assert.equal(frames().length, WIDS.length, 'the wall did not render its terminals');
});

test('the wall requests its iframes while /api/restore (and every read it does not use) is pending', async () => {
    const held = ['/api/restore', '/api/overrides', '/api/share-requests', '/api/launcher', '/api/summary',
        '/api/orchestrator/status', '/api/resources', '/api/agents/status_health'];
    const { log } = await boot({ hold: p => held.includes(p) });
    assert.ok(log.includes('/api/restore'), 'the boot never asked for /api/restore, so nothing was held');
    const srcs = frames().map(f => f.getAttribute('src'));
    for (const w of WIDS) assert.ok(srcs.some(s => s.includes(`/term/${encodeURIComponent(w)}/`)), `no iframe for ${w}: ${srcs}`);
});

test('a boot read that FAILS does not stop the others: the wall and the sidebar still paint', async () => {
    const { log } = await boot({ fail: p => p === '/api/summary' });   // the old chain stopped here
    assert.equal(frames().length, WIDS.length, 'a failed /api/summary kept the wall from rendering');
    assert.equal(rows(), AGENTS.length, 'a failed /api/summary kept the sidebar from rendering');
    assert.ok(log.includes('/api/overrides'), 'a failed /api/summary skipped the lanes after it');
});

test('/api/update/status waits for the wall on boot, and is asked at once on a later tick', async () => {
    let framesAtCheck = null;
    const { log, main } = await boot({ onFetch: p => { if (p === '/api/update/status') framesAtCheck = frames().length; } });
    assert.equal(framesAtCheck, WIDS.length, `the boot update check ran with ${framesAtCheck} terminals on the wall`);
    globalThis.fetch = makeFetch(log, { hold: p => p === '/api/agents' });   // this tick's wall never runs
    const before = n(log, '/api/update/status');
    main.refresh();
    for (let i = 0; i < 5; i++) await flush();
    assert.equal(n(log, '/api/update/status'), before + 1, 'a later tick held its update check behind the wall');
});

test('a non-list /api/agents reply keeps the sidebar\'s last agent list', async () => {
    const { util, nav, log } = await boot();
    globalThis.fetch = makeFetch(log, { agents: { error: 'tmux unavailable' } });
    await nav.refreshSidebar();
    assert.ok(Array.isArray(util._agentsCache), 'an error body replaced the agent list');
    assert.deepEqual(util._agentsCache.map(a => a.window_id), WIDS, 'the last good agent list was dropped');
    assert.equal(rows(), AGENTS.length);
});

test('with no agent list cached, the wall reads /api/agents itself; a non-list reply is "no panes"', async () => {
    const { util, terminals, log } = await boot();
    util.setAgentsCache([]);
    globalThis.fetch = makeFetch(log, { agents: AGENTS.slice(0, 2) });
    const before = n(log, '/api/agents');
    await terminals.renderTerminals();
    assert.equal(n(log, '/api/agents'), before + 1, 'an empty cache was not refilled');
    assert.equal(frames().length, 2, 'the wall did not render the panes it just read');
    util.setAgentsCache([]);
    globalThis.fetch = makeFetch(log, { agents: { error: 'tmux unavailable' } });
    await assert.doesNotReject(terminals.renderTerminals());
    assert.ok(Array.isArray(util._agentsCache), 'an error body became the agent cache');
    assert.match(document.getElementById('term-stage').textContent, /No active agents/);
});

test('a non-list context reply never poisons the wall: the next rebuild still paints', async () => {
    const { util, terminals, log } = await boot();
    util.setAgentsCache(AGENTS.slice(0, 2));
    await terminals.renderTerminals(Promise.resolve({ error: 'boom' }));
    for (let i = 0; i < 5; i++) await flush();
    globalThis.fetch = makeFetch(log);
    util.setAgentsCache(AGENTS);   // a pane added: a full rebuild
    await assert.doesNotReject(terminals.renderTerminals());
    assert.equal(frames().length, WIDS.length);
});
