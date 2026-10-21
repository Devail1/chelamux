// CMX-85 — FAST SWITCH LIKE THE CLAUDE DESKTOP APP, IN A REAL DOM.
//
// The desktop app shows a number beside each sidebar session while Ctrl is held; chela
// does it with Alt (Ctrl+1..9 is the browser's own tab switch). Holding Alt paints 1..9
// on the first nine VISIBLE session rows, top to bottom as drawn; Alt+N opens the Nth
// row's session, in every mode (not only the wall). The wall panes carry no number of
// their own any more (tests/wallnav.test.mjs, "the pane corner number is gone").
//
// Runs the REAL nav.js (renderSidebarAgents → sidebarmodel.js) into a REAL
// #sidebar-agents and drives it with real KeyboardEvents on the real document — the
// listener the page registers at import, not a handler called by hand. Terminals are
// off here, so opening a row lands on its agent-detail view, which marks that row
// `.active` — the observable a user sees.
//
//   1. 🔴 Alt down ⇒ 1..N on the visible rows in draw order; Alt up / blur ⇒ gone.
//   2. 🔴 A collapsed group's rows are skipped — by the numbers AND by Alt+N.
//      So are the Archived section's, even when it is drawn open.
//   3. 🔴 Alt+2 opens the 2nd visible row's session.
//   4. 🔴 Typing in a field, the palette open, or Ctrl/⌘ held with Alt ⇒ Alt+N does nothing.
//   5. 🔴 Only the first NINE visible rows are numbered — an 11-row sidebar shows 1..9
//      and rows 10/11 carry nothing (there is no Alt+10).
//   6. 🔴 A bare Alt keydown is NOT consumed (it must still reach a pane); Alt+N is.
//      Ctrl/⌘/Shift held with Alt is another chord: no numbers.
//
// Run: pnpm exec vitest run tests/sidebar_alt_switch.test.mjs  (pytest runs it via tests/test_js_suites.py)
import { beforeAll, beforeEach, test } from 'vitest';
import assert from 'node:assert/strict';
import { bootDashboardDom } from './js_helpers/dashboard_dom.mjs';
import { fakeArchiveServer } from './js_helpers/fake_archive.mjs';

const BODY = `
<aside class="sidebar">
  <div class="side-list" id="sidebar-agents"></div>
</aside>
<input id="some-field"><textarea id="some-area"></textarea><select id="some-select"><option>x</option></select>
<div class="palette-overlay" id="palette"><div class="palette"><input id="palette-input"><div id="palette-list"></div></div></div>
<div id="agent-detail"></div>
<span id="hdr-agents"></span><span id="hdr-next"></span><span id="hdr-updated"></span>`;

let nav, util;
const server = fakeArchiveServer(() => util && util._agentsCache);

beforeAll(async () => {
    ({ modules: { util, nav } } = await bootDashboardDom({
        body: BODY,
        canvasStub: true,
        terminalsEnabled: false,
        fetchImpl: (url, opts) => {
            const faked = server.handle(url, opts);
            if (faked !== undefined) return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(faked) });
            const body = /\/api\/(agents|summary)/.test(String(url)) ? [] : {};
            return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
        },
        extraModules: ['util.js', 'nav.js'],
    }));
});

let _wid = 100;
const win = (name, cwd) => ({
    name, cwd, window_id: `@${_wid++}`, online: true, claude_running: true, session_status: 'idle',
});
// Three folder groups — alpha (2 rows), beta (2 rows), gamma (1 row).
const ROWS = [
    win('a-one', '/srv/code/alpha'), win('a-two', '/srv/code/alpha'),
    win('b-one', '/srv/code/beta'), win('b-two', '/srv/code/beta'),
    win('g-one', '/srv/code/gamma'),
];

const host = () => document.getElementById('sidebar-agents');
// Every session row as drawn, top to bottom (collapsed or not).
const drawn = () => [...host().querySelectorAll('.agent-row[data-agent]')];
const inGroup = (row, key) => row.closest('.side-group').dataset.g === key;
// The numbers on screen, as [rowName, number] in DOM order.
const badges = () => drawn().filter(r => r.querySelector('.ar-altnum'))
    .map(r => [r.dataset.agent, r.querySelector('.ar-altnum').textContent]);
const activeRow = () => (host().querySelector('.agent-row.active') || {}).dataset?.agent || null;

const key = (type, init, target = document) =>
    target.dispatchEvent(new window.KeyboardEvent(type, { bubbles: true, cancelable: true, ...init }));
