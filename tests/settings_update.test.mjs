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
        const body = String(url).endsWith('/api/settings')
            ? { sections: [], update: updatePayload }
            : {};
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
