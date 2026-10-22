// CMX-217 (CMX-212 P03): sse.js is gone; each SSE event's handler lives with its owner and
// subscribes through frontend/src/shared/events. This drives the handlers sse.js used to hold
// that no other suite reaches: `runs`/`hello` (features/work since CMX-233, the run-state
// toasts and the board redraw), `windows` (util.js invalidates the agent cache; terminals.js reconciles the Wall)
// and `term-ready` (terminals.js swaps a placeholder for its iframe). The real page, booted
// through the real entry, a fake EventSource (jsdom has none). `log`, `orchestrator` and
// `overrides` are driven by decisions_sse, orchestrator_ui and approvals_wiring.
//
// Run: pnpm exec vitest run tests/sse_owners.test.mjs (tests/test_js_suites.py runs it in pytest).
import { beforeAll, beforeEach, test } from 'vitest';
import assert from 'node:assert/strict';
import { bootDashboardDom, flush, WALL } from './js_helpers/dashboard_dom.mjs';

const PANEL = `
<div class="panel" id="panel-terminals">
  <button id="term-mode-single"></button>
  <button id="term-mode-wall"></button>
  <select id="term-agent"></select>
  <span id="term-wall-grid"><span id="term-grid-presets"></span><button id="term-lock-btn"></button></span>
  <button id="term-new-shell"></button>
  <div id="term-stage"></div>
  <div id="term-min-dock"></div>
  <div id="term-bar" class="kb-collapsed"><button class="kb-toggle" id="kb-toggle"></button><div class="kb-body" id="kb-body"></div></div>
</div>
<div class="panel" id="panel-work"></div>
<div id="work-toasts"></div>`;

const agent = wid => ({ name: `agent-${wid}`, window_id: wid, online: true, session_status: 'idle' });
let liveAgents = [agent('@1'), agent('@2')];
let notReady = new Set();
let requests = [];

function fakeFetch(url) {
    const path = String(url);
    requests.push(path);
    const wid = decodeURIComponent((path.match(/agent=([^&]+)/) || [])[1] || '');
    const body = path.endsWith('/api/agents') ? liveAgents
        : path.startsWith('/api/term/ready') ? { ready: !notReady.has(wid) }
            : path.endsWith('/api/agents/context') ? {}
                : path.endsWith('/api/rooms') ? { rooms: {}, pending: [] }
                    : {};
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
}

class FakeEventSource {
    constructor(url) { this.url = url; this.listeners = {}; FakeEventSource.last = this; }
    addEventListener(type, cb) { (this.listeners[type] ||= []).push(cb); }
    close() {}
}

function frame(type, data) {
    const handlers = FakeEventSource.last.listeners[type] || [];
    assert.equal(handlers.length, 1, `the stream must listen for \`${type}\` exactly once`);
    handlers.forEach(cb => cb({ data: JSON.stringify(data) }));
}
async function settle() { for (let i = 0; i < 10; i++) await flush(); }
const toasts = () => [...document.querySelectorAll('#work-toasts .run-toast')];
const dispatcherReads = () => requests.filter(r => r.split('?')[0].endsWith('/api/dispatcher')).length;

let util, terminals, toastStore;
beforeAll(async () => {
    const grid = new Proxy({ engine: { nodes: [] }, save: () => [], getGridItems: () => [] },
        { get: (t, k) => (k in t ? t[k] : el => el) });
    globalThis.GridStack = { init: () => grid };
    ({ modules: { util, terminals } } = await bootDashboardDom({
        body: PANEL, canvasStub: true, fetchImpl: fakeFetch, EventSource: FakeEventSource,
        extraModules: ['util.js', WALL],
    }));
    ({ toasts: toastStore } = await import('../chela/dashboard/frontend/src/features/work/index.ts'));
    await settle();
    assert.ok(FakeEventSource.last, 'the boot never opened the event stream');
    assert.equal(FakeEventSource.last.url, '/api/events');
});

beforeEach(async () => {
    requests = [];
    localStorage.clear();
    util.setCurrentTab('terminals');
    toastStore.set([]);
    await settle();
});

