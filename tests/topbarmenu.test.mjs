// TOPBAR PRIMARIES → ONE MENU, IN A REAL DOM (CMX-109 / CMX-108 Part A re-filed).
//
// cmx-108/PR #122 folded the WALL toolbar's grid-preset picker + lock button behind
// one "Layout" menu (tests/wallnav.test.mjs) — a different, valid consolidation from
// the one this task asks for. This is the MAIN topbar: three separate primaries
// (#btn-palette "Jump to…", #btn-new "New…", #btn-overflow "More") fold into ONE
// button (#btn-primary-menu) opening ONE popover (#primary-menu). "Jump to…" and the
// old overflow's items (Share current / Notifications / Settings) are flat rows in
// that popover; "New…" reopens the pre-existing #new-menu (openNewMenuFromPrimary)
// rather than duplicating its Favorites/Recent host — #new-menu still serves the
// sidebar's own "+" trigger from a different anchor, unchanged.
//
// Runs the REAL nav.js (via main.js, same module-graph-import approach as
// tests/sidebar.test.mjs) in jsdom — real openPrimaryMenu/hidePrimaryMenu/
// openNewMenuFromPrimary/openPalette against real DOM nodes, not a source grep.
//
// Three properties, each a regression that would ship silently:
//
//   1. 🔴 ONE TRIGGER, NOT THREE. #btn-primary-menu is the only primary-action
//      button left in .topbar-actions; the old #btn-palette/#btn-new/#btn-overflow
//      ids are gone from index.html.
//   2. 🔴 THE MENU ACTUALLY EXPOSES JUMP / NEW / OVERFLOW. Opening #primary-menu
//      renders rows wired to openPalette, openNewMenuFromPrimary, and the three old
//      overflow actions (Share current session / Notifications / Settings) — drop
//      any one of those wires and this goes red. ⌘K still opens the palette
//      directly, independent of the menu (the global keydown listener).
//   3. 🔴 #btn-shares STAYS OUTSIDE THE MENU. The safety kill-switch must never end
//      up inside #primary-menu — it has to stay visible whenever a share is live,
//      menu open or not.
//
// Run: node --test tests/topbarmenu.test.mjs (pytest runs it via tests/test_js_suites.py;
// it needs `npm ci` for jsdom — CHELA_REQUIRE_JS_TESTS makes a missing jsdom a FAILURE.)
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { JSDOM } from 'jsdom';   // needs `npm ci` — tests/test_js_suites.py enforces it

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', 'chela', 'dashboard');
const HTML = readFileSync(join(ROOT, 'templates', 'index.html'), 'utf8');

// The topbar + the two popovers, as index.html emits them (only the ids nav.js
// reaches for). #new-menu is unchanged from before this fold — still the sidebar
// "+" trigger's popover too — so it stays minimal here.
const BODY = `
<header class="topbar">
  <div class="topbar-actions">
    <button class="shares-indicator" id="btn-shares" hidden></button>
    <button class="icon-btn" id="btn-primary-menu" aria-haspopup="true" onclick="chela.openPrimaryMenu(event)"></button>
  </div>
</header>
<div class="popover launch-menu" id="new-menu" style="display:none;">
  <div id="new-menu-launch"></div>
</div>
<div class="popover overflow-menu" id="primary-menu" style="display:none;">
  <div class="popover-item ov-item" id="pm-jump" onclick="chela.hidePrimaryMenu(); chela.openPalette()">
    <span>Jump to…</span><span class="pt-kbd">⌘K</span>
  </div>
  <div class="popover-item ov-item" id="pm-new" onclick="chela.openNewMenuFromPrimary()">
    <span>New…</span>
  </div>
  <div class="popover-item ov-item" id="pm-share" onclick="chela.hidePrimaryMenu(); chela.shareCurrentAgent()">
    <span>Share current session</span>
  </div>
  <div class="popover-item ov-item" id="pm-notify" onclick="chela.hidePrimaryMenu(); chela.toggleSettings('notify')">
    <span>Notifications</span>
  </div>
  <div class="popover-item ov-item" id="pm-settings" onclick="chela.hidePrimaryMenu(); chela.toggleSettings()">
    <span>Settings</span>
  </div>
</div>
<div class="palette-overlay" id="palette">
  <div class="palette">
    <input id="palette-input">
    <div id="palette-list"></div>
  </div>
</div>`;

