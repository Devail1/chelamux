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
//
// Run: node --test tests/sidebar_alt_switch.test.mjs  (pytest runs it via tests/test_js_suites.py)
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { bootDashboardDom } from './js_helpers/dashboard_dom.mjs';
import { fakeArchiveServer } from './js_helpers/fake_archive.mjs';

const BODY = `
<aside class="sidebar">
  <div class="side-list" id="sidebar-agents"></div>
</aside>
<input id="some-field">
<div class="palette-overlay" id="palette"><div class="palette"><input id="palette-input"><div id="palette-list"></div></div></div>
<div id="agent-detail"></div>
<span id="hdr-agents"></span><span id="hdr-next"></span><span id="hdr-updated"></span>`;

let nav, util;
const server = fakeArchiveServer(() => util && util._agentsCache);

before(async () => {
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
    window.chela.toggleShowArchived();       // View ▸ Status ▸ All — the section is drawn
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

    altDigit(1, { ctrlKey: true });
    assert.equal(activeRow(), null, 'Ctrl+Alt+1 must not open a row');

    altDigit(1);   // the control: the same key with nothing in the way DOES open
    assert.equal(activeRow(), drawn()[0].dataset.agent, 'Alt+1 must open the 1st row');
});

test('Alt+N past the last visible row does nothing', () => {
    altDigit(9);
    assert.equal(activeRow(), null);
});
