// SETTINGS SEARCH — VS CODE STYLE, ACROSS EVERY TAB (CMX-396), IN A REAL DOM.
//
// Runs the REAL main.js/nav.js in jsdom over the REAL settings modal markup sliced
// out of index.html, with the REAL style.css loaded — so "visible" below is jsdom's
// computed `display` up the ancestor chain, not a re-statement of nav.js's classes.
// (tests/browser/dashboard.test.mjs measures the same thing in Chromium.)
//
// Guards (each corrupt→RED, recorded in the PR's self-check experiments):
//   - "remote" typed while on ANOTHER tab shows the Remote Control row and hides
//     unrelated rows; clearing the query restores the tab you were on.
//   - a keyword-only term (in data-keywords, not in the label or help) matches.
//   - Esc clears the query first, and a second Esc closes the drawer.
//   - rows rendered AFTER the query was typed (Timing AND Dispatch load async) are
//     filtered too, and the section's unlabelled Save row stays visible.
//   - a section whose OWN heading / help text / data-keywords match shows whole
//     (Remote access has no labelled rows — this is its only way in), and the live
//     count counts every labelled row of a whole-matched section.
//   - multi-word queries are AND.
//   - Ctrl+, does NOT focus the box on a touch (pointer: coarse) screen.
//   - empty state + the live count.
//   - ⭐ MUST BE ACCEPTED: an empty query leaves the drawer byte-for-byte as rendered.
//
// Run: node --test tests/settings_search.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `pnpm install` for jsdom.)
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { JSDOM } from 'jsdom';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', 'chela', 'dashboard');
const HTML = readFileSync(join(ROOT, 'templates', 'index.html'), 'utf8');
const CSS = readFileSync(join(ROOT, 'static', 'style.css'), 'utf8');

const SETTINGS_START = HTML.indexOf('<div class="drawer-scrim" id="drawer-scrim"');
const SETTINGS_END = HTML.indexOf('<!-- "+ new" popover');
if (SETTINGS_START < 0 || SETTINGS_END < 0) throw new Error('index.html markers for the settings modal moved — update this test');
const BODY = `
<div class="palette-overlay" id="palette">
  <div class="palette"><input id="palette-input"><div id="palette-list"></div></div>
</div>
${HTML.slice(SETTINGS_START, SETTINGS_END)}`;

// Per-test API responses; a value that is a function is called for a (possibly
// deferred) body.
let routes = {};
// Flip to true to make matchMedia('(pointer: coarse)') match (a touch screen).
let coarsePointer = false;

function flush() {
    return new Promise(resolve => setTimeout(resolve, 0));
}

