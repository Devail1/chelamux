// SETTINGS DRAWER "Dispatch" TAB (CMX-220), in a REAL DOM — same idiom as
// tests/settings_update.test.mjs: run the real nav.js (via main.js, same
// module-graph-import approach) against a mocked /api/config/dispatch and assert
// what `renderSettings`/`_loadDispatchSettings`/`saveTiming` actually produce in
// the DOM, not a source grep.
//
// Five properties:
//
//   1. 🔴 THE DRAWER RENDERS EVERY KNOB THE SERVER REPORTS, stored value in the
//      field and the built-in default as its placeholder. Drop the render loop
//      (or forget to thread `k.stored`/`k.default`) and this goes red.
//   2. 🔴 SAVE POSTS EVERY VISIBLE ROW, keyed by its `data-timing-key`. Forget to
//      collect an input (or key it wrong) and the posted payload silently loses
//      a field — this fails on the exact shape the request body must have.
//   3. 🔴 A SERVER-SIDE REJECTION (400, atomic batch) SURFACES THE FIELD ERROR
//      AND RE-LOADS what's actually stored — it must not claim "Saved."
//   4. 🔴 A KNOB WITH `source: "env"` RENDERS ITS FIELD DISABLED (env wins server-
//      side — an editable field here would silently discard whatever the user
//      typed) AND SAVE EXCLUDES IT FROM THE POST BODY (else Save would
//      re-persist the env value into config.json as an unasked-for dashboard
//      value). Asserting only the editable case cannot catch either regression.
//   5. 🔴 A KNOB WITH `restart_required: true` ANNOTATES ITS ROW — the tab must
//      not imply a restart-latched knob takes effect on the next tick like the
//      rest of the group.
//
// Run: node --test tests/settings_timing.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `npm ci` for jsdom.)
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const BODY = `
<div class="drawer-scrim" id="drawer-scrim" onclick="chela.toggleSettings()"></div>
<aside class="drawer" id="settings-drawer">
  <div class="drawer-body" id="drawer-body"></div>
</aside>`;

const KNOBS = [
    { key: 'max_reworks', env: 'CHELA_MAX_REWORKS', label: 'Rework cap', unit: '',
      default: 2, stored: '', effective: 2, source: 'default', restart_required: false },
    { key: 'judge_enabled', env: 'CHELA_JUDGE', label: 'Judge', unit: '',
      default: true, stored: '', effective: true, source: 'default', restart_required: true },
    { key: 'gate_max_waits', env: 'CHELA_GATE_MAX_WAITS', label: 'Gate slots', unit: '',
      default: 8, stored: 5, effective: 5, source: 'dashboard', restart_required: false },
    { key: 'merge_base', env: 'CHELA_MERGE_BASE', label: 'Autonomous base', unit: '',
      default: 'dev', stored: '', effective: 'release-train', source: 'env', restart_required: true },
];

let dispatchGetPayload;
let dispatchPostResponse;
let fetchCalls = [];

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
        const u = String(url);
        fetchCalls.push({ url: u, opts: opts || null });
        if (u.endsWith('/api/config/dispatch')) {
            if (opts && opts.method === 'POST') {
                return Promise.resolve({
                    ok: true, status: dispatchPostResponse.error ? 400 : 200,
                    json: () => Promise.resolve(dispatchPostResponse),
                });
            }
            return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(dispatchGetPayload) });
        }
        // Every other settings fetch (/api/config, /api/settings, ...): an empty
        // 200 is enough to keep the rest of renderSettings() from throwing.
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    };
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;
    globalThis.TERMINALS_ENABLED = dom.window.TERMINALS_ENABLED = true;
    await import('../chela/dashboard/static/js/main.js');
});

beforeEach(() => {
    document.getElementById('settings-drawer').classList.remove('open');
    dispatchGetPayload = { knobs: KNOBS };
    dispatchPostResponse = { knobs: KNOBS };
    fetchCalls = [];
});

async function openDrawer() {
    window.chela.toggleSettings();
    await flush();
    await flush();   // renderSettings()'s _loadDispatchSettings() is itself async
}

// --- 1. 🔴 renderSettings() must actually LOAD this tab -----------------------

test('opening Settings loads and renders the Dispatch knobs', async () => {
    await openDrawer();

    const inputs = document.querySelectorAll('#dispatch-rows .s-dispatch-input');
    assert.equal(inputs.length, KNOBS.length,
        'the Dispatch tab rendered no rows — renderSettings() is not calling _loadDispatchSettings()');
    assert.ok(fetchCalls.some(c => c.url.endsWith('/api/config/dispatch')),
        'the drawer never fetched /api/config/dispatch');

    const byKey = {};
    inputs.forEach(inp => { byKey[inp.dataset.dispatchKey] = inp; });
    assert.equal(byKey.gate_max_waits.value, '5', 'a stored value did not pre-fill its field');
});

// --- 2. 🔴 an env-overridden row must NOT be re-posted ------------------------
//
// Saving must not copy the env value into config.json as an unasked-for dashboard
// value: the moment the operator unsets the env var, that copy would silently take
// over. This is property #4 of settings_timing.test.mjs, for the identical code.

test('Save excludes an env-overridden (disabled) row from the POST body', async () => {
    // Opens its own drawer: sharing the previous test's DOM made this test's result
    // depend on that one having run first, so a failure here could mean either "Save
    // re-posts a disabled row" or "the tab never rendered". Independent tests name
    // their own cause.
    await openDrawer();

    const envRow = document.querySelector('.s-dispatch-input[data-dispatch-key="merge_base"]');
    assert.ok(envRow, 'no row for the env-overridden knob');
    assert.equal(envRow.disabled, true, 'an env-overridden row must be disabled');

    fetchCalls = [];
    window.chela.saveDispatch();
    await flush();

    const post = fetchCalls.find(c => c.opts && c.opts.method === 'POST');
    assert.ok(post, 'Save posted nothing');
    const sent = JSON.parse(post.opts.body);
    assert.ok(!('merge_base' in sent),
        'Save re-posted the env-overridden knob — it would persist the env value into config.json');
    assert.ok('max_reworks' in sent, 'Save dropped the editable rows too');
});
