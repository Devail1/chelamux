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
import { JSDOM } from 'jsdom';

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

test('the Pause/Resume button is wired to chela.toggleDispatchHold() — not just rendered', () => {
    const btn = document.getElementById('dispatch-hold-btn');
    assert.match(btn.getAttribute('onclick') || '', /chela\.toggleDispatchHold\(\)/);
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
