// CMX-35 — THE SIDEBAR GROUPED BY FOLDER, IN A REAL DOM (part 1 of 3).
//
// The sidebar now groups sessions the way the Claude desktop app's Code sidebar does:
// by PROJECT FOLDER (the session's cwd), each group a quiet header with a `+` (new
// session in that folder), a collapse chevron and a group menu (right-click, or the ⋯
// for touch). Dispatched work is ONE row per RUN, in a "Dispatched" group. These run the
// REAL nav.js (renderSidebarAgents → sidebarmodel.js) into a REAL #sidebar-agents, fire
// the REAL inline handlers the rendered markup carries, and read the result back off the
// DOM — plus the pure model (sidebarmodel.js) for the rules the DOM can't reach alone
// (a waiting row never lands in a group, so the archive rule for it is asserted there).
//
// Run: node --test tests/sidebar_groups.test.mjs  (pytest runs it via tests/test_js_suites.py)
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { bootDashboardDom, flush } from './js_helpers/dashboard_dom.mjs';
import {
    DISPATCHED_KEY, OTHER_KEY, buildItems, folderKey, folderLabels, groupSidebar, isArchivable, runState,
} from '../chela/dashboard/static/js/sidebarmodel.js';

const BODY = `
<aside class="sidebar">
  <div class="side-list" id="sidebar-agents"></div>
</aside>
<div id="agent-detail"></div>
<span id="hdr-agents"></span><span id="hdr-next"></span><span id="hdr-updated"></span>`;

// Every POST /api/agents/spawn the launcher makes, body parsed.
const spawns = [];
let nav, util;

before(async () => {
    ({ modules: { util, nav } } = await bootDashboardDom({
        body: BODY,
        canvasStub: true,
        // Off, so "Open … pane" lands on the agent-detail view (a real DOM node to read
        // back) instead of a wall this fixture does not build.
        terminalsEnabled: false,
        fetchImpl: (url, opts) => {
            if (String(url).includes('/api/agents/spawn')) spawns.push(JSON.parse(opts.body));
            const u = String(url);
            // the list endpoints answer with an empty list, the rest with an empty object
            const body = u.includes('/api/agents/spawn') ? { ok: true } : /\/api\/(agents|summary)/.test(u) ? [] : {};
            return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
        },
        extraModules: ['util.js', 'nav.js'],
    }));
});

beforeEach(() => {
    localStorage.clear();
    spawns.length = 0;
});

const wants = a => !!a && (a.needs_human === true || a.session_status === 'waiting');

let _wid = 100;
const win = (name, over = {}) => ({
    name, window_id: `@${_wid++}`, online: true, claude_running: true, session_status: 'idle', ...over,
});

function render(rows) {
    util.setAgentsCache(rows);
    nav.renderSidebarAgents(rows);
}

const host = () => document.getElementById('sidebar-agents');
const group = key => [...host().querySelectorAll('.side-group')].find(g => g.dataset.g === key) || null;
const groupKeys = () => [...host().querySelectorAll('.side-group')].map(g => g.dataset.g);
const rowsIn = key => [...group(key).querySelectorAll('.agent-row')].map(r => r.dataset.agent);
const labelOf = key => group(key).querySelector('.group-name').textContent;

// Run a rendered element's inline handler the way a real event would: `this` is the
// element, and `event` is a real-enough event (stopPropagation / preventDefault /
// currentTarget), since the group and row handlers call both.
function fire(el, attr = 'onclick') {
    assert.ok(el, `no element to fire ${attr} on`);
    const code = el.getAttribute(attr);
    assert.ok(code, `the element has no ${attr} attribute`);
    const ev = { target: el, currentTarget: el, stopPropagation() {}, preventDefault() {} };
    return new Function('chela', 'event', code).call(el, window.chela, ev);
}
const menu = id => document.getElementById(id);
const menuItems = id => [...menu(id).querySelectorAll('.popover-item')].map(i => ({
    text: i.textContent.trim(), act: i.dataset.act || null, disabled: i.classList.contains('disabled'),
}));
function clickMenu(id, text) {
    const el = [...menu(id).querySelectorAll('.popover-item')].find(i => i.textContent.trim() === text);
    assert.ok(el, `no "${text}" item in #${id}`);
    assert.ok(!el.classList.contains('disabled'), `"${text}" is disabled`);
    menu(id).onclick({ target: el, stopPropagation() {} });
}

// --- 1. grouping by cwd ----------------------------------------------------------

