// SETTINGS MODAL — env precedence guard, driven through the REAL tabbed modal
// (CMX-287 rework round 1). settings_timing.test.mjs and settings_dispatch.test.mjs
// already assert `k.source === 'env'` disables a knob's field, but against a bare
// `#settings-drawer`/`#drawer-body` fixture predating the CMX-287 tab rewrite — not
// the modal's real `#settings-tabs` + `.settings-tabpanels` markup a user actually
// sees. This file drives the guard through the exact index.html structure instead,
// and closes a second gap the existing files don't cover: the Dispatch tab's
// `bool`-kind knobs render through an entirely different branch of
// `_renderDispatchRows` (a `<select>`, not an `<input>`) that shares the same
// `isEnv` flag but was never independently exercised — see DEFEAT_SHAPES.md #22
// ("a field declared identically on every branch of one function, tested through
// only one branch").
//
// Negative control (recorded, not just asserted): mutating `_renderTimingRows`'s or
// `_renderDispatchRows`'s `const isEnv = k.source === 'env'` to `const isEnv = false`
// turns every test below RED — verified by hand before writing this file.
//
// CMX-287 rework round 2 (PR #358) — DEFEAT_SHAPES #34: this file's BODY used to
// be hand-typed to LOOK like index.html's settings modal, rather than sliced
// from the template itself, so a mutation to the real template (e.g. renaming
// `id="settings-tabs"`) would never reach this fixture — it just kept agreeing
// with its own hand-typed copy. Sliced from the real file now, same idiom as
// tests/dashboard_default_view.test.mjs.
//
// Run: node --test tests/settings_modal_precedence.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `npm ci` for jsdom.)
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { JSDOM } from 'jsdom';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', 'chela', 'dashboard');
const HTML = readFileSync(join(ROOT, 'templates', 'index.html'), 'utf8');

// The REAL modal markup (scrim + tab rail + tabpanel host), sliced straight out
// of index.html — not hand-typed — so a mutation to the tab rail's id (the
// judge's own reported mutation) shows up here, not just in a fixture that
// happens to agree with the file today.
const SETTINGS_START = HTML.indexOf('<div class="drawer-scrim" id="drawer-scrim"');
const SETTINGS_END = HTML.indexOf('<!-- "+ new" popover');
if (SETTINGS_START < 0 || SETTINGS_END < 0) throw new Error('index.html markers for the settings modal moved — update this test');
const BODY = HTML.slice(SETTINGS_START, SETTINGS_END);

const TIMING_KNOBS = [
    { key: 'scheduler_poll_interval_seconds', env: 'CHELA_SCHEDULER_POLL_INTERVAL',
      label: 'Daemon tick', unit: 's', default: 30, stored: '', effective: 30,
      source: 'default', restart_required: false },
    { key: 'status_cmd_timeout_seconds', env: 'CHELA_STATUS_CMD_TIMEOUT_S',
      label: 'Status-feed subprocess timeout', unit: 's', default: 45.0, stored: '', effective: 60.0,
      source: 'env', restart_required: true },
];

const DISPATCH_KNOBS = [
    { key: 'max_reworks', env: 'CHELA_MAX_REWORKS', label: 'Rework cap', unit: '',
      default: 2, stored: '', effective: 2, source: 'default', restart_required: false },
    { key: 'merge_base', env: 'CHELA_MERGE_BASE', label: 'Autonomous base', unit: '',
      default: 'dev', stored: '', effective: 'release-train', source: 'env', restart_required: true },
    // bool-kind, env-overridden: a DIFFERENT branch of _renderDispatchRows (a
    // <select>, not an <input>) than merge_base above.
    { key: 'judge_enabled', env: 'CHELA_JUDGE', label: 'Judge', unit: '', kind: 'bool',
      default: true, stored: '', effective: false, source: 'env', restart_required: true },
];

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
    globalThis.fetch = (url) => {
        const u = String(url);
        fetchCalls.push({ url: u });
        if (u.endsWith('/api/config/timing')) {
            return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ knobs: TIMING_KNOBS }) });
        }
        if (u.endsWith('/api/config/dispatch')) {
            return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ knobs: DISPATCH_KNOBS }) });
        }
        // Every other settings fetch (/api/config, /api/settings, /api/agents,
        // /api/cost, ...): an empty 200 is enough to keep renderSettings() from throwing.
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    };
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;
    globalThis.TERMINALS_ENABLED = dom.window.TERMINALS_ENABLED = true;
    await import('../chela/dashboard/static/js/main.js');
});

beforeEach(() => {
    document.getElementById('settings-drawer').classList.remove('open');
    fetchCalls = [];
});

async function openOnTab(tab) {
    window.chela.toggleSettings();
    await flush();
    await flush();   // renderSettings()'s per-tab loaders are themselves async
    window.chela.selectSettingsTab(tab);
    await flush();
    await flush();
}

// --- Timing tab: env-overridden knob, through the real modal -------------------

test('Timing tab: a knob with source "env" renders disabled with its override explained, in the real modal', async () => {
    await openOnTab('timing');

    // Confirm this really is the tabbed modal, not the old bare drawer.
    assert.ok(document.getElementById('settings-tabs').children.length > 0,
        'the tab rail never rendered — this is not exercising the real modal markup');
    assert.equal(document.querySelector('.settings-tabpanel[data-tab="timing"]').classList.contains('active'), true);

    const input = document.querySelector('#timing-rows [data-timing-key="status_cmd_timeout_seconds"]');
    assert.ok(input, 'env-overridden timing knob did not get a row');
    assert.equal(input.disabled, true, 'an env-overridden field must not be editable');
    assert.equal(input.value, '60', 'a disabled field should show the effective (env) value, not the default');

    const row = input.closest('.s-row');
    assert.match(row.textContent, /env/, 'no visible annotation marks the row as env-overridden');
    const explained = input.title || (row.querySelector('[title]') && row.querySelector('[title]').title) || '';
    assert.match(explained, /CHELA_STATUS_CMD_TIMEOUT_S/,
        'nothing on the row explains WHICH env var is overriding it');
});

// --- Dispatch tab: env-overridden knob, through the real modal -----------------

test('Dispatch tab: a knob with source "env" renders disabled with its override explained, in the real modal', async () => {
    await openOnTab('dispatch');

    const input = document.querySelector('#dispatch-rows [data-dispatch-key="merge_base"]');
    assert.ok(input, 'env-overridden dispatch knob did not get a row');
    assert.equal(input.disabled, true, 'an env-overridden field must not be editable');
    assert.equal(input.value, 'release-train', 'a disabled field should show the effective (env) value');
    assert.match(input.title, /CHELA_MERGE_BASE/, 'nothing explains which env var is overriding it');
});

// --- Dispatch tab: env-overridden BOOL knob — the <select> branch, untested elsewhere --

test('Dispatch tab: a BOOL-kind knob with source "env" also renders its <select> disabled', async () => {
    await openOnTab('dispatch');

    const select = document.querySelector('#dispatch-rows [data-dispatch-key="judge_enabled"]');
    assert.ok(select, 'env-overridden bool knob did not get a row');
    assert.equal(select.tagName, 'SELECT', 'a bool-kind knob must render as a <select>, not a text/number input');
    assert.equal(select.disabled, true,
        'an env-overridden bool knob\'s <select> must not be editable — this is a separate code branch from the text/number input case');
    assert.match(select.title, /CHELA_JUDGE/, 'nothing explains which env var is overriding it');
});
