// SETTINGS DRAWER "Update" CONTROL (CMX-199), in a REAL DOM.
//
// The judge corrupted `nav.js`'s `if (behind > 0)` guard to `if (false && behind > 0)`
// and the pytest suite stayed green — nothing in `tests/` exercises `renderSettings`,
// `_loadSettingsStatus`, or `#update-apply-btn`, so the entire operator-facing half of
// CMX-199 (the button that actually appears/enables when the checkout falls behind)
// was unguarded ground. This runs the REAL nav.js (via main.js, same module-graph-import
// approach as tests/topbarmenu.test.mjs) against a mocked /api/settings response and
// asserts the DOM `_renderUpdateStatus` actually produces — not a source grep.
//
// Two properties:
//
//   1. 🔴 BEHIND > 0 ENABLES THE BUTTON AND ANNOUNCES THE COUNT. Drop the `behind > 0`
//      branch (or falsify its condition) and this goes red — the button stays disabled
//      and the badge never says "N behind" even though the checkout is behind.
//   2. 🔴 COUNTERWEIGHT — up to date leaves the button disabled. Without this, "always
//      enable the button" would also satisfy property 1.
//
// Run: node --test tests/settings_update.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `npm ci` for jsdom — CHELA_REQUIRE_JS_TESTS makes a
// missing jsdom a FAILURE.)
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';   // needs `npm ci` — tests/test_js_suites.py enforces it

const BODY = `
<div class="drawer-scrim" id="drawer-scrim" onclick="chela.toggleSettings()"></div>
<aside class="drawer" id="settings-drawer">
  <div class="drawer-body" id="drawer-body"></div>
</aside>`;

let updatePayload;
let applyPayload = {};
let fetchCalls = [];
let settingsShouldFail = false;

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
    globalThis.fetch = (url, opts) => {
        fetchCalls.push({ url: String(url), opts: opts || null });
        if (String(url).endsWith('/api/settings') && settingsShouldFail) {
            return Promise.reject(new Error('network down'));
        }
        const body = String(url).endsWith('/api/settings')
            ? { sections: [], update: updatePayload }
            : applyPayload;
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
    };
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;

    globalThis.TERMINALS_ENABLED = dom.window.TERMINALS_ENABLED = true;
    await import('../chela/dashboard/static/js/main.js');
});

beforeEach(() => {
    // Each test opens the drawer fresh — toggleSettings() only calls renderSettings()
    // when it transitions closed→open, so start from a known-closed state.
    document.getElementById('settings-drawer').classList.remove('open');
    applyPayload = {};
    fetchCalls = [];
    settingsShouldFail = false;
});

async function openWith(update) {
    updatePayload = update;
    window.chela.toggleSettings();
    await flush();
}

// --- 1. 🔴 behind > 0 enables the button and announces the count ------------------

test('a checkout that is behind enables the Update button and shows the count', async () => {
    await openWith({ ok: true, behind: 3, ahead: 0, branch: 'dev' });

    const btn = document.getElementById('update-apply-btn');
    const row = document.getElementById('update-status-row');
    assert.equal(btn.disabled, false, 'the Update button stayed disabled while behind > 0');
    assert.match(row.textContent, /3 behind/, 'the status row does not announce the behind count');
    assert.match(row.textContent, /dev/, 'the status row does not name the branch');
});

// --- 2. 🔴 counterweight — up to date leaves the button disabled ------------------

test('a checkout that is up to date leaves the Update button disabled', async () => {
    await openWith({ ok: true, behind: 0, ahead: 0, branch: 'dev' });

    const btn = document.getElementById('update-apply-btn');
    const row = document.getElementById('update-status-row');
    assert.equal(btn.disabled, true, 'the Update button is enabled with nothing to pull');
    assert.match(row.textContent, /Up to date/, 'the status row does not say "Up to date"');
});

test('an unreadable checkout (git=false) reports Unknown and keeps the button disabled', async () => {
    await openWith({ ok: false, git: false, error: 'not a git checkout' });

    const btn = document.getElementById('update-apply-btn');
    const row = document.getElementById('update-status-row');
    assert.equal(btn.disabled, true);
    assert.match(row.textContent, /Unknown/);
});


// --- 3. 🔴 THE BUTTON IS WIRED TO applyUpdate() ------------------------------------
//
// The judge corrupted `onclick="chela.applyUpdate()"` to `onclick="void 0"` and the whole
// suite stayed green: every assertion above reads `btn.disabled` and `row.textContent` —
// how the control LOOKS, never that it DOES anything. An unwired button renders perfectly.
// Same shape as tests/shortcuts.test.mjs:90, which pins its overlay's onclick attribute.

test('the Update button is wired to applyUpdate() — not just rendered', async () => {
    await openWith({ ok: true, behind: 3, ahead: 0, branch: 'dev' });

    const btn = document.getElementById('update-apply-btn');
    assert.match(btn.getAttribute('onclick') || '', /chela\.applyUpdate\(\)/,
        'the Update button renders but is not wired to chela.applyUpdate()');
});