test('a run moving INTO review toasts once, with its label and PR link, and redraws the board', async () => {
    const run = { task_id: 'CMX-901', label: 'CMX-901 · port', status: 'awaiting_review', pr_url: 'https://example.test/pull/9' };
    frame('runs', { runs: [{ ...run, status: 'running' }] });
    frame('runs', { runs: [run] });
    frame('runs', { runs: [{ ...run, pr_state: 'MERGEABLE' }] });   // same status: no second toast
    await settle();
    assert.equal(toasts().length, 1);
    const [t] = toasts();
    assert.match(t.textContent, /CMX-901 · port/);
    assert.match(t.textContent, /awaiting review/);
    assert.equal(t.querySelector('a').getAttribute('href'), 'https://example.test/pull/9');
    assert.equal(dispatcherReads(), 3, 'each runs frame refetches /api/dispatcher once');
    t.click();
    await settle();
    assert.equal(toasts().length, 0, 'a click dismisses the toast');
});

test('the hello baseline primes the last-seen status: a run already in review never toasts', async () => {
    frame('hello', { runs: [{ task_id: 'CMX-902', status: 'awaiting_review' }] });
    frame('runs', { runs: [{ task_id: 'CMX-902', status: 'awaiting_review' }] });
    await settle();
    assert.equal(toasts().length, 0);
    frame('runs', { runs: [{ task_id: 'CMX-902', status: 'done' }] });
    await settle();
    assert.equal(toasts().length, 1, 'a later transition still toasts');
    assert.equal(toasts()[0].querySelector('a'), null, 'no PR url, no link');
});

test('muted run toasts stay silent, and the board still redraws', async () => {
    localStorage.setItem('chela_mute_run_toasts', '1');
    frame('runs', { runs: [{ task_id: 'CMX-903', status: 'running' }] });
    frame('runs', { runs: [{ task_id: 'CMX-903', status: 'failed' }] });
    await settle();
    assert.equal(toasts().length, 0);
    assert.equal(dispatcherReads(), 2);
});

test('windows on the Wall: vanished panes drop, new ones are absorbed, by window id', async () => {
    util.setAgentsCache([agent('@1'), agent('@2')]);
    await terminals.renderTerminals();
    await settle();
    assert.deepEqual([...terminals.renderedWids].sort(), ['@1', '@2']);
    liveAgents = [agent('@1'), agent('@3')];
    frame('windows', { windows: ['agent-@1', 'agent-@3'] });
    await settle();
    assert.ok(requests.includes('/api/agents'), 'the Wall did not refetch the live agent set');
    assert.deepEqual([...terminals.renderedWids].sort(), ['@1', '@3']);
    assert.deepEqual(util._agentsCache.map(a => a.window_id), ['@1', '@3']);
});

test('windows off the Wall only invalidates the agent cache: no refetch, no tile change', async () => {
    util.setCurrentTab('work');
    util.setAgentsCache([agent('@1')]);
    const before = [...terminals.renderedWids];
    frame('windows', {});
    await settle();
    assert.deepEqual(util._agentsCache, []);
    assert.ok(!requests.includes('/api/agents'), 'a windows frame off the Wall refetched /api/agents');
    assert.deepEqual([...terminals.renderedWids], before);
});

test('term-ready swaps a pending placeholder for its iframe, only on the Wall', async () => {
    notReady = new Set(['@4']);
    liveAgents = [agent('@1'), agent('@4')];
    util.setAgentsCache(liveAgents);
    await terminals.renderTerminals();
    await settle();
    const pending = () => document.querySelector('#term-stage .term-pending[data-pending="@4"]');
    assert.ok(pending(), 'the fixture never rendered @4 as pending');
    util.setCurrentTab('work');
    frame('term-ready', { ready: ['@4'] });
    assert.ok(pending(), 'term-ready swapped a placeholder while the Wall was not showing');
    util.setCurrentTab('terminals');
    frame('term-ready', { ready: ['@9', '@4'] });
    assert.equal(pending(), null);
    const frames = [...document.querySelectorAll('#term-stage iframe.term-frame')].map(f => f.getAttribute('src'));
    assert.ok(frames.some(s => s.includes('/term/%404/')), `no iframe for @4: ${frames}`);
});
