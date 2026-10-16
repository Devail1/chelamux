// CMX-160 rewrote the copy on dashboard lines that sit behind a condition no test had
// covered. These pin each condition, so the rewrite can't hide a dropped branch:
//   1. agents.js: a context reading that is a transcript ESTIMATE gets the
//      `chela install-statusline` hint in its tooltip, and an exact reading does not.
//   2. terminals.js: the same split on a wall tile's context bar tooltip.
//   3. dispatcher.js: seeding a path that is NOT a git repo warns that the dispatcher
//      needs one, and seeding a git repo does not.
//   4. nav.js: an archive/unarchive the dashboard never answered alerts, an answered one
//      does not, and a page without `alert` fails quietly instead of throwing.
//
// The page boots the real main.js module graph, so the skeleton, GridStack and the timers
// are stubbed (harness from window_id.test.mjs).
//
// Run: node --test tests/em_dash_rewrite_branches.test.mjs (tests/test_js_suites.py runs it).
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';   // needs `pnpm install`; tests/test_js_suites.py enforces it

let CONTEXT = [];
let INIT = {};
let ARCHIVE_DOWN = false;

const AGENTS = [{ name: 'a1', window_id: '@3', online: true, cwd: '/p/a1' }];

function fakeFetch(url) {
    const path = String(url);
    if (ARCHIVE_DOWN && /\/api\/sidebar\/(un)?archive$/.test(path)) return Promise.reject(new TypeError('Failed to fetch'));
    const body = path.endsWith('/api/agents/context') ? CONTEXT
        : path.endsWith('/api/dispatcher/init') ? INIT
            : path.endsWith('/api/agents') ? AGENTS
                : path.endsWith('/api/rooms') ? { rooms: {}, pending: [] }
                    : path.startsWith('/api/term/ready') ? { ready: true }
                        : path.endsWith('/api/orchestrator/status') ? { wid: null, name: null, state: 'unregistered', why: '', queued: 0 }
                            : path.includes('/api/log') ? { boot_id: 'b1', gap: null, first_seq: 0, last_seq: 0, next_seq: 0, events: [] }
                                : {};
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
}

const PAGE = `
<div id="sidebar-agents"></div>
<div class="panel" id="panel-terminals">
  <button id="term-mode-single"></button><button id="term-mode-wall"></button>
  <select id="term-agent"></select>
  <span id="term-wall-grid"><span id="term-grid-presets"></span><button id="term-lock-btn"></button></span>
  <button id="term-new-shell"></button><div id="term-switcher"></div><div id="term-stage"></div>
  <div id="term-min-dock"></div>
  <div id="term-bar" class="kb-collapsed"><button class="kb-toggle" id="kb-toggle"></button><div class="kb-body" id="kb-body"></div></div>
</div>
<div id="ctx-a1"><div class="context-bar-fill"></div><span class="context-label"></span></div>
<input id="init-path" value="/srv/repo"><pre id="init-result"></pre>`;

function fakeGridStack() {
    const grid = {
        on() {}, off() {}, save: () => [], destroy() {}, removeWidget(el) { el.remove(); },
        addWidget: el => el, makeWidget: el => el, enableMove() {}, enableResize() {},
        update() {}, batchUpdate() {}, commit() {}, cellHeight() {}, column() {},
        getGridItems: () => [], removeAll() {}, float() {}, engine: { nodes: [] },
    };
    return { init: () => grid };
}

let agents, nav, terminals, util;

