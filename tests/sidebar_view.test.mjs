// CMX-66 — THE SIDEBAR'S VIEW MENU, IN A REAL DOM (part 2 of CMX-35's sidebar).
//
// The sliders button on the Sessions header opens the desktop's VIEW menu: Status /
// Environment / Last activity filter the rows, Group by picks Date / Folder / State /
// Custom groups / None, Sort by orders each group, and two toggles show empty groups and
// PR badges. These run the REAL nav.js (renderSidebarAgents → sidebarmodel.js) into a
// REAL #sidebar-agents, drive the REAL menu (the button's inline handler, the popover's
// own click handler) and read the result back off the DOM.
//
// Run: node --test tests/sidebar_view.test.mjs  (pytest runs it via tests/test_js_suites.py)
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { bootDashboardDom } from './js_helpers/dashboard_dom.mjs';
import {
    DISPATCHED_KEY, VIEW_DEFAULTS, dateBucket, groupSidebar, isLive, itemKinds, buildItems, normalizeView, windowKind,
} from '../chela/dashboard/static/js/sidebarmodel.js';

const BODY = `
<aside class="sidebar">
  <div class="side-head"><span class="side-title">Sessions</span>
    <button class="side-view-btn" id="btn-sidebar-view" onclick="chela.openViewMenu(event)"></button></div>
  <div class="side-list" id="sidebar-agents"></div>
</aside>
<div id="agent-detail"></div>
<span id="hdr-agents"></span><span id="hdr-next"></span><span id="hdr-updated"></span>`;

let nav, util;

before(async () => {
    ({ modules: { util, nav } } = await bootDashboardDom({
        body: BODY,
        canvasStub: true,
        terminalsEnabled: false,
        fetchImpl: url => {
            const body = /\/api\/(agents|summary)/.test(String(url)) ? [] : {};
            return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
        },
        extraModules: ['util.js', 'nav.js'],
    }));
});

beforeEach(() => {
    localStorage.clear();
    // a choice is also kept in memory (for a throwing storage) — reset it to the defaults
    nav.viewMenuAction('group:folder');
    localStorage.clear();
    window.chela.hideSideMenus();
});

const wants = a => !!a && (a.needs_human === true || a.session_status === 'waiting');
const DAY = 86400000;
const ago = ms => new Date(Date.now() - ms).toISOString();

let _wid = 500;
// An idle Claude window, last active an hour ago unless told otherwise.
const win = (name, over = {}) => ({
    name, window_id: `@${_wid++}`, online: true, claude_running: true, session_status: 'idle',
    cwd: '/srv/code/alpha', last_activity: ago(3600e3), ...over,
});
const WT = '/srv/.chela/worktrees/chelamux';
const runCard = (task, title, role, over = {}) => ({ task_id: task, title, status: 'running', judge_state: '', role, ...over });

function render(rows) {
    util.setAgentsCache(rows);
    nav.renderSidebarAgents(rows);
}
const host = () => document.getElementById('sidebar-agents');
const shown = () => [...host().querySelectorAll('.agent-row')].map(r => r.dataset.agent);
const groupKeys = () => [...host().querySelectorAll('.side-group')].map(g => g.dataset.g);
const group = key => [...host().querySelectorAll('.side-group')].find(g => g.dataset.g === key) || null;
const rowsIn = key => [...group(key).querySelectorAll('.agent-row')].map(r => r.dataset.agent);
const groupLabels = () => [...host().querySelectorAll('.side-group .group-name')].map(n => n.textContent);
const groupByLabel = label => [...host().querySelectorAll('.side-group')]
    .find(g => (g.querySelector('.group-name') || {}).textContent === label) || null;
const rowsUnder = label => [...groupByLabel(label).querySelectorAll('.agent-row')].map(r => r.dataset.agent);
const view = act => nav.viewMenuAction(act);

function fire(el, attr = 'onclick') {
    assert.ok(el, `no element to fire ${attr} on`);
    const code = el.getAttribute(attr);
    assert.ok(code, `the element has no ${attr} attribute`);
    const ev = { target: el, currentTarget: el, stopPropagation() {}, preventDefault() {} };
    return new Function('chela', 'event', code).call(el, window.chela, ev);
}
const menu = id => document.getElementById(id);
const menuRows = id => [...menu(id).querySelectorAll('.popover-item')].map(i => ({
    label: (i.querySelector('.vm-label') || i).textContent.trim(),
    value: (i.querySelector('.vm-val') || { textContent: '' }).textContent.trim(),
    chevron: !!i.querySelector('.vm-chev:not(.back)'),
    checked: !!i.querySelector('.vm-check'),
}));
// A TAP on a menu row: the popover's own click handler, as a real click delivers it.
function tap(id, label) {
    const el = [...menu(id).querySelectorAll('[data-act]')]
        .find(i => ((i.querySelector('.vm-label') || i).textContent.trim()) === label);
    assert.ok(el, `no "${label}" in #${id}: ${JSON.stringify(menuRows(id).map(r => r.label))}`);
    menu(id).onclick({ target: el, stopPropagation() {} });
}