before(async () => {
    const dom = new JSDOM(`<!doctype html><html><head><style>${CSS}</style></head><body>${BODY}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
    for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement',
        'Element', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'CustomEvent',
        'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
        Object.defineProperty(globalThis, k, {
            value: dom.window[k], writable: true, configurable: true,
        });
    }
    dom.window.matchMedia = q => ({
        media: q, matches: coarsePointer && /pointer:\s*coarse/.test(q),
        addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
    });
    globalThis.fetch = (url) => {
        const path = new URL(String(url), 'http://localhost:5005/').pathname;
        const r = routes[path];
        const body = typeof r === 'function' ? r() : (r || {});
        return Promise.resolve(body).then(b => ({ ok: true, status: 200, json: () => Promise.resolve(b) }));
    };
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;
    globalThis.TERMINALS_ENABLED = dom.window.TERMINALS_ENABLED = true;
    await import('../chela/dashboard/static/js/main.js');
});

const $ = sel => document.querySelector(sel);
const modal = () => document.getElementById('settings-drawer');
const settingsOpen = () => modal().classList.contains('open');
const input = () => document.getElementById('settings-search');
const key = (init) => {
    const ev = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init });
    (document.activeElement || document).dispatchEvent(ev);
    return ev;
};

// Visible = nothing from the element up to the modal computes to display:none.
function visible(el) {
    assert.ok(el, 'visible(): element not found');
    for (let n = el; n && n !== document.body; n = n.parentElement) {
        if (getComputedStyle(n).display === 'none') return false;
        if (n.hidden) return false;
    }
    return true;
}
const rowOf = (sel) => document.querySelector(sel).closest('.s-row');
const sectionTitled = (title) => [...document.querySelectorAll('#drawer-body .settings-section')]
    .find(s => s.querySelector('h4')?.textContent.trim() === title);
const activeTab = () => [...document.querySelectorAll('.settings-tab.active')].map(t => t.dataset.tab);
const activePanel = () => [...document.querySelectorAll('.settings-tabpanel.active')].map(t => t.dataset.tab);

// jsdom runs no inline handlers here, so run the template's OWN oninput text
// (with `chela` bound to the real namespace) — a rewired or dropped handler in
// index.html fails this, where calling settingsSearch() directly would not.
function type(q) {
    const el = input();
    el.value = q;
    const handler = el.getAttribute('oninput');
    assert.ok(handler, '#settings-search has no oninput handler');
    new Function('chela', handler).call(el, window.chela);
}

async function openDrawer() {
    window.chela.toggleSettings();
    await flush();
    await flush();
}

beforeEach(() => {
    routes = {};
    coarsePointer = false;
    if (settingsOpen()) window.chela.toggleSettings();
    window.chela.closePalette();
    window.chela.selectSettingsTab('general');
});

test('Ctrl+, opens Settings with the search box focused and empty', async () => {
    key({ key: ',', ctrlKey: true });
    await flush();
    assert.equal(settingsOpen(), true, 'Ctrl+, did not open Settings');
    assert.equal(document.activeElement, input(), 'the search box is not focused on open');
    assert.equal(input().value, '');
});

test('"remote" typed on another tab shows Remote Control and hides unrelated rows; clearing restores the tab', async () => {
    await openDrawer();
    window.chela.selectSettingsTab('appearance');
    assert.equal(visible(rowOf('#remote-control-toggle')), false, 'setup: Remote Control is visible on the Appearance tab');
    assert.equal(visible(rowOf('#theme-select')), true, 'setup: Theme is not visible on the Appearance tab');

    type('ReMoTe');
    assert.equal(visible(rowOf('#remote-control-toggle')), true, '"remote" did not show the Remote Control row');
    for (const sel of ['#theme-select', '#term-latin-select', '#collab-name', '#agent-model-select', '#update-apply-btn', '#run-toasts-select']) {
        assert.equal(visible(document.querySelector(sel)), false, `"remote" left unrelated ${sel} visible`);
    }
    assert.deepEqual(activeTab(), [], 'the tab rail still shows a selected tab while searching');
    const groups = [...document.querySelectorAll('#drawer-body .settings-search-group')].filter(visible);
    assert.deepEqual(groups.map(g => g.textContent), ['General'], 'results are not grouped under their tab name');
    assert.match($('#settings-search-status').textContent, /^\d+ settings?$/, 'no live count');

    type('');
    assert.deepEqual(activeTab(), ['appearance'], 'clearing did not restore the tab you were on');
    assert.deepEqual(activePanel(), ['appearance']);
    assert.equal(visible(rowOf('#theme-select')), true, 'clearing did not bring the Appearance rows back');
    assert.equal(visible(rowOf('#remote-control-toggle')), false, 'clearing left the General tab showing');
    assert.equal($('#settings-search-status').textContent, '');
});

test('a keyword-only term (data-keywords, not the label or help) matches its row', async () => {
    await openDrawer();
    window.chela.selectSettingsTab('cost');
    const row = rowOf('#remote-control-toggle');
    const sec = row.closest('.settings-section');
    assert.doesNotMatch(sec.textContent.toLowerCase(), /mobile/, 'setup: "mobile" is not keyword-only any more');
    assert.match(row.dataset.keywords, /mobile/, 'setup: the Remote Control row lost its keywords');

    type('mobile');
    assert.equal(visible(row), true, 'a data-keywords-only term did not match the Remote Control row');
    assert.equal($('#settings-search-status').textContent, '1 setting');
    assert.equal(visible(rowOf('#theme-select')), false);
});

test('Esc clears the query first, and a second Esc closes Settings', async () => {
    await openDrawer();
    input().focus();
    type('remote');
    key({ key: 'Escape' });
    assert.equal(input().value, '', 'the first Esc did not clear the query');
    assert.equal(settingsOpen(), true, 'the first Esc closed Settings with a query in the box');
    assert.equal(visible(rowOf('#update-apply-btn')), true, 'the first Esc left the filter applied');
    key({ key: 'Escape' });
    assert.equal(settingsOpen(), false, 'the second Esc did not close Settings');
});

test('empty state: no match says so, and so does the live region', async () => {
    await openDrawer();
    type('zzqqxx');
    const empty = $('#settings-search-empty');
    assert.equal(visible(empty), true, 'no empty state shown');
    assert.equal(empty.textContent, "No settings match 'zzqqxx'");
    assert.equal($('#settings-search-status').textContent, "No settings match 'zzqqxx'");
    const shown = [...document.querySelectorAll('#drawer-body .settings-section')].filter(visible);
    assert.deepEqual(shown, [], 'a non-matching query still shows sections');
    type('theme');
    assert.equal(visible(empty), false, 'the empty state outlived a matching query');
});

test('rows that render AFTER the query was typed are filtered too (Timing loads async)', async () => {
    let release;
    routes['/api/config/timing'] = () => new Promise(r => { release = r; });
    await openDrawer();
    type('heartbeat');
    release({ knobs: [
        { key: 'hb', label: 'Heartbeat interval', unit: 's', default: 5, stored: '', effective: 5, source: 'default' },
        { key: 'other', label: 'Something unrelated', unit: 's', default: 9, stored: '', effective: 9, source: 'default' },
    ] });
    await flush();
    await flush();
    const rows = [...document.querySelectorAll('#timing-rows .s-row')];
    assert.equal(rows.length, 2, 'setup: timing rows did not render');
    assert.equal(visible(rows[0]), true, 'the late-rendered matching row is hidden');
    assert.equal(visible(rows[1]), false, 'a late-rendered row escaped the active filter');
    const save = document.querySelector('#settings-timing button[onclick="chela.saveTiming()"]');
    assert.equal(visible(save), true, 'the unlabelled Save row was hidden — a filtered Timing row cannot be saved');
    assert.equal($('#settings-search-status').textContent, '1 setting', 'the Save row was counted as a setting');
});

test('Dispatch rows that render AFTER the query was typed are filtered too', async () => {
    let release;
    routes['/api/config/dispatch'] = () => new Promise(r => { release = r; });
    await openDrawer();
    type('zebra');
    assert.ok(release, 'setup: opening Settings did not request /api/config/dispatch');
    assert.doesNotMatch(sectionTitled('Dispatch').textContent.toLowerCase(), /zebra/, 'setup: "zebra" is in the Dispatch section already');
    release({ knobs: [
        { key: 'z', label: 'Zebra limit', kind: 'number', unit: '', default: 3, stored: '', effective: 3, source: 'default' },
        { key: 'o', label: 'Something unrelated', kind: 'number', unit: '', default: 1, stored: '', effective: 1, source: 'default' },
    ] });
    await flush();
    await flush();
    const rows = [...document.querySelectorAll('#dispatch-rows .s-row')];
    assert.equal(rows.length, 2, 'setup: dispatch rows did not render');
    assert.equal(visible(rows[0]), true, 'the late-rendered matching Dispatch row is hidden');
    assert.equal(visible(rows[1]), false, 'a late-rendered Dispatch row escaped the active filter');
    const save = document.querySelector('#settings-dispatch button[onclick="chela.saveDispatch()"]');
    assert.equal(visible(save), true, 'the unlabelled Dispatch Save row was hidden');
});

test('a section-level data-keywords term shows that whole section (Remote access has no labelled rows)', async () => {
    await openDrawer();
    window.chela.selectSettingsTab('appearance');
    const sec = sectionTitled('Remote access');
    assert.ok(sec, 'setup: no Remote access section');
    assert.equal(sec.querySelectorAll('.s-rowlabel').length, 0, 'setup: Remote access gained labelled rows');
    assert.doesNotMatch(sec.textContent.toLowerCase(), /\bvpn\b/, 'setup: "vpn" is not keyword-only any more');
    assert.match(sec.dataset.keywords, /\bvpn\b/, 'setup: Remote access lost its "vpn" keyword');

    type('vpn');
    assert.equal(visible(sec), true, 'a section data-keywords term did not show its section');
    assert.equal($('#settings-search-status').textContent, '1 setting');
    assert.equal(visible(rowOf('#remote-control-toggle')), false, 'unrelated Remote Control row shown for "vpn"');
});

test('a section help-text term (.s-desc) shows that whole section', async () => {
    await openDrawer();
    const sec = sectionTitled('Remote access');
    const desc = [...sec.querySelectorAll('.s-desc')].map(e => e.textContent.toLowerCase()).join(' ');
    assert.match(desc, /termius/, 'setup: "termius" left the Remote access help text');
    assert.doesNotMatch(sec.querySelector('h4').textContent.toLowerCase() + ' ' + sec.dataset.keywords, /termius/,
        'setup: "termius" is not help-text-only any more');

    type('termius');
    assert.equal(visible(sec), true, 'a help-text-only term did not show its section');
    assert.equal($('#settings-search-status').textContent, '1 setting');
});

test('a whole-matched section counts every labelled row it shows', async () => {
    await openDrawer();
    const sec = sectionTitled('Terminal font');
    const labelled = [...sec.querySelectorAll('.s-row')].filter(r => r.querySelector('.s-rowlabel'));
    assert.equal(labelled.length, 3, 'setup: Terminal font no longer has 3 labelled rows');

    type('terminal font');
    for (const r of labelled) assert.equal(visible(r), true, 'a row of the whole-matched section is hidden');
    const shown = [...document.querySelectorAll('#drawer-body .s-row')]
        .filter(r => r.querySelector('.s-rowlabel') && visible(r));
    assert.equal(shown.length, 3, 'setup: "terminal font" shows rows outside Terminal font');
    assert.equal($('#settings-search-status').textContent, '3 settings', 'the live count is not the number of rows shown');
});

test('multi-word queries are AND, not OR', async () => {
    await openDrawer();
    type('remote phone');
    assert.equal(visible(rowOf('#remote-control-toggle')), true, '"remote phone" did not match Remote Control');
    type('remote zzqqxx');
    assert.equal(visible(rowOf('#remote-control-toggle')), false, 'a query with a non-matching word still matched (OR, not AND)');
    assert.equal($('#settings-search-status').textContent, "No settings match 'remote zzqqxx'");
});

test('Ctrl+, on a touch (pointer: coarse) screen opens Settings WITHOUT focusing the search box', async () => {
    coarsePointer = true;
    document.activeElement?.blur?.();
    assert.notEqual(document.activeElement, input(), 'setup: the search box is still focused from an earlier test');
    key({ key: ',', ctrlKey: true });
    await flush();
    assert.equal(settingsOpen(), true, 'Ctrl+, did not open Settings');
    assert.notEqual(document.activeElement, input(), 'the search box was focused on a touch screen (keyboard covers the drawer)');
});

test('⭐ MUST BE ACCEPTED: an empty query leaves the drawer byte-for-byte as rendered', async () => {
    await openDrawer();
    window.chela.selectSettingsTab('notifications');
    const snap = () => ({
        body: document.getElementById('drawer-body').innerHTML,
        tabs: document.getElementById('settings-tabs').innerHTML,
        modal: modal().className,
    });
    const before = snap();
    assert.deepEqual(activeTab(), ['notifications']);
    assert.ok(visible(rowOf('#run-toasts-select')), 'setup: the Notifications tab is not showing');
    type('   ');
    assert.deepEqual(snap(), before, 'a whitespace-only query changed the drawer');
    type('remote');
    assert.notDeepEqual(snap(), before, 'setup: "remote" did not filter anything');
    type('');
    assert.deepEqual(snap(), before, 'clearing the query did not leave the drawer exactly as rendered');
});

test('closing and reopening starts from an empty query, not a stale filter', async () => {
    await openDrawer();
    window.chela.selectSettingsTab('appearance');
    type('remote');
    window.chela.toggleSettings();
    await openDrawer();
    assert.equal(input().value, '', 'the query survived close/reopen');
    assert.equal(modal().classList.contains('searching'), false);
    assert.deepEqual(activeTab(), ['appearance']);
    assert.equal(visible(rowOf('#theme-select')), true, 'a stale filter hid the reopened tab');
});

test('picking a tab mid-search drops the query and goes to that tab', async () => {
    await openDrawer();
    type('remote');
    window.chela.selectSettingsTab('timing');
    assert.equal(input().value, '');
    assert.deepEqual(activeTab(), ['timing']);
    assert.equal(modal().classList.contains('searching'), false);
});
