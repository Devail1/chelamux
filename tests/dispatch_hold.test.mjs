// WORK TOOLBAR "Pause dispatch" CONTROL (CMX-206), in a REAL DOM.
//
// `chela dispatch --pause` is genuinely operational, but until now the only way to
// reach it was SSH + the CLI. This runs the REAL work.js (via main.js, same
// module-graph-import approach as tests/settings_update.test.mjs) against a mocked
// /api/dispatcher + pause/resume responses and asserts the DOM the button actually
// produces — not a source grep.
//
// Run: node --test tests/dispatch_hold.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `npm ci` for jsdom.)
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { JSDOM } from 'jsdom';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', 'chela', 'dashboard');
const HTML = readFileSync(join(ROOT, 'templates', 'index.html'), 'utf8');

const BODY = `
<div class="panel" id="panel-work">
  <div class="work-toolbar">
    <div class="work-seg" id="work-seg" role="group" aria-label="Work view">
      <button type="button" class="work-seg-btn" data-seg="board" aria-pressed="true">Board</button>
    </div>
    <button class="btn-accent" id="dispatch-hold-btn" style="display:none;"
            onclick="chela.toggleDispatchHold()"></button>
    <span class="work-toolbar-hint" id="dispatch-hold-hint"></span>
  </div>
  <div class="work-pane active" id="work-board" data-seg="board"></div>
  <div class="work-pane" id="work-runs" data-seg="runs"></div>
  <div class="work-pane" id="work-schedules" data-seg="schedules"></div>
</div>`;

let dispatchPayload;
let pauseResponse;
let resumeResponse;
let fetchCalls = [];

function flush() {
    return new Promise(resolve => setTimeout(resolve, 0));
}

let pollWork, toggleDispatchHold;

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
    globalThis.fetch = (url, opts) => {
        fetchCalls.push({ url: String(url), opts: opts || null });
        const u = String(url);
        let body;
        if (u.endsWith('/api/dispatcher/pause')) body = pauseResponse;
        else if (u.endsWith('/api/dispatcher/resume')) body = resumeResponse;
        else if (u.endsWith('/api/dispatcher')) body = dispatchPayload;
        else body = {};
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
    };
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;
    globalThis.TERMINALS_ENABLED = dom.window.TERMINALS_ENABLED = true;

    await import('../chela/dashboard/static/js/main.js');
    // Same cached module instance main.js already evaluated (Node's ESM cache is keyed
    // by resolved URL) — this just hands back its exports, it does not re-run work.js.
    ({ pollWork, toggleDispatchHold } = await import('../chela/dashboard/static/js/work.js'));
});

beforeEach(async () => {
    dispatchPayload = { configured: false, workflows: [], dispatch_hold: null };
    pauseResponse = { ok: true, dispatch_hold: { reason: 'dashboard', by: 'dashboard', summary: 'held by dashboard (expires in 30m)' } };
    resumeResponse = { ok: true, released: null };
    fetchCalls = [];
    globalThis.confirm = () => true;
    globalThis.alert = () => {};
    await pollWork();
});

// --- 1. 🔴 a HELD queue shows "Resume dispatch" and names why ---------------------

test('a held queue shows Resume dispatch and the hold summary', async () => {
    dispatchPayload = {
        configured: false, workflows: [],
        dispatch_hold: { reason: 'batch merge', by: 'dashboard', summary: 'held by dashboard — batch merge (expires in 30m)' },
    };
    await pollWork();

    const btn = document.getElementById('dispatch-hold-btn');
    const hint = document.getElementById('dispatch-hold-hint');
    assert.notEqual(btn.style.display, 'none', 'the button stayed hidden once a payload landed');
    assert.match(btn.textContent, /Resume dispatch/);
    assert.match(hint.textContent, /batch merge/, 'the hint never names why dispatch is paused');
});

// --- 2. 🔴 COUNTERWEIGHT — an unheld queue shows "Pause dispatch" -----------------
//
// Without this, always rendering "Resume dispatch" would also satisfy test 1.

test('an unheld queue shows Pause dispatch with no hint', async () => {
    dispatchPayload = { configured: false, workflows: [], dispatch_hold: null };
    await pollWork();

    const btn = document.getElementById('dispatch-hold-btn');
    const hint = document.getElementById('dispatch-hold-hint');
    assert.match(btn.textContent, /Pause dispatch/);
    assert.equal(hint.textContent, '');
});

// --- 3. 🔴 THE BUTTON IS WIRED TO toggleDispatchHold() -----------------------------
//
// The DOM check below reads `document`, which only ever saw the BODY fixture above —
// asserting the fixture's own copy of the onclick would pass even if the SHIPPED
// index.html never wired the button at all. So this also greps the REAL template file
// (HTML, read straight off disk — same readFileSync approach as tests/topbarmenu.test.mjs
// and tests/sidebar.test.mjs) to pin the markup that actually ships.