// --- the menu itself ---------------------------------------------------------------

test('the view menu opens from the header button, shows each submenu\'s value + chevron, and submenus open by TAP', () => {
    render([win('a')]);
    fire(document.getElementById('btn-sidebar-view'));
    assert.equal(menu('view-menu').style.display, 'block');
    const top = Object.fromEntries(menuRows('view-menu').map(r => [r.label, r]));
    assert.deepEqual(['Status', 'Environment', 'Last activity', 'Group by', 'Sort by'].map(l => [top[l].value, top[l].chevron]),
        [['Active', true], ['3 selected', true], ['7d', true], ['Folder', true], ['Last activity', true]]);
    assert.equal(top['Show PR status'].checked, true, 'Show PR status defaults ON');
    assert.equal(top['Show empty groups'].checked, false);
    // a tap drills into the submenu (no hover flyout), the current choice ✓
    tap('view-menu', 'Group by');
    assert.deepEqual(menuRows('view-menu').filter(r => r.checked).map(r => r.label), ['Folder']);
    assert.deepEqual(menuRows('view-menu').map(r => r.label), ['Group by', 'Date', 'Folder', 'State', 'Custom groups', 'None']);
    tap('view-menu', 'State');
    assert.equal(menu('view-menu').style.display, 'block', 'a choice keeps the menu open');
    assert.equal(menuRows('view-menu').find(r => r.label === 'Group by').value, 'State', 'back on top, the new value shows');
    assert.ok(groupLabels().includes('Idle'), 'the sidebar regrouped by State');
    // the multi-select stays on its page; its header counts the selection
    tap('view-menu', 'Environment');
    tap('view-menu', 'Judges');
    assert.ok(menuRows('view-menu').find(r => r.label === 'Judges').checked);
    tap('view-menu', 'Environment');   // the ‹ back row
    assert.equal(menuRows('view-menu').find(r => r.label === 'Environment').value, '4 selected');
});

// --- Status ---------------------------------------------------------------------------

test('Status: Active hides archived rows, All shows both, Archived shows only them', () => {
    const rows = [win('live'), win('finished', { done: true })];
    render(rows);
    nav.groupMenuAction('/srv/code/alpha', 'archive');
    assert.deepEqual(shown(), ['live']);
    view('status:all');
    assert.deepEqual(shown().sort(), ['finished', 'live']);
    view('status:archived');
    assert.deepEqual(shown(), ['finished']);
    view('status:active');
    assert.deepEqual(shown(), ['live']);
});

// --- Environment -------------------------------------------------------------------------

test('Environment: each kind filters its rows; the default is everything except Judges', () => {
    const rows = [
        win('human'),
        win('bg', { session_moved: true }),
        win('judge-lone', { dispatched: true }),
        win('liavacc/cmx-9-x', { cwd: `${WT}/CMX-9`, dispatched: true, run: runCard('CMX-9', 'X', 'agent') }),
    ];
    render(rows);
    assert.deepEqual(shown().sort(), ['bg', 'human', 'liavacc/cmx-9-x'], 'the default hides the lone judge window');
    view('env:judge');
    assert.ok(shown().includes('judge-lone'));
    view('env:interactive');
    assert.ok(!shown().includes('human'), 'deselecting Interactive must hide the interactive row');
    assert.ok(shown().includes('bg'), 'a background session is its own kind, not Interactive');
    view('env:background');
    assert.ok(!shown().includes('bg'));
    view('env:dispatched');
    assert.ok(!shown().includes('liavacc/cmx-9-x'));
    assert.deepEqual(shown(), ['judge-lone']);
});