before(async () => {
    const dom = new JSDOM(`<!doctype html><html><body>${PAGE}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
    dom.window.TERMINALS_ENABLED = true;
    for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement',
        'Element', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'CustomEvent',
        'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
        Object.defineProperty(globalThis, k, { value: dom.window[k], writable: true, configurable: true });
    }
    dom.window.HTMLElement.prototype.scrollIntoView = function () {};
    globalThis.GridStack = fakeGridStack();
    globalThis.fetch = fakeFetch;
    dom.window.matchMedia = q => ({
        media: q, matches: false, addEventListener() {}, removeEventListener() {},
        addListener() {}, removeListener() {},
    });
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;
    await import('../chela/dashboard/static/js/main.js');
    util = await import('../chela/dashboard/static/js/util.js');
    nav = await import('../chela/dashboard/static/js/nav.js');
    terminals = await import('../chela/dashboard/static/js/terminals.js');
    agents = await import('../chela/dashboard/static/js/agents.js');
    await import('../chela/dashboard/static/js/dispatcher.js');
    util.setCurrentTab('terminals');
    util.setAgentsCache(AGENTS);
});

const settle = async () => { for (let i = 0; i < 8; i++) await new Promise(r => setTimeout(r, 0)); };

// --- 1 + 2. the context tooltip's estimate hint ------------------------------------

const reading = over => ({ name: 'a1', window_id: '@3', used: '50k', total: '200k', used_pct: 25,
    session_name: 'cmx-1', ...over });
const label = () => document.querySelector('#ctx-a1 .context-label');

test('an estimated context reading carries the install-statusline hint in its tooltip', async () => {
    CONTEXT = [reading({ estimated: true })];
    await agents.checkContext();
    assert.equal(label().title,
        'cmx-1 · estimate from transcript; run `chela install-statusline` for exact context %');
});

test('an exact context reading has no estimate hint', async () => {
    CONTEXT = [reading({ estimated: false })];
    await agents.checkContext();
    assert.equal(label().title, 'cmx-1');
});

const tileBar = () => document.querySelector('#term-stage .term-ctx-bar[data-ctx-for="@3"]');

test('a wall tile with an estimated reading says so in its context tooltip', async () => {
    CONTEXT = [reading({ estimated: true })];
    await terminals.renderTerminals();
    await settle();
    assert.ok(tileBar(), 'the wall rendered no context bar for @3');
    assert.equal(tileBar().title, 'Context: 50k/200k (25%~) · estimate; run `chela install-statusline` for exact');
});

test('a wall tile with an exact reading has no estimate hint', async () => {
    CONTEXT = [reading({ estimated: false })];
    await terminals.termTick();     // the poll re-reads /api/agents/context into the bar
    await settle();
    assert.equal(tileBar().title, 'Context: 50k/200k (25%)');
});

// --- 3. seeding a repo -------------------------------------------------------------

const result = () => document.getElementById('init-result').textContent;

test('seeding a path that is not a git repo warns that the dispatcher needs one', async () => {
    INIT = { ok: true, path: '/srv/repo', created: ['WORKFLOW.md'], skipped: [], is_git: false };
    await window.chela.doInitRepo();
    assert.match(result(), /^⚠ Not a git repo\. The dispatcher needs one to branch & open PRs\.$/m);
});

test('seeding a git repo shows no git warning', async () => {
    INIT = { ok: true, path: '/srv/repo', created: ['WORKFLOW.md'], skipped: [], is_git: true };
    await window.chela.doInitRepo();
    assert.match(result(), /^✓ Created WORKFLOW\.md$/m);
    assert.doesNotMatch(result(), /Not a git repo/);
});

// --- 4. archive / unarchive with no answer -----------------------------------------

let ALERTS = [];
beforeEach(() => { ALERTS = []; ARCHIVE_DOWN = false; globalThis.alert = msg => { ALERTS.push(msg); }; });

for (const [fn, text] of [['archiveKeys', 'Archive failed. The dashboard did not answer.'],
    ['unarchiveKeys', 'Unarchive failed. The dashboard did not answer.']]) {
    test(`${fn}: no answer from the dashboard alerts "${text}"`, async () => {
        ARCHIVE_DOWN = true;
        assert.equal(await nav[fn](['s:abc']), null);
        assert.deepEqual(ALERTS, [text]);
    });

    test(`${fn}: an answered request does not alert`, async () => {
        const res = await nav[fn](['s:abc']);
        assert.ok(res, 'the fake dashboard answered, so the result is not null');
        assert.deepEqual(ALERTS, []);
    });

    test(`${fn}: a page without alert() fails quietly instead of throwing`, async () => {
        ARCHIVE_DOWN = true;
        delete globalThis.alert;
        assert.equal(await nav[fn](['s:abc']), null);
    });
}