test('rows group by cwd — two differently-named windows in one folder share ONE group', () => {
    render([
        win('alpha-1', { cwd: '/srv/code/alpha' }),
        win('beta-1', { cwd: '/srv/code/beta' }),
        win('alpha-2', { cwd: '/srv/code/alpha' }),
        // same NAME as a window in alpha, different folder — the name must not group it
        win('alpha-1', { cwd: '/srv/code/beta' }),
    ]);
    assert.deepEqual(groupKeys(), ['/srv/code/alpha', '/srv/code/beta']);
    assert.equal(labelOf('/srv/code/alpha'), 'alpha');
    assert.deepEqual(rowsIn('/srv/code/alpha').sort(), ['alpha-1', 'alpha-2']);
    assert.deepEqual(rowsIn('/srv/code/beta').sort(), ['alpha-1', 'beta-1']);
    // a lone session still gets its folder header (the desktop's look — no loose rows)
    render([win('solo', { cwd: '/srv/code/gamma' })]);
    assert.equal(labelOf('/srv/code/gamma'), 'gamma');
});

test('home-dir and cwd-less rows land in "Other", at the bottom', () => {
    render([
        win('in-home', { cwd: '/home/u', cwd_is_home: true }),
        win('no-cwd', { cwd: null }),
        win('proj', { cwd: '/srv/code/zeta' }),
    ]);
    assert.deepEqual(groupKeys(), ['/srv/code/zeta', OTHER_KEY]);
    assert.equal(labelOf(OTHER_KEY), 'Other');
    assert.deepEqual(rowsIn(OTHER_KEY).sort(), ['in-home', 'no-cwd']);
    // its "+" opens a session in the home dir it knows about
    fire(group(OTHER_KEY).querySelector('.group-add'));
    assert.deepEqual(spawns, [{ cwd: '/home/u', command: 'claude' }]);
});

test('a basename collision is disambiguated by the parent path, like the desktop', () => {
    render([
        win('a', { cwd: '/srv/work/python-agent' }),
        win('b', { cwd: '/srv/personal/python-agent' }),
        win('c', { cwd: '/srv/x/same/tool' }),
        win('d', { cwd: '/srv/y/same/tool' }),
    ]);
    assert.equal(labelOf('/srv/work/python-agent'), 'python-agent · work');
    assert.equal(labelOf('/srv/personal/python-agent'), 'python-agent · personal');
    // the nearest parent ties ("same") — walk up until the labels differ
    assert.equal(labelOf('/srv/x/same/tool'), 'tool · x/same');
    assert.equal(labelOf('/srv/y/same/tool'), 'tool · y/same');
});

// --- 2. the "+" ----------------------------------------------------------------

test('"+" on a group calls the launcher with THAT group\'s cwd — even where a session already runs', async () => {
    render([
        win('alpha-1', { cwd: '/srv/code/alpha' }),
        win('beta-1', { cwd: '/srv/code/beta' }),
    ]);
    fire(group('/srv/code/beta').querySelector('.group-add'));
    await flush();
    assert.deepEqual(spawns, [{ cwd: '/srv/code/beta', command: 'claude' }],
        'the "+" must spawn a NEW claude in the group\'s folder (not focus the session already there)');
});

// --- 3. collapse -----------------------------------------------------------------

test('collapse is per group, persists, and survives a re-render', () => {
    const rows = [win('a', { cwd: '/srv/code/alpha' }), win('b', { cwd: '/srv/code/beta' })];
    render(rows);
    fire(group('/srv/code/alpha').querySelector('.group-head'));
    assert.ok(group('/srv/code/alpha').classList.contains('collapsed'));
    assert.ok(!group('/srv/code/beta').classList.contains('collapsed'));
    assert.deepEqual(JSON.parse(localStorage.getItem('chela_grp_collapsed')), ['/srv/code/alpha']);
    render(rows);
    assert.ok(group('/srv/code/alpha').classList.contains('collapsed'), 'collapse forgot itself on re-render');
    fire(group('/srv/code/alpha').querySelector('.group-head'));
    assert.ok(!group('/srv/code/alpha').classList.contains('collapsed'), 'a second click did not expand it');
});