test('Environment never hides a run\'s ONE row for its judge, and never hides a row blocked on you', () => {
    render([
        // a run whose agent finished: only its judge window is left — still the run's row
        win('judge-liavacc/cmx-40-x', { cwd: `${WT}/judge-CMX-40`, dispatched: true,
            run: runCard('CMX-40', 'J', 'judge', { status: 'awaiting_review', judge_state: 'running' }) }),
        win('blocked', { session_status: 'waiting' }),
    ]);
    view('env:interactive');   // off
    assert.deepEqual(shown().sort(), ['blocked', 'judge-liavacc/cmx-40-x']);
});

test('model: window kinds — judge by role or name, dispatched, background, interactive', () => {
    assert.equal(windowKind({ name: 'judge-x' }), 'judge');
    assert.equal(windowKind({ name: 'w', run: { role: 'judge' } }), 'judge');
    assert.equal(windowKind({ name: 'w', dispatched: true, session_moved: true }), 'dispatched');
    assert.equal(windowKind({ name: 'w', session_moved: true }), 'background');
    assert.equal(windowKind({ name: 'w' }), 'interactive');
    const [run] = buildItems([{ name: 'judge-y', window_id: '@1', run: { task_id: 'T', role: 'judge' } }]);
    assert.deepEqual([...itemKinds(run)].sort(), ['dispatched', 'judge']);
    assert.deepEqual(normalizeView({ env: ['judge', 'bogus'] }).env, ['judge']);
    assert.deepEqual(normalizeView(null), { ...VIEW_DEFAULTS, env: [...VIEW_DEFAULTS.env] });
});

// --- Last activity -------------------------------------------------------------------------

test('Last activity: 1d / 7d / 30d / All filter by the row\'s last activity', () => {
    render([
        win('hour', { last_activity: ago(3600e3) }),
        win('days3', { last_activity: ago(3 * DAY) }),
        win('days10', { last_activity: ago(10 * DAY) }),
        win('days60', { last_activity: ago(60 * DAY) }),
        win('undated', { last_activity: null }),
    ]);
    assert.deepEqual(shown().sort(), ['days3', 'hour', 'undated'], '7d is the default; an undated row is not hidden');
    view('activity:1d');
    assert.deepEqual(shown().sort(), ['hour', 'undated']);
    view('activity:30d');
    assert.deepEqual(shown().sort(), ['days10', 'days3', 'hour', 'undated']);
    view('activity:all');
    assert.equal(shown().length, 5);
});

test('Last activity NEVER hides a running or a waiting session, however old its activity reads', () => {
    const old = ago(90 * DAY);
    render([
        win('running', { session_status: 'busy', last_activity: old }),
        win('waiting', { session_status: 'waiting', last_activity: old }),
        win('battery', { name: 'judge-z', judge_battery: { state: 'testing' }, last_activity: old }),
        win('liavacc/cmx-5-y', { cwd: `${WT}/CMX-5`, dispatched: true, last_activity: old, run: runCard('CMX-5', 'Y', 'agent') }),
        win('stale', { last_activity: old }),
    ]);
    view('env:judge');
    view('activity:1d');
    assert.deepEqual(shown().sort(), ['judge-z', 'liavacc/cmx-5-y', 'running', 'waiting']);
    const items = buildItems([{ name: 'r', session_status: 'busy' }]);
    assert.equal(isLive(items[0], wants), true);
});

// --- Group by -----------------------------------------------------------------------------

function localMidnight() { const d = new Date(); return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime(); }

test('Group by Date: Today / Yesterday / This week / Older', () => {
    const t0 = localMidnight();
    render([
        win('today', { last_activity: new Date(Date.now() - 1000).toISOString() }),
        win('yesterday', { last_activity: new Date(t0 - DAY / 2).toISOString() }),
        win('thisweek', { last_activity: new Date(t0 - 4 * DAY).toISOString() }),
        win('older', { last_activity: new Date(t0 - 20 * DAY).toISOString() }),
        win('busy-old', { session_status: 'busy', last_activity: new Date(t0 - 20 * DAY).toISOString() }),
    ]);
    view('activity:all');
    view('group:date');
    assert.deepEqual(groupLabels(), ['Today', 'Yesterday', 'This week', 'Older']);
    assert.deepEqual(rowsUnder('Today').sort(), ['busy-old', 'today'], 'a working row is active today');
    assert.deepEqual(rowsUnder('Yesterday'), ['yesterday']);
    assert.deepEqual(rowsUnder('This week'), ['thisweek']);
    assert.deepEqual(rowsUnder('Older'), ['older']);
    assert.equal(dateBucket(null, Date.now()), '~date:older');
});