test('the Pause/Resume button is wired to chela.toggleDispatchHold() — not just rendered', () => {
    const btn = document.getElementById('dispatch-hold-btn');
    assert.match(btn.getAttribute('onclick') || '', /chela\.toggleDispatchHold\(\)/);
});

test('the shipped index.html actually wires #dispatch-hold-btn to chela.toggleDispatchHold()', () => {
    const m = HTML.match(/<button class="btn-accent" id="dispatch-hold-btn"[\s\S]*?<\/button>/);
    assert.ok(m, '#dispatch-hold-btn is missing from chela/dashboard/templates/index.html');
    assert.match(m[0], /onclick="chela\.toggleDispatchHold\(\)"/,
        'the shipped template\'s #dispatch-hold-btn onclick does not call chela.toggleDispatchHold()');
});

// --- 3b. 🔴 toggleDispatchHold IS REACHABLE FROM window.chela ---------------------
//
// index.html's inline onclick calls `chela.toggleDispatchHold()` against the global
// window.chela namespace — it does not import the work.js module. Every test above
// drives toggleDispatchHold via the MODULE EXPORT, so a work.js that stopped
// registering it onto window.chela would leave every test above green while a real
// click in the shipped dashboard threw "chela.toggleDispatchHold is not a function"
// (cf. tests/cost.test.mjs's window.chela.setCostWindow pin, same failure mode).

test('toggleDispatchHold is reachable via window.chela — the entry point index.html\'s onclick actually calls', () => {
    assert.equal(typeof window.chela.toggleDispatchHold, 'function',
        'window.chela.toggleDispatchHold must be registered; index.html\'s onclick="chela.toggleDispatchHold()" is the only production entry point for the button');
});

// --- 4. 🔴 PAUSING PROMPTS A CONFIRM DIALOG, AND DECLINING ABORTS BEFORE THE POST --

test('declining the confirm dialog aborts a pause before any POST', async () => {
    globalThis.confirm = () => false;
    fetchCalls = [];

    await toggleDispatchHold();

    const post = fetchCalls.find(c => c.url.endsWith('/api/dispatcher/pause'));
    assert.equal(post, undefined, 'declining confirm() still POSTed to /api/dispatcher/pause');
});

// --- 5. 🔴 CONFIRMING PAUSES — POSTs, then re-renders as held ---------------------

test('confirming pauses: POSTs to /api/dispatcher/pause and re-renders as held', async () => {
    dispatchPayload = { configured: false, workflows: [], dispatch_hold: null };
    await pollWork();
    globalThis.confirm = () => true;
    fetchCalls = [];
    // The route's own response after the POST resolves — the next poll picks this up.
    dispatchPayload = {
        configured: false, workflows: [],
        dispatch_hold: { reason: 'dashboard', by: 'dashboard', summary: 'held by dashboard (expires in 30m)' },
    };

    await toggleDispatchHold();

    const post = fetchCalls.find(c => c.url.endsWith('/api/dispatcher/pause'));
    assert.ok(post, 'toggleDispatchHold() never POSTed to /api/dispatcher/pause');
    assert.equal(post.opts && post.opts.method, 'POST');
    const btn = document.getElementById('dispatch-hold-btn');
    assert.match(btn.textContent, /Resume dispatch/, 'the button did not re-render after pausing');
});

// --- 6. 🔴 RESUMING NEVER PROMPTS A CONFIRM DIALOG --------------------------------
//
// Resume is non-destructive to in-flight work (it only lets NEW claims through again),
// unlike Pause, which is why only Pause gates on confirm().

test('resuming a held queue does not prompt confirm and POSTs to /api/dispatcher/resume', async () => {
    dispatchPayload = {
        configured: false, workflows: [],
        dispatch_hold: { reason: 'x', by: 'dashboard', summary: 'held by dashboard — x' },
    };
    await pollWork();
    let confirmCalled = false;
    globalThis.confirm = () => { confirmCalled = true; return true; };
    fetchCalls = [];
    dispatchPayload = { configured: false, workflows: [], dispatch_hold: null };

    await toggleDispatchHold();

    assert.equal(confirmCalled, false, 'resuming must not prompt a confirm dialog');
    const post = fetchCalls.find(c => c.url.endsWith('/api/dispatcher/resume'));
    assert.ok(post, 'toggleDispatchHold() never POSTed to /api/dispatcher/resume');
    const btn = document.getElementById('dispatch-hold-btn');
    assert.match(btn.textContent, /Pause dispatch/, 'the button did not re-render after resuming');
});

// --- 7. 🔴 A REFUSED PAUSE SURFACES ITS REASON, NOT A SILENT NO-OP ----------------

