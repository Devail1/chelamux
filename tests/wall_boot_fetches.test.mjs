// CMX-166 — the wall's first load: every boot read ONCE, and the terminals never wait
// behind reads they do not use.
//
// Measured 2026-10-16 on a 7-tile wall: the 7 terminal iframes did not START until
// ~1.7 s, though each loads in ~50 ms. Two causes, both guarded here against the REAL
// index.html + the REAL main.js module graph:
//
//   1. THE DOUBLE BOOT. main.js called refresh() right after selectView('terminals'),
//      which runs refresh() itself, and then re-seeded Decisions, resources, status
//      health and the orchestrator owner with their own enter*() calls. Almost every
//      boot endpoint was fetched twice (/api/orchestrator/status 4×).
//   2. THE CHAIN. refresh() awaited summary → sidebar (agents → archive + layout →
//      /api/restore) → decisions → resources → status health → share-requests →
//      overrides, and only THEN the wall's tick. The wall needs the pane list, no more.
//
// Each boot below runs from its OWN copy of static/js, so each is a fresh page with
// its own module instances (one import would otherwise boot once per file).
//
// Run: node --test tests/wall_boot_fetches.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `pnpm install` for jsdom).
import { after, test } from 'node:test';
import assert from 'node:assert/strict';
import { cpSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { bootDashboardDom, flush, sliceTemplate } from './js_helpers/dashboard_dom.mjs';

const STATIC = join(dirname(fileURLToPath(import.meta.url)), '..', 'chela', 'dashboard', 'static');
const END = '<script>window.CHELA_ASSET_VERSION';
const BODY = sliceTemplate('<div class="app">', END,
    { terminals_enabled: true, wall_tile_dispatched: false }).slice(0, -END.length);

const WIDS = ['@1', '@2', '@3'];
const AGENTS = WIDS.map((w, i) => ({
    name: `agent-${i}`, window_id: w, online: true, session_status: 'idle', claude_running: true,
}));

const tmp = mkdtempSync(join(tmpdir(), 'chela-cmx166-'));
after(() => rmSync(tmp, { recursive: true, force: true }));

// The GridStack vendor global: the tile DOM asserted below is all real, the engine
// only has to answer.
function fakeGridStack() {
    const grid = {
        on() {}, off() {}, save: () => [], destroy() {}, removeWidget(el) { el.remove(); },
        addWidget: el => el, makeWidget: el => el, enableMove() {}, enableResize() {},
        update() {}, batchUpdate() {}, commit() {}, cellHeight() {}, column() {},
        getGridItems: () => [], removeAll() {}, float() {}, engine: { nodes: [] },
    };
    return { init: () => grid };
}

const PENDING = new Promise(() => {});   // a read that never answers

// `hold(path)` → true: that endpoint never answers. `fail(path)` → true: it rejects.
// `onFetch(path)`: runs when the request is made (before it answers).
function makeFetch(log, { hold = () => false, fail = () => false, agents = AGENTS, onFetch = () => {} } = {}) {
    return url => {
        const full = String(url);
        const path = full.split('?')[0];
        log.push(full);
        onFetch(path);
        if (hold(path)) return PENDING;
        if (fail(path)) return Promise.reject(new TypeError('Failed to fetch'));
        const body = path.endsWith('/api/agents') ? agents
            : path.endsWith('/api/agents/context') ? []
                : path.startsWith('/api/term/ready') ? { ready: true }
                    : path.endsWith('/api/restore') ? { sessions: [] }
                        : path.endsWith('/api/rooms') ? { rooms: {}, pending: [] }
                            : {};
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
    };
}

let _n = 0;
async function boot(fetchOpts = {}) {
    const root = join(tmp, `page-${_n++}`);
    for (const d of ['js', 'collab']) cpSync(join(STATIC, d), join(root, d), { recursive: true });
    const log = [];
    globalThis.GridStack = fakeGridStack();
    const { modules } = await bootDashboardDom({
        body: BODY, canvasStub: true, fetchImpl: makeFetch(log, fetchOpts),
        jsRoot: pathToFileURL(join(root, 'js') + '/').href,
        extraModules: ['util.js', 'nav.js', 'terminals.js'],
    });
    for (let i = 0; i < 30; i++) await flush();
    return { log, ...modules };
}

const frames = () => [...document.querySelectorAll('#term-stage iframe.term-frame')];
const countBy = log => log.reduce((m, u) => {
    // /api/term/ready?agent=@N is one read per pane, so it is keyed with its query.
    const k = u.startsWith('/api/term/ready') ? u : u.split('?')[0];
    m[k] = (m[k] || 0) + 1;
    return m;
}, {});

test('one boot fetches every boot endpoint exactly once', async () => {
    const { log } = await boot();
    const counts = countBy(log);
    // Not vacuous: the reads the double boot duplicated were all made.
    for (const ep of ['/api/agents', '/api/agents/context', '/api/summary', '/api/restore',
        '/api/orchestrator/status', '/api/log', '/api/resources', '/api/agents/status_health',
        '/api/share-requests', '/api/overrides', '/api/launcher', '/api/sidebar/archive',
        '/api/sidebar/layout', '/api/update/status', '/api/version']) {
        assert.ok(counts[ep], `${ep} was never fetched on boot — the harness is not booting the page`);
    }
    for (const w of WIDS) assert.ok(counts[`/api/term/ready?agent=${encodeURIComponent(w)}`], `no readiness probe for ${w}`);
    const twice = Object.entries(counts).filter(([, n]) => n > 1);
    assert.deepEqual(twice, [], `boot endpoints fetched more than once: ${JSON.stringify(Object.fromEntries(twice))}`);
    assert.equal(frames().length, WIDS.length, 'the wall did not render its terminals');
});

test('the wall requests its terminal iframes while /api/restore is still pending', async () => {
    const { log } = await boot({ hold: p => p.endsWith('/api/restore') });
    assert.ok(log.includes('/api/restore'), 'the boot never asked for /api/restore, so nothing was held');
    const srcs = frames().map(f => f.getAttribute('src'));
    assert.equal(srcs.length, WIDS.length, 'the terminals waited behind /api/restore');
    for (const w of WIDS) {
        assert.ok(srcs.some(s => s.includes(`/term/${encodeURIComponent(w)}/`)), `no iframe src for ${w}: ${srcs}`);
    }
});

test('the wall does not wait for the other boot reads either (overrides, share-requests, launcher, summary)', async () => {
    const slow = ['/api/overrides', '/api/share-requests', '/api/launcher', '/api/summary',
        '/api/orchestrator/status', '/api/resources', '/api/agents/status_health', '/api/sidebar/archive'];
    await boot({ hold: p => slow.some(s => p.endsWith(s)) });
    assert.equal(frames().length, WIDS.length, 'the terminals waited behind a read the wall does not use');
});

test('a boot read that FAILS does not stop the others: the wall and the sidebar still paint', async () => {
    // The old chain stopped at the first throw: a failed /api/summary skipped the
    // sidebar, decisions, … and the wall.
    const { log } = await boot({ fail: p => p.endsWith('/api/summary') });
    assert.equal(frames().length, WIDS.length, 'a failed /api/summary kept the wall from rendering');
    assert.equal(document.querySelectorAll('#sidebar-agents .agent-row').length, AGENTS.length,
        'a failed /api/summary kept the sidebar from rendering');
    assert.ok(log.includes('/api/overrides'), 'a failed /api/summary skipped the lanes after it');
});

test('the update check waits for the wall: /api/update/status is asked after the iframes exist', async () => {
    let framesAtUpdateCheck = null;
    await boot({ onFetch: p => { if (p.endsWith('/api/update/status')) framesAtUpdateCheck = frames().length; } });
    assert.equal(framesAtUpdateCheck, WIDS.length,
        `/api/update/status (the slowest boot read) was fetched with ${framesAtUpdateCheck} terminals on the wall`);
});

test('a non-list /api/agents reply keeps the last agent list (sidebar refresh)', async () => {
    const { util, nav, log } = await boot();
    assert.deepEqual(util._agentsCache.map(a => a.window_id), WIDS);
    globalThis.fetch = makeFetch(log, { agents: { error: 'tmux unavailable' } });
    await nav.refreshSidebar();
    assert.ok(Array.isArray(util._agentsCache), 'an error body replaced the agent list');
    assert.deepEqual(util._agentsCache.map(a => a.window_id), WIDS, 'the last good agent list was dropped');
    assert.equal(document.querySelectorAll('#sidebar-agents .agent-row').length, AGENTS.length);
});

test('a non-list /api/agents reply reads as "no panes" on the wall, never a throw', async () => {
    const { util, terminals, log } = await boot();
    util.setAgentsCache([]);   // nothing cached: renderTerminals reads /api/agents itself
    globalThis.fetch = makeFetch(log, { agents: { error: 'tmux unavailable' } });
    await assert.doesNotReject(terminals.renderTerminals());
    assert.ok(Array.isArray(util._agentsCache), 'an error body became the agent cache');
    assert.match(document.getElementById('term-stage').textContent, /No active agents/);
});

test('a non-list context reply never poisons the wall: the next rebuild still paints', async () => {
    const { util, terminals, log } = await boot();
    // One rebuild fed an error body for context, then a rebuild with a pane added.
    util.setAgentsCache(AGENTS.slice(0, 2));
    await terminals.renderTerminals(Promise.resolve({ error: 'boom' }));
    for (let i = 0; i < 5; i++) await flush();
    globalThis.fetch = makeFetch(log);
    util.setAgentsCache(AGENTS);
    await assert.doesNotReject(terminals.renderTerminals());
    assert.equal(frames().length, WIDS.length);
});

test('with no agent list cached, the wall reads /api/agents itself and renders those panes', async () => {
    const { util, terminals, log } = await boot();
    util.setAgentsCache([]);
    globalThis.fetch = makeFetch(log, { agents: AGENTS.slice(0, 2) });
    const before = log.filter(u => u === '/api/agents').length;
    await terminals.renderTerminals();
    assert.equal(log.filter(u => u === '/api/agents').length, before + 1, 'an empty cache was not refilled');
    assert.equal(frames().length, 2, 'the wall did not render the panes it just read');
});

test('after the boot, a tick asks /api/update/status at once, without waiting for the wall', async () => {
    const { log } = await boot();
    const main = await import(pathToFileURL(join(tmp, `page-${_n - 1}`, 'js', 'main.js')).href);
    // /api/agents never answers: this tick's wall never runs.
    globalThis.fetch = makeFetch(log, { hold: p => p.endsWith('/api/agents') });
    const before = log.filter(u => u === '/api/update/status').length;
    main.refresh();
    for (let i = 0; i < 5; i++) await flush();
    assert.equal(log.filter(u => u === '/api/update/status').length, before + 1,
        'a later tick held its update check behind the wall');
});