// A localStorage whose every accessor throws (a private window, blocked site data).
function withThrowingStorage(fn) {
    const real = globalThis.localStorage;
    const boom = () => { throw new Error('storage blocked'); };
    Object.defineProperty(globalThis, 'localStorage', {
        value: { getItem: boom, setItem: boom, removeItem: boom, clear: boom }, writable: true, configurable: true,
    });
    try { return fn(); } finally {
        Object.defineProperty(globalThis, 'localStorage', { value: real, writable: true, configurable: true });
    }
}

test('a THROWING localStorage still renders, and collapse / order / archive still work for the session', () => {
    withThrowingStorage(() => {
        const rows = [
            win('a', { cwd: '/srv/code/alpha' }),
            win('b', { cwd: '/srv/code/beta' }),
            win('b-done', { cwd: '/srv/code/beta', done: true }),
        ];
        render(rows);
        assert.deepEqual(groupKeys(), ['/srv/code/alpha', '/srv/code/beta']);
        fire(group('/srv/code/alpha').querySelector('.group-head'));
        assert.ok(group('/srv/code/alpha').classList.contains('collapsed'));
        nav.groupMenuAction('/srv/code/beta', 'up');
        assert.deepEqual(groupKeys(), ['/srv/code/beta', '/srv/code/alpha']);
        nav.groupMenuAction('/srv/code/beta', 'archive');
        assert.deepEqual(rowsIn('/srv/code/beta'), ['b']);
    });
});

// --- 4. the group menu -----------------------------------------------------------

test('the group menu opens on right-click AND on the ⋯ button, with the desktop\'s items', () => {
    render([win('a', { cwd: '/srv/code/alpha' }), win('b', { cwd: '/srv/code/beta' })]);
    fire(group('/srv/code/alpha').querySelector('.group-head'), 'oncontextmenu');
    assert.equal(menu('group-menu').style.display, 'block', 'right-click did not open the group menu');
    assert.deepEqual(menuItems('group-menu').map(i => i.text),
        ['New session', 'Move up', 'Move down', 'Collapse all', 'Expand all', 'Archive all (0)']);
    assert.equal(menu('group-menu').querySelectorAll('.popover-sep').length, 3, 'dividers between action groups');
    window.chela.hideSideMenus();
    assert.equal(menu('group-menu').style.display, 'none');
    fire(group('/srv/code/beta').querySelector('.group-more'));
    assert.equal(menu('group-menu').style.display, 'block', 'the ⋯ button did not open the group menu');
    // first group can't move up, last can't move down
    assert.ok(menuItems('group-menu').find(i => i.text === 'Move down').disabled);
    assert.ok(!menuItems('group-menu').find(i => i.text === 'Move up').disabled);
});

test('Move up / Move down reorder the groups and the order persists', () => {
    const rows = [
        win('a', { cwd: '/srv/code/alpha' }), win('b', { cwd: '/srv/code/beta' }), win('c', { cwd: '/srv/code/gamma' }),
    ];
    render(rows);
    assert.deepEqual(groupKeys(), ['/srv/code/alpha', '/srv/code/beta', '/srv/code/gamma']);
    fire(group('/srv/code/gamma').querySelector('.group-more'));
    clickMenu('group-menu', 'Move up');
    assert.deepEqual(groupKeys(), ['/srv/code/alpha', '/srv/code/gamma', '/srv/code/beta']);
    fire(group('/srv/code/alpha').querySelector('.group-head'), 'oncontextmenu');
    clickMenu('group-menu', 'Move down');
    assert.deepEqual(groupKeys(), ['/srv/code/gamma', '/srv/code/alpha', '/srv/code/beta']);
    assert.deepEqual(JSON.parse(localStorage.getItem('chela_sb_group_order')),
        ['/srv/code/gamma', '/srv/code/alpha', '/srv/code/beta']);
    render(rows);   // a fresh render reads the persisted order back
    assert.deepEqual(groupKeys(), ['/srv/code/gamma', '/srv/code/alpha', '/srv/code/beta']);
});

test('Collapse all / Expand all fold and unfold every group', () => {
    render([win('a', { cwd: '/srv/code/alpha' }), win('b', { cwd: '/srv/code/beta' })]);
    fire(group('/srv/code/alpha').querySelector('.group-more'));
    clickMenu('group-menu', 'Collapse all');
    assert.ok(groupKeys().every(k => group(k).classList.contains('collapsed')));
    fire(group('/srv/code/beta').querySelector('.group-more'));
    clickMenu('group-menu', 'Expand all');
    assert.ok(groupKeys().every(k => !group(k).classList.contains('collapsed')));
});

// --- 4b. Archive all --------------------------------------------------------------