test('Group by Folder is the default (part 1\'s groups)', () => {
    render([win('a1'), win('b1', { cwd: '/srv/code/beta' })]);
    assert.deepEqual(groupKeys(), ['/srv/code/alpha', '/srv/code/beta']);
    view('group:state');
    view('group:folder');
    assert.deepEqual(groupKeys(), ['/srv/code/alpha', '/srv/code/beta']);
});

test('Group by State: Working / Needs you / Idle / Completed — the orchestrator stays Pinned', () => {
    render([
        win('busy', { session_status: 'busy' }),
        win('blocked', { session_status: 'waiting' }),
        win('idle'),
        win('finished', { done: true }),
        win('liavacc/cmx-3-z', { cwd: `${WT}/CMX-3`, dispatched: true, run: runCard('CMX-3', 'Z', 'agent', { status: 'done' }) }),
    ]);
    view('group:state');
    assert.deepEqual(groupLabels(), ['Working', 'Needs you', 'Idle', 'Completed']);
    assert.deepEqual(rowsUnder('Working'), ['busy']);
    assert.deepEqual(rowsUnder('Needs you'), ['blocked']);
    assert.deepEqual(rowsUnder('Idle'), ['idle']);
    assert.deepEqual(rowsUnder('Completed').sort(), ['finished', 'liavacc/cmx-3-z']);
    assert.equal(host().querySelector('.side-needs-you'), null, 'in State mode, Needs you is a group, not a second cluster');
    // the orchestrator (the decisions-inbox holder) keeps its Pinned mark in State mode
    const m = groupSidebar([{ name: 'orch', window_id: '@1', session_status: 'busy' }, { name: 'w', window_id: '@2' }],
        { wants, mode: 'state', orchWid: '@1' });
    assert.deepEqual(m.pinned.map(i => i.agent.name), ['orch']);
    assert.ok(m.groups.every(g => g.items.every(i => i.agent.name !== 'orch')));
});

test('Group by None: one flat list, no headers; a run is still ONE row', () => {
    render([
        win('a1'), win('b1', { cwd: '/srv/code/beta' }),
        win('liavacc/cmx-37-t', { cwd: `${WT}/CMX-37`, dispatched: true, run: runCard('CMX-37', 'T', 'agent') }),
        win('judge-liavacc/cmx-37-t', { cwd: `${WT}/judge-CMX-37`, dispatched: true, run: runCard('CMX-37', 'T', 'judge') }),
    ]);
    view('group:none');
    assert.equal(host().querySelectorAll('.group-head').length, 0);
    assert.deepEqual(shown().sort(), ['a1', 'b1', 'liavacc/cmx-37-t']);
});

test('in EVERY Group-by mode a dispatched run is ONE row', () => {
    const rows = [
        win('liavacc/cmx-37-t', { cwd: `${WT}/CMX-37`, dispatched: true, run: runCard('CMX-37', 'T', 'agent') }),
        win('judge-liavacc/cmx-37-t', { cwd: `${WT}/judge-CMX-37`, dispatched: true, run: runCard('CMX-37', 'T', 'judge') }),
    ];
    for (const mode of ['date', 'folder', 'state', 'custom', 'none']) {
        render(rows);
        view(`group:${mode}`);
        view('env:judge');   // even with Judges selected
        assert.equal(host().querySelectorAll('.agent-row[data-run="CMX-37"]').length, 1, `mode ${mode}`);
        assert.equal(shown().length, 1, `mode ${mode}: ${shown()}`);
        view('env:judge');
    }
});

test('Group by Custom groups: create, assign via the row menu, unassigned → Ungrouped, rename and delete', () => {
    const rows = [win('a1'), win('b1')];
    render(rows);
    view('group:custom');
    assert.deepEqual(groupLabels(), ['Ungrouped']);
    const g = nav.newCustomGroup('Infra');
    // assign through the row menu: ⋯ → Move to group… → Infra
    fire(host().querySelector('.agent-row[data-agent="a1"] .row-more'));
    tap('row-menu', 'Move to group…');
    tap('row-menu', 'Infra');
    assert.deepEqual(groupLabels(), ['Infra', 'Ungrouped']);
    assert.deepEqual(rowsUnder('Infra'), ['a1']);
    assert.deepEqual(rowsUnder('Ungrouped'), ['b1']);
    nav.renameCustomGroup(g.id, 'Platform');
    assert.deepEqual(rowsUnder('Platform'), ['a1']);
    nav.deleteCustomGroup(g.id);
    assert.deepEqual(groupLabels(), ['Ungrouped']);
    assert.deepEqual(rowsUnder('Ungrouped').sort(), ['a1', 'b1'], 'deleting a group moves its rows, never hides them');
    assert.deepEqual(JSON.parse(localStorage.getItem('chela_sb_custom_groups')).assign, {},
        'a deleted group must not leave assignments behind (a group re-created later would re-capture them)');
});