const altDown = () => key('keydown', { key: 'Alt', code: 'AltLeft', altKey: true });
const altUp = () => key('keyup', { key: 'Alt', code: 'AltLeft' });
const altDigit = (n, extra = {}, target) =>
    key('keydown', { key: String(n), code: `Digit${n}`, altKey: true, ...extra }, target);

beforeEach(async () => {
    localStorage.clear();
    server.reset();
    await nav.refreshArchive();
    await nav.refreshLayout();
    altUp();
    document.getElementById('palette').classList.remove('open');
    util.setAgentsCache(ROWS);
    nav.renderSidebarAgents(ROWS);
    host().querySelectorAll('.agent-row.active').forEach(r => r.classList.remove('active'));
});

test('holding Alt numbers the visible rows 1..N in draw order; releasing Alt removes them', () => {
    assert.equal(drawn().length, 5, 'fixture must draw five rows, or this test is vacuous');
    assert.deepEqual(badges(), [], 'no numbers may show before Alt is held');

    altDown();
    assert.deepEqual(badges(), drawn().map((r, i) => [r.dataset.agent, String(i + 1)]),
        'holding Alt must number every visible row 1..N, top to bottom as drawn');

    // A re-render while Alt is still held (a poll) keeps the numbers.
    nav.renderSidebarAgents(ROWS);
    assert.equal(badges().length, 5, 'a sidebar re-render while Alt is held dropped the numbers');

    altUp();
    assert.deepEqual(badges(), [], 'releasing Alt must hide the numbers');
});

test('blur and a visibility change hide the numbers too', () => {
    altDown();
    assert.equal(badges().length, 5);
    window.dispatchEvent(new window.Event('blur'));
    assert.deepEqual(badges(), [], 'the window losing focus (Alt+Tab) must hide the numbers');

    altDown();
    assert.equal(badges().length, 5);
    document.dispatchEvent(new window.Event('visibilitychange'));
    assert.deepEqual(badges(), [], 'the tab going hidden must hide the numbers');
});

test('Alt+2 opens the 2nd visible row\'s session', () => {
    const second = drawn()[1].dataset.agent;
    altDigit(2);
    assert.equal(activeRow(), second, 'Alt+2 must open the session on the 2nd row');
});

test('a collapsed group\'s rows are skipped by the numbers and by Alt+N', () => {
    window.chela.toggleGroup('/srv/code/beta');   // collapse beta
    const visible = drawn().filter(r => !inGroup(r, '/srv/code/beta'));
    assert.equal(visible.length, 3, 'fixture: collapsing beta must leave three visible rows');
    assert.ok(drawn().some(r => inGroup(r, '/srv/code/beta')), 'beta\'s rows must still be in the DOM, or the skip is untested');

    altDown();
    assert.deepEqual(badges(), visible.map((r, i) => [r.dataset.agent, String(i + 1)]),
        'a collapsed group\'s hidden rows must get no number, and must not use one up');

    altDigit(3);
    assert.equal(activeRow(), visible[2].dataset.agent,
        'Alt+3 must open the 3rd VISIBLE row, skipping the collapsed group\'s rows');
});

test('an OPEN Archived section\'s rows are skipped by the numbers and by Alt+N', async () => {
    // A hidden LIVE row: still a real window, so its archived row is drawn with
    // data-agent like any other, in a section that is NOT collapsed — only the
    // `.side-archived` exclusion keeps it out (without it, it would take number 5).
    const hiddenRow = ROWS[0];
    server.st.hidden.push(`w:${hiddenRow.window_id}`);
    await nav.refreshArchive();
    // View ▸ Status ▸ All (the default since CMX-277) draws the section
    window.chela.toggleArchivedSection();    // …and OPEN, so .side-group.collapsed cannot hide it
    const sec = host().querySelector('.side-archived');
    assert.ok(sec && !sec.classList.contains('collapsed'), 'fixture: the Archived section must be drawn open');
    const archivedRows = [...sec.querySelectorAll('.agent-row[data-agent]')];
    assert.deepEqual(archivedRows.map(r => r.dataset.agent), [hiddenRow.name],
        'fixture: the hidden window must be drawn as an Archived row with data-agent, or the skip is untested');
    const visible = drawn().filter(r => !r.closest('.side-archived'));
    assert.equal(visible.length, 4, 'fixture: four rows outside Archived');

    altDown();
    assert.deepEqual(badges(), visible.map((r, i) => [r.dataset.agent, String(i + 1)]),
        'an Archived row must get no number, and must not use one up');

    // The re-renders above re-mark whatever row an earlier test opened, so compare
    // against what is active now rather than against null.
    const before = activeRow();
    assert.notEqual(before, hiddenRow.name);
    altDigit(5);
    assert.equal(activeRow(), before, 'Alt+5 must not reach the Archived row (only four visible rows)');
});