test('a refused pause alerts the reason', async () => {
    dispatchPayload = { configured: false, workflows: [], dispatch_hold: null };
    await pollWork();
    globalThis.confirm = () => true;
    pauseResponse = { ok: false, error: 'ttl: not a duration: "soon"' };
    let alertMsg = null;
    globalThis.alert = m => { alertMsg = m; };

    await toggleDispatchHold();

    assert.match(alertMsg || '', /not a duration/, 'the refusal reason never reached the operator');
});

// --- 8. 🔴 COUNTERWEIGHT — a SUCCESSFUL pause must never alert --------------------
//
// Without this, alerting unconditionally would also satisfy test 7.

test('a successful pause does not alert', async () => {
    dispatchPayload = { configured: false, workflows: [], dispatch_hold: null };
    await pollWork();
    globalThis.confirm = () => true;
    pauseResponse = { ok: true, dispatch_hold: { reason: 'dashboard', by: 'dashboard', summary: 'held' } };
    let alertCalled = false;
    globalThis.alert = () => { alertCalled = true; };

    await toggleDispatchHold();

    assert.equal(alertCalled, false, 'a successful pause alerted the operator');
});

// --- 5. 🔴 THE HINT ELEMENT EXISTS ON THE SHIPPED PAGE ----------------------------
//
// ⏯️ CMX-206 round 3, a WIRING finding. `renderDispatchHold` reads
// `getElementById('dispatch-hold-hint')` and every write is guarded by `if (hint)` — so
// deleting the element from index.html does not throw, does not warn, and does not fail
// a single test: the button keeps working and the operator silently loses WHO holds the
// queue, WHY, and the expiry countdown. That summary is the reason a pause button is
// safe to ship rather than alarming, and "a hold can never strand the fleet" is only
// verifiable if the countdown is on the page.
//
// ⛔ Test 3 greps the real template for the BUTTON only. The BODY fixture at the top of
// this file has its own copy of both elements, so every DOM assertion above passes
// against the fixture whether or not the shipped page carries them.

test('the shipped index.html carries #dispatch-hold-hint, not just the button', () => {
    assert.match(HTML, /id="dispatch-hold-hint"/,
        '#dispatch-hold-hint is missing from chela/dashboard/templates/index.html — '
        + 'renderDispatchHold no-ops via `if (hint)`, so the operator loses the holder, '
        + 'the reason and the expiry countdown with the suite still green');
});

// --- 6. 🔴 THE PAUSE BUTTON IS VISIBLE ON AN UNHELD QUEUE --------------------------
//
// The state an operator is in EVERY time they go to pause. Test 1 asserts
// `btn.style.display !== 'none'` for the HELD case; test 2 (its declared counterweight)
// checks only textContent and the empty hint, so the unheld half of that same assertion
// was uncovered — the button ships hidden (`style="display:none;"` in the template) and
// is revealed by JS, so "never reveal it" would have passed.

test('an unheld queue leaves the Pause button VISIBLE, not hidden', async () => {
    dispatchPayload = { configured: false, workflows: [], dispatch_hold: null };
    await pollWork();

    const btn = document.getElementById('dispatch-hold-btn');
    assert.notEqual(btn.style.display, 'none',
        'the Pause button stayed hidden on an unheld queue — the one state every pause starts from');
});

// --- 7. 🔴 THE CONTROL RE-ENABLES ITSELF, INCLUDING ON THE ERROR PATH --------------
//
// `toggleDispatchHold` disables the button, then re-enables it in a `finally` — put
// there deliberately so it survives a throwing request. Nothing asserted either half: a
// Pause that never re-enables leaves Resume unclickable and the only way back is waiting
// out the TTL. ⛔ Both arms, because a `finally` is exactly the construct a refactor
// turns back into a happy-path line.

test('the button re-enables after a SUCCESSFUL request', async () => {
    dispatchPayload = { configured: false, workflows: [], dispatch_hold: null };
    await pollWork();
    pauseResponse = { ok: true, dispatch_hold: { reason: 'x', by: 'dashboard', summary: 'held' } };

    await window.chela.toggleDispatchHold();

    assert.equal(document.getElementById('dispatch-hold-btn').disabled, false,
        'the control stayed disabled after a successful request');
});

test('the button re-enables even when the request THROWS', async () => {
    dispatchPayload = { configured: false, workflows: [], dispatch_hold: null };
    await pollWork();
    const boom = () => { throw new Error('network down'); };
    const saved = globalThis.fetch;
    globalThis.fetch = boom;
    try {
        await window.chela.toggleDispatchHold();
    } catch { /* the handler may rethrow; the finally must still have run */ }
    globalThis.fetch = saved;

    assert.equal(document.getElementById('dispatch-hold-btn').disabled, false,
        'a throwing request left the control disabled — Resume is then unclickable until the TTL expires');
});