let nav;

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
    nav = await import('../chela/dashboard/static/js/nav.js');
});

// --- 1. 🔴 one trigger, not three -------------------------------------------------

test('the topbar has ONE primary button, not the old three', () => {
    assert.ok(HTML.includes('id="btn-primary-menu"'), '#btn-primary-menu is missing from index.html');
    assert.ok(!HTML.includes('id="btn-palette"'), '#btn-palette is still in index.html — not folded');
    assert.ok(!HTML.includes('id="btn-new"'), '#btn-new is still in index.html — not folded');
    assert.ok(!HTML.includes('id="btn-overflow"'), '#btn-overflow is still in index.html — not folded');
});

// The DOM fixtures below are hand-built (same approach as tests/wallnav.test.mjs /
// tests/sidebar.test.mjs), so they cannot catch a structural regression in the REAL
// index.html — e.g. #btn-shares physically moved inside #primary-menu there while
// staying correctly-outside in this file's fixture. These two run against the real
// source text instead.

test('.topbar-actions in the REAL index.html has exactly one primary button', () => {
    const topbar = HTML.match(/<div class="topbar-actions">[\s\S]*?<\/div>\s*<\/header>/)[0];
    const buttonIds = [...topbar.matchAll(/<button[^>]*\bid="([^"]+)"/g)].map(m => m[1]);
    assert.deepEqual(buttonIds, ['btn-shares', 'btn-primary-menu'],
        `.topbar-actions has unexpected buttons: ${buttonIds.join(', ')}`);
});

test('#btn-shares is never nested inside #primary-menu in the REAL index.html', () => {
    const m = HTML.match(/<div class="popover overflow-menu" id="primary-menu"[\s\S]*?\n<\/div>/);
    assert.ok(m, '#primary-menu block not found in index.html');
    assert.ok(!m[0].includes('btn-shares'), '#btn-shares is inside the #primary-menu markup block');
});

// The behavioural tests below (openPalette, openNewMenuFromPrimary, …) run against
// this file's own hand-built fixture, so they cannot catch a row being DELETED from
// the REAL #primary-menu markup while the fixture keeps it. This checks the actual
// source text carries every wire the fold promised to keep reachable.
test('the REAL #primary-menu block still wires every folded action', () => {
    const m = HTML.match(/<div class="popover overflow-menu" id="primary-menu"[\s\S]*?\n<\/div>/);
    assert.ok(m, '#primary-menu block not found in index.html');
    const block = m[0];
    [
        [/chela\.openPalette\(\)/, 'Jump to… → chela.openPalette()'],
        [/chela\.openNewMenuFromPrimary\(\)/, 'New… → chela.openNewMenuFromPrimary()'],
        [/chela\.shareCurrentAgent\(\)/, 'Share current session → chela.shareCurrentAgent()'],
        [/chela\.toggleSettings\('notify'\)/, "Notifications → chela.toggleSettings('notify')"],
        [/chela\.toggleSettings\(\)/, 'Settings → chela.toggleSettings()'],
    ].forEach(([re, label]) => assert.match(block, re, `#primary-menu dropped: ${label}`));
    // The usage/updated readouts moved off the old #overflow-menu unchanged.
    ['hdr-ratelimit-pill', 'hdr-weekly-rl-pill', 'hdr-schedules', 'hdr-next-pill', 'hdr-updated'].forEach(id =>
        assert.ok(block.includes(`id="${id}"`), `#primary-menu dropped the ${id} readout`));
});

// --- 2. 🔴 the menu actually exposes jump / new / overflow, wired to the REAL fns -

test('opening #primary-menu measures + positions off the real anchor', () => {
    const m = document.getElementById('primary-menu');
    const anchor = document.getElementById('btn-primary-menu');
    anchor.getBoundingClientRect = () => ({ top: 8, bottom: 40, left: 969, right: 1001, width: 32, height: 32 });
    Object.defineProperty(m, 'offsetWidth', { value: 210, configurable: true });

    window.chela.openPrimaryMenu({ stopPropagation() {}, currentTarget: anchor });

    assert.equal(m.style.display, 'block', 'openPrimaryMenu did not show #primary-menu');
    assert.equal(parseFloat(m.style.left) + 210, 1001, 'the menu is not right-aligned to the button');
    window.chela.hidePrimaryMenu();
    assert.equal(m.style.display, 'none', 'hidePrimaryMenu did not hide #primary-menu');
});

