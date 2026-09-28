// CMX-381: changing the dashboard theme must re-theme the OPEN terminals live —
// in a REAL DOM, running the real nav.js (via main.js, same module-graph import
// as tests/settings_timing.test.mjs).
//
// Each open terminal is a ttyd iframe whose injected shim (app.py
// _term_theme_shim) exposes `window.chelaApplyTermTheme`. The fake below stands
// in for it: like the real shim it reads `chela_theme` from the same-origin
// localStorage and sets it on a fake `window.term` — so the test also pins the
// ORDER (storage written before the poke), not just that a call happened.
//
//   1. 🔴 setTheme re-themes every open terminal: each iframe's fake term ends up
//      on the new palette key. Drop the live-apply call and it stays stale.
//   2. 🔴 the Settings > Appearance picker offers `warm`.
//
// Run: node --test tests/term_theme_live.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `npm ci` for jsdom.)
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const BODY = `
<div class="drawer-scrim" id="drawer-scrim" onclick="chela.toggleSettings()"></div>
<aside class="drawer" id="settings-drawer">
  <div class="drawer-body" id="drawer-body"></div>
</aside>
<div id="wall"></div>`;

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
    globalThis.fetch = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;
    globalThis.TERMINALS_ENABLED = dom.window.TERMINALS_ENABLED = true;
    await import('../chela/dashboard/static/js/main.js');
});

// N open panes, each an iframe carrying a fake shim + fake xterm.
function openPanes(n) {
    const wall = document.getElementById('wall');
    wall.innerHTML = '';
    const terms = [];
    for (let i = 0; i < n; i++) {
        const f = document.createElement('iframe');
        wall.appendChild(f);
        const w = f.contentWindow;
        const term = { options: { theme: { key: 'dark' } } };
        w.term = term;
        w.chelaApplyTermTheme = () => {
            term.options.theme = { key: localStorage.getItem('chela_theme') };
        };
        terms.push(term);
    }
    return terms;
}

beforeEach(() => {
    localStorage.setItem('chela_theme', 'dark');
    document.body.dataset.theme = 'dark';
});

test('setTheme re-themes every open terminal live', () => {
    const terms = openPanes(4);
    window.chela.setTheme('warm');
    assert.equal(document.body.dataset.theme, 'warm');
    assert.equal(localStorage.getItem('chela_theme'), 'warm');
    terms.forEach((t, i) => assert.equal(t.options.theme.key, 'warm', `pane ${i} kept the old terminal palette`));

    window.chela.setTheme('gruvbox');
    terms.forEach((t, i) => assert.equal(t.options.theme.key, 'gruvbox', `pane ${i} missed the second switch`));
});

test('setTheme tolerates an iframe whose shim is not loaded yet', () => {
    const terms = openPanes(2);
    const bare = document.createElement('iframe');
    document.getElementById('wall').appendChild(bare);   // no chelaApplyTermTheme
    window.chela.setTheme('nord');
    terms.forEach(t => assert.equal(t.options.theme.key, 'nord'));
});

test('the Settings theme picker offers warm', async () => {
    window.chela.toggleSettings();
    await flush();
    const sel = document.getElementById('theme-select');
    assert.ok(sel, 'theme picker not rendered');
    const opts = [...sel.options].map(o => o.value);
    assert.ok(opts.includes('warm'), `warm missing from picker: ${opts}`);
    assert.equal([...sel.options].find(o => o.value === 'warm').textContent, 'Warm');
});