test('Alt+N does nothing while typing in a field, with the palette open, or with Ctrl held', () => {
    const field = document.getElementById('some-field');
    altDigit(1, {}, field);
    assert.equal(activeRow(), null, 'Alt+1 typed into an input must not open a row');

    document.getElementById('palette').classList.add('open');
    altDigit(1);
    assert.equal(activeRow(), null, 'Alt+1 with the palette open must not open a row');
    document.getElementById('palette').classList.remove('open');

    altDigit(1, {}, document.getElementById('some-area'));
    assert.equal(activeRow(), null, 'Alt+1 typed into a textarea must not open a row');
    altDigit(1, {}, document.getElementById('some-select'));
    assert.equal(activeRow(), null, 'Alt+1 on a focused select must not open a row');

    altDigit(1, { ctrlKey: true });
    assert.equal(activeRow(), null, 'Ctrl+Alt+1 must not open a row');
    altDigit(1, { metaKey: true });
    assert.equal(activeRow(), null, '⌘+Alt+1 must not open a row');

    altDigit(1);   // the control: the same key with nothing in the way DOES open
    assert.equal(activeRow(), drawn()[0].dataset.agent, 'Alt+1 must open the 1st row');
});

test('Alt+N past the last visible row does nothing', () => {
    altDigit(9);
    assert.equal(activeRow(), null);
});

test('only the first NINE visible rows are numbered — rows 10 and 11 carry nothing', () => {
    const many = Array.from({ length: 11 }, (_, i) => win(`m-${String(i + 1).padStart(2, '0')}`, '/srv/code/many'));
    util.setAgentsCache(many);
    nav.renderSidebarAgents(many);
    try {
        assert.equal(drawn().length, 11, 'fixture must draw eleven visible rows, or the cap is untested');
        altDown();
        const want = drawn().slice(0, 9).map((r, i) => [r.dataset.agent, String(i + 1)]);
        assert.deepEqual(badges(), want,
            'holding Alt must number exactly the first nine rows 1..9 — a 10th/11th row has no Alt+N to show');
        assert.equal(host().querySelectorAll('.ar-altnum').length, 9, 'exactly nine numbers may be on screen');
        drawn().slice(9).forEach(r => assert.equal(r.querySelector('.ar-altnum'), null,
            `row ${r.dataset.agent} past the ninth must carry no number`));
        assert.equal(drawn()[0].querySelector('.ar-altnum').getAttribute('aria-hidden'), 'true',
            'the number is a visual hint only — screen readers must not read it as part of the row name');

        altDigit(9);
        assert.equal(activeRow(), drawn()[8].dataset.agent, 'Alt+9 must open the 9th row');
    } finally {
        altUp();
        util.setAgentsCache(ROWS);
        nav.renderSidebarAgents(ROWS);
    }
});

test('a bare Alt keydown is not consumed; Alt+N is; Alt with another modifier shows no numbers', () => {
    const altEv = new window.KeyboardEvent('keydown', { key: 'Alt', code: 'AltLeft', altKey: true, bubbles: true, cancelable: true });
    document.dispatchEvent(altEv);
    assert.equal(altEv.defaultPrevented, false, 'Alt itself must not be preventDefault-ed — it still belongs to whatever has focus');
    assert.equal(badges().length, 5, 'the bare Alt press must show the numbers');

    const digitEv = new window.KeyboardEvent('keydown', { key: '1', code: 'Digit1', altKey: true, bubbles: true, cancelable: true });
    document.dispatchEvent(digitEv);
    assert.equal(digitEv.defaultPrevented, true, 'Alt+1 opened a row, so the browser must not also act on it');
    altUp();

    for (const mod of ['ctrlKey', 'metaKey', 'shiftKey']) {
        key('keydown', { key: 'Alt', code: 'AltLeft', altKey: true, [mod]: true });
        assert.deepEqual(badges(), [], `Alt pressed with ${mod} held is another chord — no numbers`);
        altUp();
    }
});