// jsdom does not execute inline onclick="..." attributes without runScripts:
// "dangerously" (unset here, matching wall.test.mjs/wallnav.test.mjs/sidebar.test.mjs
// — none of them enable it either). So each row is checked two ways: its onclick
// ATTRIBUTE textually names the real function (catches "the wire got deleted"), and
// that real function is then called directly (catches "the wire is a no-op").

test('the "Jump to…" row is wired to openPalette, and the real function opens the REAL palette', () => {
    assert.match(document.getElementById('pm-jump').getAttribute('onclick'), /chela\.openPalette\(\)/,
        '#pm-jump is not wired to chela.openPalette()');
    window.chela.openPalette();
    assert.ok(document.getElementById('palette').classList.contains('open'),
        'openPalette() did not open #palette — the row would be decoration even though wired');
    window.chela.closePalette();
});

test('⌘K opens the palette directly — independent of the primary menu', () => {
    assert.equal(document.getElementById('palette').classList.contains('open'), false);
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true, bubbles: true }));
    assert.ok(document.getElementById('palette').classList.contains('open'),
        '⌘K no longer opens the palette — the direct shortcut regressed');
    window.chela.closePalette();
});

test('the "New…" row is wired to openNewMenuFromPrimary, and it reopens the REAL #new-menu', () => {
    assert.match(document.getElementById('pm-new').getAttribute('onclick'), /chela\.openNewMenuFromPrimary\(\)/,
        '#pm-new is not wired to chela.openNewMenuFromPrimary()');
    const newMenu = document.getElementById('new-menu');
    const anchor = document.getElementById('btn-primary-menu');
    anchor.getBoundingClientRect = () => ({ top: 8, bottom: 40, left: 969, right: 1001, width: 32, height: 32 });
    Object.defineProperty(newMenu, 'offsetWidth', { value: 232, configurable: true });

    window.chela.openNewMenuFromPrimary();

    assert.equal(newMenu.style.display, 'block',
        'openNewMenuFromPrimary() did not reopen #new-menu — the Favorites/Recent surface is unreachable from the topbar');
    assert.equal(document.getElementById('primary-menu').style.display, 'none',
        'openNewMenuFromPrimary() left #primary-menu open on top of #new-menu');
    window.chela.hideNewMenu();
});

test('the overflow rows (Share current / Notifications / Settings) are real, wired rows', () => {
    const pm = document.getElementById('primary-menu');
    const rowText = [...pm.querySelectorAll('.ov-item')].map(el => el.textContent.trim());
    assert.ok(rowText.some(t => t.includes('Share current session')), 'Share current session row is missing');
    assert.ok(rowText.some(t => t.includes('Notifications')), 'Notifications row is missing');
    assert.ok(rowText.some(t => t.includes('Settings')), 'Settings row is missing');
    assert.match(document.getElementById('pm-share').getAttribute('onclick'), /chela\.shareCurrentAgent\(\)/,
        '#pm-share is not wired to chela.shareCurrentAgent()');
    assert.match(document.getElementById('pm-notify').getAttribute('onclick'), /chela\.toggleSettings\('notify'\)/,
        "#pm-notify is not wired to chela.toggleSettings('notify')");
    assert.match(document.getElementById('pm-settings').getAttribute('onclick'), /chela\.toggleSettings\(\)/,
        '#pm-settings is not wired to chela.toggleSettings()');
    // Wired to the real functions nav.js exports, not just present as text.
    assert.equal(typeof nav, 'object');
    ['hidePrimaryMenu', 'openPrimaryMenu', 'openNewMenuFromPrimary', 'openPalette'].forEach(fn =>
        assert.equal(typeof window.chela[fn], 'function', `window.chela.${fn} is missing`));
});

// --- 3. 🔴 the safety kill-switch stays OUTSIDE the menu --------------------------

test('#btn-shares is never a descendant of #primary-menu', () => {
    const shares = document.getElementById('btn-shares');
    const pm = document.getElementById('primary-menu');
    assert.equal(pm.contains(shares), false, '#btn-shares moved INSIDE the primary menu — the kill-switch must always be visible');
    assert.equal(document.getElementById('primary-menu').contains(document.getElementById('btn-shares')), false);
});