// --- 4. 🔴 A REFUSAL REACHES THE OPERATOR, NAMING WHY ------------------------------
//
// The backend guard asserts the 409 body NAMES the in-flight dispatched task ids
// (tests/test_update_apply_route.py::test_apply_refuses_while_a_dispatched_run_is_in_flight).
// That is worth nothing if the drawer swallows it: the judge corrupted
// `setMsg('err', (resp && resp.error) || 'Update refused.')` to `setMsg('err', '')` and the
// suite stayed green. This drives the real applyUpdate() and asserts the reason lands in
// the DOM — and, in passing, that the POST goes where it claims to.

test('a refused update surfaces the reason, naming the in-flight task', async () => {
    await openWith({ ok: true, behind: 3, ahead: 0, branch: 'dev' });
    globalThis.confirm = () => true;
    applyPayload = { ok: false, error: 'a dispatched run is in flight: cmx-199-abc12' };

    await window.chela.applyUpdate();
    await flush();

    const msg = document.getElementById('update-apply-msg');
    assert.match(msg.textContent, /cmx-199-abc12/,
        'the refusal reason never reached the operator — the drawer swallowed it');
    const post = fetchCalls.find(c => c.url.endsWith('/api/update/apply'));
    assert.ok(post, 'applyUpdate() never POSTed to /api/update/apply');
    assert.equal(post.opts && post.opts.method, 'POST');
    const btn = document.getElementById('update-apply-btn');
    assert.equal(btn.disabled, false, 'a refused update must re-enable the button to retry');
});

// --- 5. 🔴 COUNTERWEIGHT — a STARTED update must not render as a refusal ------------
//
// Without this, `setMsg('err', ...)` on every path would satisfy the test above.

test('a started update reports success, not a refusal', async () => {
    await openWith({ ok: true, behind: 3, ahead: 0, branch: 'dev' });
    globalThis.confirm = () => true;
    applyPayload = { ok: true, started: true };

    await window.chela.applyUpdate();
    await flush();

    const msg = document.getElementById('update-apply-msg');
    assert.match(msg.textContent, /Started/, 'a started update did not report as started');
    assert.doesNotMatch(msg.textContent, /refused/i);
    // ⛔ The TEXT alone is not the property: setMsg carries the outcome in the class, so
    // `setMsg('ok', …)` -> `setMsg('err', …)` leaves this message word-for-word identical
    // while styling a successful update as a failure. Assert the class, or that mutation
    // survives (it did, the first time I wrote this test).
    assert.match(msg.className, /\bok\b/, 'a started update is styled as an error');
    assert.doesNotMatch(msg.className, /\berr\b/);
});

// --- 6. 🔴 A NOT-STARTED response must never be announced as a fleet-wide restart ---
//
// Judge round 5 (PR #260) corrupted `if (!resp.started) {` to `if (false && !resp.started) {`
// and the suite stayed green: nothing drives applyUpdate() with `started: false` (the
// backend's "already up to date" arm — see test_apply_refuses_when_already_up_to_date).
// Falling through to the started-branch text would tell the operator a restart is underway
// when nothing was ever kicked off.

test('a not-started response reports its detail, not a started-restart message', async () => {
    await openWith({ ok: true, behind: 3, ahead: 0, branch: 'dev' });
    globalThis.confirm = () => true;
    applyPayload = { ok: true, started: false, detail: 'already up to date' };

    await window.chela.applyUpdate();
    await flush();

    const msg = document.getElementById('update-apply-msg');
    assert.match(msg.textContent, /already up to date/i,
        'a not-started response never surfaced its detail');
    assert.doesNotMatch(msg.textContent, /Started/,
        'a not-started response was announced as a fleet-wide restart');
});

// --- 7. 🔴 DECLINING THE CONFIRM DIALOG MUST ABORT BEFORE THE POST -----------------
//
// Judge round 6 (PR #260) corrupted the `if (!confirm(...)) return;` gate to
// `if (false && !confirm(...)) return;` and the suite stayed green: both existing
// applyUpdate() tests stub `globalThis.confirm = () => true`, so the gate is assumed and
// never exercised in its refusing direction. A fleet-wide restart must not fire from a
// single accidental click.

test('declining the confirm dialog aborts before any POST', async () => {
    await openWith({ ok: true, behind: 3, ahead: 0, branch: 'dev' });
    globalThis.confirm = () => false;

    await window.chela.applyUpdate();
    await flush();

    const post = fetchCalls.find(c => c.url.endsWith('/api/update/apply'));
    assert.equal(post, undefined, 'declining confirm() still POSTed to /api/update/apply');
});

// --- 8. 🔴 A FAILED /api/settings POLL MUST RE-RENDER THE ROW AS UNKNOWN -----------
//
// Judge round 5 (PR #260) corrupted the catch arm's `_renderUpdateStatus(null);` to
// `void 0;` and the suite stayed green: nothing ever made the mocked `/api/settings` fetch
// reject, so the row was left sitting on its initial "Checking…" badge forever instead of
// being told the poll failed.

test('a failed /api/settings poll re-renders the Update row as Unknown', async () => {
    settingsShouldFail = true;
    updatePayload = { ok: true, behind: 3, ahead: 0, branch: 'dev' };
    window.chela.toggleSettings();
    await flush();

    const btn = document.getElementById('update-apply-btn');
    const row = document.getElementById('update-status-row');
    assert.match(row.textContent, /Unknown/,
        'a failed /api/settings poll left the row on its stale/initial state');
    assert.equal(btn.disabled, true);
});
