// CMX-169 - THE PHONE OPENS THE PANE THIS DEVICE LAST VIEWED, ELSE THE TOP SIDEBAR ROW.
//
// Liav, 2026-10-17: single mode (forced on phones) opened #term-agent's first option, the
// first /api/agents row, on every load: neither remembered nor in the sidebar's order. Now
// a load opens the last pane viewed on this device (localStorage `pc_term_last_wid`) if it
// is still online, else the first session in the sidebar's order, else the first option.
//
// The real dashboard boots in jsdom at phone width with a stored pane (@203, the LAST row
// of /api/agents and of the sidebar), so the boot itself is the first guard. A "reload" in
// the other tests empties #term-agent and re-renders: the same path a fresh page takes.
// The sidebar's order is never /api/agents' order here, so a default that follows the
// rows fails.
//
// Run: pnpm exec vitest run tests/term_last_pane.test.mjs  (pytest runs it via tests/test_js_suites.py)
import { beforeAll, beforeEach, test } from 'vitest';
import assert from 'node:assert/strict';
import { clickIsland } from './js_helpers/dashboard_dom.mjs';
import { KEY, bootPhone, pane, sel, settle, shown, stored } from './js_helpers/term_pane_fixture.mjs';

const SIDEBAR = ['/srv/code/beta', '/srv/code/gamma', '/srv/code/alpha'];   // sidebar rows: @202, @203, @201
let nav, sidebar, terminals, chela, bootPane;
const storedWid = () => JSON.parse(localStorage.getItem(KEY)).wid;
async function reload() {
    sel().innerHTML = '';
    await terminals.renderTerminals();
    await settle();
}

beforeAll(async () => {
    ({ nav, sidebar, terminals, chela } = await bootPhone({ order: SIDEBAR, seed: { [KEY]: stored('@203', 's-c', 'c') } }));
    await settle();
    bootPane = pane();
});

beforeEach(async () => {
    await sidebar.refreshSidebar();
    await settle();
});

test('a stored pane that is online opens on load, not the first row', () => {
    assert.deepEqual(chela.sidebarOrder(), ['@202', '@203', '@201']);
    assert.deepEqual(bootPane, { value: '@203', shown: '@203' });
});

test('a stored pane that is offline opens the top sidebar row, not the first row', async () => {
    localStorage.setItem(KEY, stored('@299', 's-z', 'z'));
    await reload();
    assert.deepEqual(pane(), { value: '@202', shown: '@202' });
    assert.equal(storedWid(), '@202', 'the pane that opened is now the stored one');
});

test('a wid a renumber handed to another session is not trusted', async () => {
    localStorage.setItem(KEY, stored('@203', 's-old', 'old'));
    await reload();
    assert.equal(pane().value, '@202');
});

test('the same wid and name with a new session id (a /clear) still opens', async () => {
    localStorage.setItem(KEY, stored('@203', 's-old', 'c'));
    await reload();
    assert.equal(pane().value, '@203');
});

test('a stored wid with no name or session id to compare is trusted', async () => {
    localStorage.setItem(KEY, JSON.stringify({ wid: '@201' }));
    await reload();
    assert.equal(pane().value, '@201');
});

test('a sidebar tap stores its pane, and a reload opens it', async () => {
    localStorage.setItem(KEY, stored('@202', 's-b', 'b'));
    await reload();
    clickIsland(document.querySelector('#sidebar-agents .agent-row[data-item="w:@201"]'));
    await settle();
    assert.equal(shown(), '@201');
    assert.deepEqual(JSON.parse(localStorage.getItem(KEY)), { wid: '@201', session_id: 's-a', name: 'a' });
    await reload();
    assert.deepEqual(pane(), { value: '@201', shown: '@201' });
});

test('an explicit focus overrides the stored pane and becomes it', async () => {
    localStorage.setItem(KEY, stored('@202', 's-b', 'b'));
    await reload();
    terminals.focusPaneByWid('@203');
    await settle();
    assert.deepEqual(pane(), { value: '@203', shown: '@203' });
    assert.equal(storedWid(), '@203');
    await reload();
    assert.equal(pane().value, '@203');
});

test('a focus that lands before the pane list (a deep link at boot) is what opens', async () => {
    localStorage.setItem(KEY, stored('@202', 's-b', 'b'));
    sel().innerHTML = '';
    terminals.focusPaneByWid('@201');   // no options yet: the select cannot hold it
    await reload();
    assert.deepEqual(pane(), { value: '@201', shown: '@201' });
});

test('localStorage throwing still renders a pane (the top sidebar row)', async () => {
    const proto = Object.getPrototypeOf(localStorage);
    const { getItem, setItem } = proto;
    const boom = () => { throw new DOMException('private mode', 'SecurityError'); };
    proto.getItem = function (k) { return k === KEY ? boom() : getItem.call(this, k); };
    proto.setItem = function (k, v) { return k === KEY ? boom() : setItem.call(this, k, v); };
    try {
        await reload();
        assert.deepEqual(pane(), { value: '@202', shown: '@202' });
        clickIsland(document.querySelector('#sidebar-agents .agent-row[data-item="w:@203"]'));
        await settle();
        assert.equal(shown(), '@203', 'a switch still works when the write throws');
    } finally {
        proto.getItem = getItem;
        proto.setItem = setItem;
    }
});

test('a corrupt stored value falls through to the top sidebar row', async () => {
    localStorage.setItem(KEY, '{not json');
    await reload();
    assert.equal(pane().value, '@202');
});