test('a custom group SURVIVES a window rename — it is keyed on the window id, not the label', () => {
    const a = win('old-name', { ai_title: 'Old title' });
    render([a, win('other')]);
    view('group:custom');
    const g = nav.newCustomGroup('Keep');
    nav.moveToGroup(`w:${a.window_id}`, g.id);
    assert.deepEqual(rowsUnder('Keep'), ['old-name']);
    // the same window, renamed by hand AND retitled by Claude
    render([{ ...a, name: 'new-name', manual_name: true, ai_title: 'New title' }, win('other')]);
    assert.deepEqual(rowsUnder('Keep'), ['new-name'], 'the renamed window fell out of its custom group');
});

test('a custom-group assignment is dropped when its WINDOW is gone (a recycled @N must not inherit it); a run\'s is kept', () => {
    const a = win('gone-soon');
    const r = win('liavacc/cmx-8-q', { cwd: `${WT}/CMX-8`, dispatched: true, run: runCard('CMX-8', 'Q', 'agent') });
    render([a, r]);
    const g = nav.newCustomGroup('G');
    nav.moveToGroup(`w:${a.window_id}`, g.id);
    nav.moveToGroup('run:CMX-8', g.id);
    render([win('other')]);   // both windows closed
    const assign = JSON.parse(localStorage.getItem('chela_sb_custom_groups')).assign;
    assert.deepEqual(assign, { 'run:CMX-8': g.id });
});

test('the custom-groups page in the view menu creates, renames and deletes (prompted names)', () => {
    render([win('a1')]);
    const realPrompt = window.prompt;
    const answers = ['Ops', 'Ops 2'];
    window.prompt = () => answers.shift();
    try {
        fire(document.getElementById('btn-sidebar-view'));
        tap('view-menu', 'Custom groups');
        tap('view-menu', 'New group…');
        assert.ok(menuRows('view-menu').some(r => r.label.startsWith('Ops')));
        menu('view-menu').onclick({ target: menu('view-menu').querySelector('[data-act^="rename:"]'), stopPropagation() {} });
        assert.ok(menu('view-menu').textContent.includes('Ops 2'));
        menu('view-menu').onclick({ target: menu('view-menu').querySelector('[data-act^="delete:"]'), stopPropagation() {} });
        assert.equal(menu('view-menu').querySelector('[data-act^="delete:"]'), null);
    } finally { window.prompt = realPrompt; }
});

// --- Sort by ---------------------------------------------------------------------------

test('Sort by: Last activity (default, newest first) / Name / Created (newest first) within each group', () => {
    render([
        win('b-mid', { last_activity: ago(2 * 3600e3), created: ago(5 * DAY) }),
        win('a-old', { last_activity: ago(5 * 3600e3), created: ago(1 * DAY) }),
        win('c-new', { last_activity: ago(60e3), created: ago(3 * DAY) }),
    ]);
    assert.deepEqual(rowsIn('/srv/code/alpha'), ['c-new', 'b-mid', 'a-old']);
    view('sort:name');
    assert.deepEqual(rowsIn('/srv/code/alpha'), ['a-old', 'b-mid', 'c-new']);
    view('sort:created');
    assert.deepEqual(rowsIn('/srv/code/alpha'), ['a-old', 'c-new', 'b-mid']);
});

// --- toggles ------------------------------------------------------------------------------

test('Show empty groups: a folder whose sessions are all filtered out keeps its header and its "+"', () => {
    render([win('a1'), win('stale', { cwd: '/srv/code/beta', last_activity: ago(40 * DAY) })]);
    assert.deepEqual(groupKeys(), ['/srv/code/alpha']);
    view('toggle:showEmpty');
    assert.deepEqual(groupKeys(), ['/srv/code/alpha', '/srv/code/beta']);
    assert.ok(group('/srv/code/beta').querySelector('.group-add'), 'the empty group\'s + must stay reachable');
    assert.deepEqual(rowsIn('/srv/code/beta'), []);
    view('toggle:showEmpty');
    assert.deepEqual(groupKeys(), ['/srv/code/alpha']);
    // the same toggle in the fixed-set groupings: State's four groups, filled or not
    view('group:state');
    assert.deepEqual(groupLabels(), ['Idle']);
    view('toggle:showEmpty');
    assert.deepEqual(groupLabels(), ['Working', 'Needs you', 'Idle', 'Completed']);
});

