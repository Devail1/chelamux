// CMX-279 rework round 1 (PR #350) — closes a gap the judge found completely
// unguarded (DEFEAT_SHAPES shape 9, "a behavior-changing fix shipped with no
// guard at all"): three separate literals encode "the default canvas, on a
// terminals-off deployment, is Work — not the now-deleted Agents view":
//
//   1. util.js's `let currentTab = 'work';` (its module-load default — nothing
//      sets it when TERMINALS_ON is false, since main.js's own
//      `selectView('terminals')` call is TERMINALS_ON-gated);
//   2. index.html's `<div class="panel active" id="panel-work">` — the ONLY
//      thing that marks a panel visible when terminals are off, since nothing
//      in JS ever calls selectView('work') to add the class itself;
//   3. nav.js's `_agentDetailBackView()` fallback, which must resolve to
//      'work' (not the deleted 'agents') so the agent-detail "← Back" link
//      routes somewhere real.
//
// None of these had ANY test reading back the actual rendered/runtime value —
// the judge reverted each to its old 'agents'-shaped literal, one at a time, in
// a throwaway checkout, and all 3059 tests stayed green. This boots the REAL
// main.js module graph (main.js <-> nav.js is a cycle — see tests/sidebar.test.mjs's
// own note) with window.TERMINALS_ENABLED explicitly false — a genuine
// terminals-off deployment, not just "unset" (util.js: `!== false`, so leaving
// it unset would NOT reproduce terminals-off) — and reads every claim back off
// the actually running program: the live `currentTab` binding, the real DOM
// node's classList (mounted from index.html's OWN markup, not a hand copy),
// and the real renderKanban() paint that only happens once the currentTab gate
// (work.js:174) lets pollWork() through.
//
// Run: node --test tests/dashboard_default_view.test.mjs (pytest via
// tests/test_js_suites.py; needs `npm ci` for jsdom).
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { JSDOM } from 'jsdom';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', 'chela', 'dashboard');
const HTML = readFileSync(join(ROOT, 'templates', 'index.html'), 'utf8');

// The REAL agent-detail + Work panels, sliced straight out of index.html — not
// hand-typed — so a mutation to panel-work's pre-set `active` class (exactly
// the mutation this round's verdict named) shows up here, not just in a
// fixture that happens to agree with the file today.
const CANVAS_START = HTML.indexOf('<div class="panel" id="panel-agent-detail">');
const CANVAS_END = HTML.indexOf('<!-- /panel-work -->') + '<!-- /panel-work -->'.length;
if (CANVAS_START < 0 || CANVAS_END < 0) throw new Error('index.html markers for panel-agent-detail/panel-work moved — update this test');
const CANVAS_HTML = HTML.slice(CANVAS_START, CANVAS_END);

const BODY = `
<div class="app">
  <aside class="sidebar">
    <section class="side-section">
      <div class="side-list" id="side-nav"></div>
    </section>
    <section class="side-section">
      <span class="side-count" id="hdr-agents">-/-</span>
      <div class="side-list" id="sidebar-agents"><div class="side-empty">No agents</div></div>
    </section>
  </aside>
  <main class="canvas" id="canvas">
    ${CANVAS_HTML}
  </main>
</div>`;

let util, nav;

function flush() {
    return new Promise(resolve => setTimeout(resolve, 0));
}