test('"Archive all" on a MIXED group hides only finished rows — never a running one — and is reversible', () => {
    const rows = [
        win('working', { cwd: '/srv/code/mix', session_status: 'busy' }),
        win('finished', { cwd: '/srv/code/mix', done: true }),
        win('dead-shell', { cwd: '/srv/code/mix', claude_running: false, window_type: 'shell' }),
        win('idle-unfinished', { cwd: '/srv/code/mix' }),
        win('blocked', { cwd: '/srv/code/mix', session_status: 'waiting' }),
    ];
    render(rows);
    fire(group('/srv/code/mix').querySelector('.group-more'));
    assert.ok(menuItems('group-menu').some(i => i.text === 'Archive all (2)'),
        `expected "Archive all (2)", got ${JSON.stringify(menuItems('group-menu').map(i => i.text))}`);
    clickMenu('group-menu', 'Archive all (2)');
    assert.deepEqual(rowsIn('/srv/code/mix').sort(), ['idle-unfinished', 'working'],
        'Archive all must hide the finished rows and keep the running/idle ones');
    assert.ok(host().querySelector('.side-needs-you .agent-row[data-agent="blocked"]'),
        'the waiting row must stay in Needs you');
    const toggle = host().querySelector('.side-archived-toggle');
    assert.equal(toggle.textContent, 'Show archived (2)');
    // reversible: show them (dimmed), then unarchive
    fire(toggle);
    assert.deepEqual(rowsIn('/srv/code/mix').sort(), ['dead-shell', 'finished', 'idle-unfinished', 'working']);
    assert.ok(group('/srv/code/mix').querySelector('.agent-row[data-agent="finished"]').classList.contains('archived'));
    fire(group('/srv/code/mix').querySelector('.group-more'));
    clickMenu('group-menu', 'Unarchive all (2)');
    fire(host().querySelector('.side-archived-toggle'));   // hide archived again
    assert.deepEqual(rowsIn('/srv/code/mix').sort(), ['dead-shell', 'finished', 'idle-unfinished', 'working']);
    assert.equal(host().querySelector('.side-archived-toggle'), null);
});

test('an archived row that comes back to life is shown again, and leaves the archive', () => {
    const finished = win('revived', { cwd: '/srv/code/mix', done: true });
    const keep = win('other', { cwd: '/srv/code/mix' });
    render([finished, keep]);
    nav.groupMenuAction('/srv/code/mix', 'archive');
    assert.deepEqual(rowsIn('/srv/code/mix'), ['other']);
    render([{ ...finished, done: false, session_status: 'busy' }, keep]);
    assert.deepEqual(rowsIn('/srv/code/mix').sort(), ['other', 'revived'], 'a busy row was kept hidden');
    assert.deepEqual(JSON.parse(localStorage.getItem('chela_sb_archived')), [],
        'a woken row must leave the archive, or it is re-hidden the moment it settles');
});

test('model: isArchivable is false for anything live — busy, waiting, needs_human, orchestrator, judge mid-battery', () => {
    const one = a => buildItems([a])[0];
    const ok = a => isArchivable(one(a), { wants, orchWid: '@1' });
    assert.equal(ok({ name: 'f', window_id: '@9', claude_running: true, done: true }), true);
    assert.equal(ok({ name: 's', window_id: '@9', claude_running: false }), true);
    assert.equal(ok({ name: 'b', window_id: '@9', claude_running: true, done: true, session_status: 'busy' }), false);
    assert.equal(ok({ name: 'w', window_id: '@9', claude_running: true, done: true, session_status: 'waiting' }), false);
    assert.equal(ok({ name: 'n', window_id: '@9', claude_running: false, needs_human: true }), false);
    assert.equal(ok({ name: 'o', window_id: '@1', claude_running: false }), false);
    assert.equal(ok({ name: 'i', window_id: '@9', claude_running: true }), false, 'an idle unfinished session is not "finished"');
    const run = (status, extra = {}) => isArchivable(buildItems([
        { name: 'j', window_id: '@8', claude_running: false, run: { task_id: 'CMX-9', status, role: 'judge', ...extra } },
    ])[0], { wants });
    assert.equal(run('done'), true);
    assert.equal(run('running'), false);
    assert.equal(run('awaiting_review'), false);
    assert.equal(run('done', { judge_state: 'running' }), false);
    assert.equal(isArchivable(buildItems([{ name: 'j', window_id: '@8', claude_running: false,
        judge_battery: { state: 'testing' }, run: { task_id: 'CMX-9', status: 'done', role: 'judge' } }])[0], { wants }), false);
});

