// SETTINGS DRAWER "Timing" TAB (CMX-217), in a REAL DOM — same idiom as
// tests/settings_update.test.mjs: run the real nav.js (via main.js, same
// module-graph-import approach) against a mocked /api/config/timing and assert
// what `renderSettings`/`_loadTimingSettings`/`saveTiming` actually produce in
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
    { key: 'scheduler_poll_interval_seconds', env: 'CHELA_SCHEDULER_POLL_INTERVAL',
      label: 'Daemon tick', unit: 's', default: 30, stored: '', effective: 30,
      source: 'default', restart_required: false },
    { key: 'capture_interval_seconds', env: 'CHELA_CAPTURE_INTERVAL_SECONDS',
      label: 'Context-snapshot capture cadence', unit: 's', default: 300, stored: '', effective: 300,
      source: 'default', restart_required: false },
    { key: 'doctor_check_interval_seconds', env: 'CHELA_DOCTOR_CHECK_INTERVAL',
      label: 'Doctor self-audit cadence', unit: 's', default: 3600, stored: 1800, effective: 1800,
      source: 'dashboard', restart_required: false },
    { key: 'status_cmd_timeout_seconds', env: 'CHELA_STATUS_CMD_TIMEOUT_S',
      label: 'Status-feed subprocess timeout', unit: 's', default: 45.0, stored: '', effective: 60.0,
      source: 'env', restart_required: true },
];

let timingGetPayload;
let timingPostResponse;
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
        if (u.endsWith('/api/config/timing')) {
            if (opts && opts.method === 'POST') {
                return Promise.resolve({
                    ok: true, status: timingPostResponse.error ? 400 : 200,
                    json: () => Promise.resolve(timingPostResponse),
                });
            }
            return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(timingGetPayload) });
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
    timingGetPayload = { knobs: KNOBS };
    timingPostResponse = { knobs: KNOBS };
    fetchCalls = [];
});

async function openDrawer() {
    window.chela.toggleSettings();
    await flush();
    await flush();   // renderSettings()'s _loadTimingSettings() is itself async
}

// --- 1. 🔴 every knob renders, stored value in field, default as placeholder ---

test('opening Settings renders one row per knob with stored value + default placeholder', async () => {
    await openDrawer();

    const inputs = document.querySelectorAll('#timing-rows .s-timing-input');
    assert.equal(inputs.length, KNOBS.length, 'not every reported knob got a row');

    const byKey = {};
    inputs.forEach(inp => { byKey[inp.dataset.timingKey] = inp; });

    assert.equal(byKey.scheduler_poll_interval_seconds.value, '');
    assert.equal(byKey.scheduler_poll_interval_seconds.placeholder, '30');
    assert.equal(byKey.doctor_check_interval_seconds.value, '1800',
        'a knob with a stored value did not pre-fill the field');
    assert.equal(byKey.doctor_check_interval_seconds.placeholder, '3600');

    const row = document.getElementById('timing-rows');
    assert.match(row.textContent, /Daemon tick/);
    assert.match(row.textContent, /Doctor self-audit cadence/);
});

// --- 2. 🔴 Save posts every visible row, keyed by its data-timing-key ----------

test('Save posts every field, keyed correctly, and reports success', async () => {
    await openDrawer();

    const inputs = document.querySelectorAll('#timing-rows .s-timing-input');
    inputs.forEach(inp => {
        if (inp.dataset.timingKey === 'scheduler_poll_interval_seconds') inp.value = '45';
    });

    timingPostResponse = { knobs: KNOBS.map(k => k.key === 'scheduler_poll_interval_seconds'
        ? { ...k, stored: 45, effective: 45 } : k) };

    await window.chela.saveTiming();
    await flush();

    const post = fetchCalls.find(c => c.url.endsWith('/api/config/timing') && c.opts && c.opts.method === 'POST');
    assert.ok(post, 'Save did not POST to /api/config/timing');
    const sent = JSON.parse(post.opts.body);
    assert.equal(sent.scheduler_poll_interval_seconds, '45');
    assert.equal(sent.doctor_check_interval_seconds, '1800', 'an unedited field was dropped from the batch');
    assert.ok(!('status_cmd_timeout_seconds' in sent),
        'an env-overridden (disabled) field was posted — Save would re-persist the env value');

    const msg = document.getElementById('timing-msg');
    assert.match(msg.textContent, /Saved/);
    assert.ok(!msg.className.includes('err'));
});

// --- 3. 🔴 a server rejection surfaces the error and does not claim success ---

test('a rejected save shows the field error and does not say Saved', async () => {
    await openDrawer();

    timingPostResponse = {
        error: 'invalid timing setting(s)',
        errors: { scheduler_poll_interval_seconds: 'Daemon tick must be a number' },
    };

    await window.chela.saveTiming();
    await flush();
    await flush();   // the error path re-calls _loadTimingSettings(), itself async

    const msg = document.getElementById('timing-msg');
    assert.doesNotMatch(msg.textContent, /^Saved/);
    assert.match(msg.textContent, /Daemon tick must be a number/);
    assert.ok(msg.className.includes('err'));
});

// --- 4. 🔴 an env-overridden knob renders disabled, annotated, not editable ---

test('a knob with source "env" renders its field disabled and annotated', async () => {
    await openDrawer();

    const input = document.querySelector('#timing-rows [data-timing-key="status_cmd_timeout_seconds"]');
    assert.ok(input, 'env-overridden knob did not get a row at all');
    assert.equal(input.disabled, true, 'an env-overridden field must not be editable');
    assert.equal(input.value, '60', 'a disabled field should show the effective (env) value');

    const row = document.getElementById('timing-rows');
    assert.match(row.textContent, /env/, 'no annotation marks the row as env-overridden');
});

// --- 5. 🔴 a restart-required knob annotates its row -------------------------

test('a knob with restart_required renders a restart annotation', async () => {
    await openDrawer();

    const row = document.getElementById('timing-rows');
    assert.match(row.textContent, /restart/i,
        'the status-feed timeout/TTL pair needs a restart but the tab does not say so');
});