before(async () => {
    const dom = new JSDOM(`<!doctype html><html><body>${BODY}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
    for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement',
        'Element', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'CustomEvent',
        'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
        Object.defineProperty(globalThis, k, {
            value: dom.window[k], writable: true, configurable: true,
        });
    }
    dom.window.matchMedia = q => ({
        media: q, matches: false,
        addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
    });
    dom.window.HTMLCanvasElement.prototype.getContext = () => new Proxy({}, {
        get: (_t, k) => (k === 'canvas' ? null : () => {}),
    });
    dom.window.HTMLCanvasElement.prototype.toDataURL = () => 'data:image/png;base64,';
    // A blanket {} for everything EXCEPT /api/agents/context, which must resolve
    // an array (checkContext's `for (const a of data)` — see sidebar.test.mjs's
    // identical note on the same call, fired unawaited from main.js's own
    // top-level refresh()).
    globalThis.fetch = url => {
        const body = String(url).includes('/api/agents/context') ? [] : {};
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
    };
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;

    // The genuine terminals-off boot: EXPLICITLY false, not merely unset — see
    // util.js's own `window.TERMINALS_ENABLED !== false`, which defaults TRUE
    // when the bootstrap script is absent (index.html only ever emits `= true`,
    // never `= false`; that asymmetry is pre-existing and untouched by CMX-279,
    // out of scope here). This is the one value that actually drives TERMINALS_ON
    // to false in JS, which is the terminals-off behaviour this file guards.
    globalThis.TERMINALS_ENABLED = dom.window.TERMINALS_ENABLED = false;

    await import('../chela/dashboard/static/js/main.js');
    util = await import('../chela/dashboard/static/js/util.js');
    nav = await import('../chela/dashboard/static/js/nav.js');

    // Flush pollWork()'s fetch -> json() -> render microtask chain (startWorkPoll
    // fires pollWork() immediately on boot — main.js:60) without advancing any
    // fake timer.
    await flush();
    await flush();

    // showAgentDetail (driven below via chela.selectAgent) fires an unawaited
    // refreshSummary(), which reaches for these two header nodes — absent from
    // this file's minimal BODY (see sidebar.test.mjs's identical note on the
    // same call). Without them the write lands on `null` in a microtask AFTER
    // the test that triggered it has already returned, surfacing as an
    // unhandledRejection against the whole file rather than that test.
    if (!document.getElementById('hdr-next')) document.body.appendChild(document.createElement('span')).id = 'hdr-next';
    if (!document.getElementById('hdr-updated')) document.body.appendChild(document.createElement('span')).id = 'hdr-updated';
});

test('currentTab defaults to work on boot when terminals are off — never the deleted agents view', () => {
    // The live binding main.js/work.js actually read (main.js:41, work.js:174),
    // not a re-parse of util.js's source text: nothing in this boot calls
    // selectView('terminals') (TERMINALS_ON is false), so whatever value is here
    // is util.js's OWN initial default — exactly the literal the judge reverted.
    assert.equal(util.currentTab, 'work',
        "currentTab is not 'work' after a terminals-off boot — util.js's `let currentTab = 'work';` " +
        "default reverted (or something now overwrites it before the first paint)");
});

test('panel-work is the panel that is actually active after a terminals-off boot', () => {
    const panel = document.getElementById('panel-work');
    assert.ok(panel, 'panel-work is missing from the mounted canvas');
    assert.ok(panel.classList.contains('active'),
        "panel-work lost its pre-set 'active' class (index.html) and nothing in JS ever adds it back " +
        "when terminals are off (selectView('work') is never called on this boot path) — the canvas would load blank");
});

test('the currentTab gate actually lets the Work board paint — not just a value that happens to read "work"', () => {
    // work.js:174 (`if (currentTab !== 'work') return;`) is the load-bearing
    // consumer of the literal above. Proven behaviourally: renderKanban() only
    // flips #kanban-empty to 'block' (its not-configured state, matching the {}
    // /api/dispatcher stub) if pollWork()'s early return was NOT taken. If
    // currentTab defaulted to 'agents' instead, this assertion is the one that
    // would catch it even if the direct currentTab check above were somehow
    // gamed.
    const empty = document.getElementById('kanban-empty');
    assert.ok(empty, '#kanban-empty is missing from the mounted panel-work markup');
    assert.equal(empty.style.display, 'block',
        'renderKanban() never ran — the Work board never painted on a terminals-off boot, which means the ' +
        "currentTab gate at work.js:174 did not let it through (currentTab did not actually resolve to 'work')");
});

// --- the agent-detail "← Back" link, both renderAgentDetail call sites -------
//
// nav.js's _agentDetailBackView() falls back to 'work' when TERMINALS_ON is
// false (the only branch reachable in THIS file's boot). Both call sites are
// driven — the not-found branch (nav.js:560, an agent absent from
// _agentsCache) and the normal/found branch (nav.js:608) — per DEFEAT_SHAPES
// shape 7 ("two callers, one guarded"): a fixture that only ever drives one
// would miss a corruption at the other.
function _invokeOnclick(el, chelaStub) {
    const handler = new Function('chela', el.getAttribute('onclick') || '');
    handler.call(el, chelaStub);
}

function _assertBackLinkTargetsWork(label) {
    const back = document.querySelector('#agent-detail .detail-back');
    assert.ok(back, `${label}: no .detail-back node rendered into #agent-detail`);
    assert.match(back.getAttribute('onclick'), /chela\.selectView\('work'\)/,
        `${label}: the "← Back" link is not wired to chela.selectView('work') — it points at a view ` +
        "that no longer exists (or was never re-pointed at the new default) on a terminals-off deployment");
    const calls = [];
    _invokeOnclick(back, { selectView: (...args) => calls.push(args) });
    assert.deepEqual(calls, [['work']],
        `${label}: the "← Back" link's onclick did not actually CALL chela.selectView('work')`);
}

test('agent-detail "← Back" (not-found branch) returns to Work, not the deleted Agents view', () => {
    nav.renderNav();
    window.chela.selectAgent('cmx279-rework-ghost-agent');   // unresolved -> not-found branch (nav.js:560)
    _assertBackLinkTargetsWork('not-found branch');
});

test('agent-detail "← Back" (found branch) also returns to Work, not the deleted Agents view', () => {
    nav.renderNav();
    // A resolvable agent with no window_id: selectAgent's wall-focus branch
    // (TERMINALS_ON && a.window_id) never applies even if TERMINALS_ON flips —
    // this always falls through to showAgentDetail's FOUND path (nav.js:608),
    // the other of the two call sites.
    util.setAgentsCache([{ name: 'cmx279-rework-known-agent', online: true }]);
    window.chela.selectAgent('cmx279-rework-known-agent');
    _assertBackLinkTargetsWork('found branch');
});