// --- 7. dispatched work: one row per run, in its own group -----------------------

const WT = '/home/u/.chela/worktrees/chelamux';
const runCard = (tid, title, role, over = {}) => ({ task_id: tid, title, status: 'running', judge_state: '', role, ...over });

test('a worktree cwd lands in "Dispatched" — never a group named after the worktree', () => {
    render([
        win('liavacc/cmx-37-theme', { cwd: `${WT}/CMX-37`, dispatched: true, run: runCard('CMX-37', 'Theme hover text', 'agent') }),
        // a worktree session the server did not (yet) tie to a run: still Dispatched
        win('stray', { cwd: `${WT}/CMX-41` }),
        win('human', { cwd: '/srv/code/chelamux' }),
    ]);
    assert.deepEqual(groupKeys(), ['/srv/code/chelamux', DISPATCHED_KEY]);
    assert.equal(group(DISPATCHED_KEY).querySelector('.group-name').textContent, 'Dispatched');
    assert.ok(!groupKeys().some(k => /CMX-/.test(k)), `a worktree got its own group: ${groupKeys()}`);
    assert.equal(group(DISPATCHED_KEY).querySelector('.group-add'), null, 'Dispatched has no "+" (a new run is a dispatch, not a spawn)');
    assert.equal(folderKey({ cwd: `${WT}/CMX-41/sub` }), DISPATCHED_KEY);
});

// docs/defeat_shapes/62b: each clause of `a.run || a.dispatched || <worktree cwd>` needs a
// fixture where it is the ONLY one true. Here it is `dispatched` alone: no run card yet, and
// a cwd outside the worktrees that, by itself, would name a human folder group.
test('a dispatched window with no run card yet (non-worktree cwd) still lands in "Dispatched"', () => {
    assert.equal(folderKey({ cwd: '/srv/code/chelamux', dispatched: true }), DISPATCHED_KEY);
    assert.equal(folderKey({ cwd: '/srv/code/chelamux' }), '/srv/code/chelamux', 'control: the flag is the only difference');
    render([
        win('early-run', { cwd: '/srv/code/chelamux', dispatched: true }),
        win('human', { cwd: '/srv/code/other' }),
    ]);
    assert.deepEqual(groupKeys(), ['/srv/code/other', DISPATCHED_KEY]);
    assert.deepEqual(rowsIn(DISPATCHED_KEY), ['early-run']);
});

test('an agent window and its judge window are ONE row per run, labelled "CMX-N · <title>"', () => {
    render([
        win('liavacc/cmx-37-theme', { cwd: `${WT}/CMX-37`, dispatched: true,
            run: runCard('CMX-37', 'Theme hover text on the wall', 'agent', { status: 'awaiting_review', judge_state: 'running' }) }),
        win('judge-liavacc/cmx-37-theme', { cwd: `${WT}/judge-CMX-37`, dispatched: true,
            run: runCard('CMX-37', 'Theme hover text on the wall', 'judge', { status: 'awaiting_review', judge_state: 'running' }) }),
        win('liavacc/cmx-32-orch', { cwd: `${WT}/CMX-32`, dispatched: true, run: runCard('CMX-32', 'Wall orchestrator ring', 'agent') }),
        // a run whose agent already finished: only its judge window is left
        win('judge-liavacc/cmx-40-x', { cwd: `${WT}/judge-CMX-40`, dispatched: true,
            run: runCard('CMX-40', 'Judge progress', 'judge', { status: 'awaiting_review', judge_state: 'running' }) }),
    ]);
    const rows = [...group(DISPATCHED_KEY).querySelectorAll('.agent-row')];
    assert.equal(rows.length, 3, `expected one row per run (3), got ${rows.map(r => r.dataset.agent)}`);
    assert.equal(group(DISPATCHED_KEY).querySelector('.group-count').textContent, String(rows.length),
        'the Dispatched header count must equal the rows it shows');
    const r37 = group(DISPATCHED_KEY).querySelector('.agent-row[data-run="CMX-37"]');
    assert.equal(r37.dataset.agent, 'liavacc/cmx-37-theme', 'a click must open the AGENT pane');
    assert.equal(r37.querySelector('.agent-row-name').textContent, 'CMX-37 · Theme hover text on the wall');
    assert.equal(r37.querySelector('.ar-state').textContent, 'judging');
    assert.ok(r37.querySelector('.term-status-dot').classList.contains('judging'), 'the state has a shape, not just a word');
    assert.equal(host().querySelector('.agent-row[data-agent="judge-liavacc/cmx-37-theme"]'), null,
        'the judge rendered as its own row — it is a state of the run');
    // the judge-only run opens its judge
    assert.equal(group(DISPATCHED_KEY).querySelector('.agent-row[data-run="CMX-40"]').dataset.agent, 'judge-liavacc/cmx-40-x');
});