test('Show PR status: a PR badge on dispatched-run rows (with its state), hidden when toggled off', () => {
    const pr = 'https://github.com/o/r/pull/631';
    render([
        win('liavacc/cmx-35-s', { cwd: `${WT}/CMX-35`, dispatched: true,
            run: runCard('CMX-35', 'Sidebar', 'agent', { status: 'awaiting_review', pr_url: pr }),
            pr: { url: pr, number: 631, state: 'merged', draft: false } }),
        win('plain', { pr: { url: 'https://github.com/o/r/pull/7', state: 'open' } }),
    ]);
    const badge = () => host().querySelector('.agent-row[data-run="CMX-35"] .ar-pr');
    assert.ok(badge(), 'no PR badge on the run row');
    assert.equal(badge().textContent, '#631');
    assert.ok(badge().classList.contains('merged'));
    assert.match(badge().getAttribute('title'), /PR #631 · merged/);
    assert.equal(host().querySelector('.agent-row[data-agent="plain"] .ar-pr'), null, 'badges are for run rows');
    view('toggle:showPR');
    assert.equal(badge(), null);
    view('toggle:showPR');
    assert.ok(badge());
});

// --- persistence ---------------------------------------------------------------------------

test('choices persist per viewer and are read back on the next render', () => {
    render([win('a1'), win('b1', { cwd: '/srv/code/beta' })]);
    view('group:none');
    view('sort:name');
    view('env:judge');
    const stored = JSON.parse(localStorage.getItem('chela_sb_view'));
    assert.equal(stored.groupBy, 'none');
    assert.equal(stored.sort, 'name');
    assert.ok(stored.env.includes('judge'));
    render([win('a1'), win('b1', { cwd: '/srv/code/beta' })]);
    assert.equal(host().querySelectorAll('.group-head').length, 0, 'a fresh render forgot Group by ▸ None');
    // a garbage stored view falls back to the defaults, field by field
    localStorage.setItem('chela_sb_view', '{"groupBy":"galaxy","sort":"name"}');
    render([win('a1')]);
    assert.deepEqual(groupKeys(), ['/srv/code/alpha']);
});

test('a THROWING localStorage still renders, and the view menu still works for the session', () => {
    const real = globalThis.localStorage;
    const boom = () => { throw new Error('storage blocked'); };
    Object.defineProperty(globalThis, 'localStorage', {
        value: { getItem: boom, setItem: boom, removeItem: boom, clear: boom }, writable: true, configurable: true,
    });
    try {
        render([win('a1'), win('b1', { cwd: '/srv/code/beta' })]);
        assert.deepEqual(groupKeys(), ['/srv/code/alpha', '/srv/code/beta']);
        view('group:none');
        assert.equal(host().querySelectorAll('.group-head').length, 0);
        render([win('a1')]);
        assert.equal(host().querySelectorAll('.group-head').length, 0, 'the in-memory copy must carry the choice');
        view('group:folder');
        fire(document.getElementById('btn-sidebar-view'));
        assert.equal(menu('view-menu').style.display, 'block');
    } finally {
        Object.defineProperty(globalThis, 'localStorage', { value: real, writable: true, configurable: true });
    }
});

// --- the pure model directly ---------------------------------------------------------------

test('model: the filters never hide the pinned orchestrator or a row blocked on you', () => {
    const orch = { name: 'orch', window_id: '@1', cwd: '/x', session_moved: false, last_activity: ago(99 * DAY) };
    const blocked = { name: 'b', window_id: '@2', cwd: '/x', session_status: 'waiting', last_activity: ago(99 * DAY) };
    const m = groupSidebar([orch, blocked], { wants, orchWid: '@1', env: [], activityDays: 1, status: 'archived' });
    assert.deepEqual(m.pinned.map(i => i.agent.name), ['orch']);
    assert.deepEqual(m.needsYou.map(i => i.agent.name), ['b']);
    assert.equal(m.groups.length, 0);
    assert.ok(groupSidebar([{ name: 'r', window_id: '@3', run: { task_id: 'T' } }], { wants, mode: 'none' })
        .groups[0].items.length === 1);
    assert.equal(DISPATCHED_KEY, '~dispatched');
});