test('the run row\'s menu opens on right-click and on ⋯, and "Open judge pane" opens the JUDGE', () => {
    const rows = [
        win('liavacc/cmx-37-theme', { cwd: `${WT}/CMX-37`, dispatched: true, run: runCard('CMX-37', 'Theme', 'agent') }),
        win('judge-liavacc/cmx-37-theme', { cwd: `${WT}/judge-CMX-37`, dispatched: true, run: runCard('CMX-37', 'Theme', 'judge') }),
    ];
    render(rows);
    const row = () => host().querySelector('.agent-row[data-run="CMX-37"]');
    fire(row(), 'oncontextmenu');
    assert.equal(menu('row-menu').style.display, 'block');
    assert.deepEqual(menuItems('row-menu').map(i => i.text), ['Open agent pane', 'Open judge pane']);
    clickMenu('row-menu', 'Open judge pane');
    assert.equal(document.querySelector('#agent-detail .detail-title').textContent, 'judge-liavacc/cmx-37-theme');
    fire(row().querySelector('.row-more'));
    clickMenu('row-menu', 'Open agent pane');
    assert.equal(document.querySelector('#agent-detail .detail-title').textContent, 'liavacc/cmx-37-theme');
});

test('model: a run\'s state reads as a word AND a shape', () => {
    const st = (status, extra = {}, judge = false) => {
        const ws = [{ name: 'a', window_id: '@1', run: { task_id: 'T', status, role: 'agent', ...extra } }];
        if (judge) ws.push({ name: 'j', window_id: '@2', run: { task_id: 'T', status, role: 'judge', ...extra } });
        return runState(buildItems(ws)[0], wants);
    };
    assert.deepEqual(st('running'), { word: 'working', shape: 'working' });
    assert.deepEqual(st('claimed'), { word: 'working', shape: 'working' });
    assert.deepEqual(st('awaiting_review', { judge_state: 'running' }), { word: 'judging', shape: 'judging' });
    assert.deepEqual(st('awaiting_review', {}, true), { word: 'judging', shape: 'judging' });
    assert.deepEqual(st('awaiting_review', { judge_state: 'clean' }, true), { word: 'awaiting review', shape: 'idle' });
    assert.deepEqual(st('changes_requested'), { word: 'rework', shape: 'working' });
    assert.deepEqual(st('needs_human'), { word: 'needs human', shape: 'waiting' });
    assert.deepEqual(runState(buildItems([{ name: 'a', window_id: '@1', session_status: 'waiting',
        run: { task_id: 'T', status: 'running', role: 'agent' } }])[0], wants), { word: 'waiting', shape: 'waiting' });
});

// --- 5. row content ----------------------------------------------------------------

test('row content follows CMX-62\'s label and keeps the state word + ctx; the orchestrator stays Pinned', () => {
    render([win('shell-1', { cwd: '/srv/code/alpha', ai_title: 'Fix the reconcile loop', session_status: 'busy' })]);
    const row = host().querySelector('.agent-row[data-agent="shell-1"]');
    assert.equal(row.querySelector('.agent-row-name').textContent, 'Fix the reconcile loop');
    assert.equal(row.querySelector('.ar-state').textContent, 'working');
    assert.ok(row.querySelector('.term-status-dot').classList.contains('working'));
});

test('model: groupSidebar takes the grouping MODE (the hook part 2 extends) and refuses one it does not know', () => {
    assert.throws(() => groupSidebar([], { wants, mode: 'date' }), /unknown sidebar grouping mode/);
    const m = groupSidebar([{ name: 'a', window_id: '@1', cwd: '/x/a' }], { wants });
    assert.deepEqual(m.groups.map(g => g.label), ['a']);
    assert.deepEqual(folderLabels(['/x/a', OTHER_KEY, DISPATCHED_KEY]), { '/x/a': 'a', [OTHER_KEY]: 'Other', [DISPATCHED_KEY]: 'Dispatched' });
});
