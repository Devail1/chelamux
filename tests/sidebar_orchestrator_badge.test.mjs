// CMX-76 — THE ORCHESTRATOR'S SIDEBAR ROW IS MARKED, AND THE INBOX ICON JUMPS TO IT.
//
// Since CMX-72 the orchestrator is a plain row in its folder group (no Pinned section),
// titled with Claude's drifting auto-title — so nothing reliably marked it. Now:
//   1. the row whose window holds the decisions-inbox slot (orchestratorState().wid)
//      carries an "orchestrator" badge in the row's badge slot — a WORD with an
//      accessible label, never a colour alone (the operator is red-weak);
//   2. the sidebar header's inbox icon (#btn-decisions) jumps to that row/pane, and
//      still opens the decisions log; with no orchestrator it only opens the log;
//   3. grouping is untouched — the orchestrator's row stays in its folder group.
//
// Everything runs the REAL nav.js render into a REAL #sidebar-agents, the orchestrator
// slot set through a REAL /api/orchestrator/{subscribe,release} round trip, and the
// inbox icon is the REAL Decisions island (CMX-228) in the REAL index.html slot, clicked.
//
// Run: pnpm exec vitest run tests/sidebar_orchestrator_badge.test.mjs  (pytest runs it via tests/test_js_suites.py)
import { closeMenu, menu, menuItems, openMenu } from './js_helpers/side_menu.mjs';
import { beforeAll, beforeEach, test } from 'vitest';
import assert from 'node:assert/strict';
import { bootDashboardDom, clickIsland, flush, sliceTemplate, ORCHESTRATOR } from './js_helpers/dashboard_dom.mjs';

const INBOX_SLOT = sliceTemplate('<span class="decisions-island" id="decisions-island"', '</span>');

const BODY = `
<aside class="sidebar">
  <div class="sidebar-head">${INBOX_SLOT}</div>
  <div class="side-list" id="sidebar-agents"></div>
</aside>
<div id="agent-detail"></div>
<span id="hdr-agents"></span><span id="hdr-next"></span><span id="hdr-updated"></span>`;

const ok = body => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
let nav, util, orchestrator, decisions;

beforeAll(async () => {
    ({ modules: { util, nav, orchestrator } } = await bootDashboardDom({
        body: BODY,
        canvasStub: true,
        // Off, so the jump lands on the agent-detail view — a real DOM node to read back.
        terminalsEnabled: false,
        fetchImpl: (url, opts) => {
            const u = String(url);
            if (u.includes('/api/orchestrator/subscribe')) {
                const body = opts && opts.body ? JSON.parse(opts.body) : {};
                // wid (the @id) and name (the tmux name) differ, as in app.py
                return ok({ ok: true, wid: body.wid, name: `${body.wid}-tmux-name`, state: 'registered', why: '', queued: 0 });
            }
            if (u.includes('/api/orchestrator/release')) {
                return ok({ ok: true, wid: null, name: null, state: 'unregistered', why: '', queued: 0 });
            }
            if (u.includes('/api/sidebar/archive')) return ok({ hidden: [], sessions: [] });
            return ok(/\/api\/(agents|summary|log)/.test(u) && !u.includes('/api/log') ? [] : {});
        },
        extraModules: ['util.js', 'nav.js', ORCHESTRATOR],
    }));
    decisions = await import('../chela/dashboard/frontend/src/features/decisions/index.ts');
});

const ROWS = [
    { name: 'orch', window_id: '@1', online: true, claude_running: true, session_status: 'idle',
        cwd: '/home/u', cwd_is_home: true, ai_title: 'Chela orchestrator handoff' },
    { name: 'home-2', window_id: '@2', online: true, claude_running: true, session_status: 'idle',
        cwd: '/home/u', cwd_is_home: true },
    { name: 'proj', window_id: '@3', online: true, claude_running: true, session_status: 'idle',
        cwd: '/srv/code/zeta' },
];

function render(rows = ROWS) {
    util.setAgentsCache(rows);
    nav.renderSidebarAgents(rows);
}

async function setOrchestrator(wid) {
    if (wid) await orchestrator.orchestratorSubscribe(wid);
    else await orchestrator.orchestratorRelease(orchestrator.orchestratorState().wid);
    await flush();
}

const rowFor = name => document.querySelector(`#sidebar-agents .agent-row[data-agent="${name}"]`);
const badged = () => [...document.querySelectorAll('#sidebar-agents .agent-row')]
    .filter(r => r.querySelector('.ar-orch')).map(r => r.dataset.agent);
const menuOpen = () => decisions.inboxOpen.get();

beforeEach(async () => {
    localStorage.clear();
    decisions.closeDecisions();
    await setOrchestrator(null);
    render();
});

test('CMX-76: the orchestrator\'s row has the badge — a word with an accessible label — and no other row does', async () => {
    await setOrchestrator('@1');
    // the listener re-rendered off the subscribe — no renderSidebarAgents call from here
    assert.deepEqual(badged(), ['orch'], 'exactly the orchestrator\'s row must carry the badge');
    const b = rowFor('orch').querySelector('.ar-orch');
    // CMX-121: a crown icon whose tooltip and accessible name say it: never a colour-only cue
    assert.ok(b.querySelector('svg.lucide-crown'), 'the badge is not the crown icon');
    assert.equal(b.title, 'Orchestrator: holds the decisions inbox');
    assert.equal(b.getAttribute('aria-label'), 'Orchestrator: holds the decisions inbox');
    // in the row's metadata, after the title
    assert.ok(b.closest('.ar-meta'), 'the badge is not in the row\'s .ar-meta');
    // the slot moves with the role
    await setOrchestrator('@3');
    assert.deepEqual(badged(), ['proj']);
});

test('CMX-76: no orchestrator ⇒ no badge anywhere', async () => {
    await setOrchestrator(null);
    render();
    assert.deepEqual(badged(), []);
    assert.equal(document.querySelectorAll('#sidebar-agents .ar-orch').length, 0);
});

test('CMX-76 / CMX-72 regression: the orchestrator\'s row is still in its folder group — no Pinned section', async () => {
    await setOrchestrator('@1');
    const g = rowFor('orch').closest('.side-group');
    assert.ok(g, 'the orchestrator\'s row is not inside a group');
    assert.equal(g.dataset.g, '/home/u', 'the orchestrator\'s row left its folder group');
    assert.equal(document.querySelector('#sidebar-agents .side-pinned'), null, 'a Pinned section came back');
    const names = [...g.querySelectorAll('.agent-row')].map(r => r.dataset.agent).sort();
    assert.deepEqual(names, ['home-2', 'orch']);
});

test('CMX-76: the REAL inbox icon\'s click selects the orchestrator\'s window — and still opens the log', async () => {
    await setOrchestrator('@1');
    assert.equal(rowFor('orch').classList.contains('active'), false);
    clickIsland(document.getElementById('btn-decisions'));
    assert.equal(document.querySelector('#agent-detail .detail-title').textContent, 'orch',
        'the inbox icon did not jump to the orchestrator\'s window');
    assert.ok(rowFor('orch').classList.contains('active'), 'the orchestrator\'s row was not selected');
    assert.equal(rowFor('home-2').classList.contains('active'), false);
    assert.ok(menuOpen(), 'the decisions log no longer opens off the inbox icon');
    assert.match(document.getElementById('btn-decisions').getAttribute('aria-label'), /jump to the orchestrator/);
});

test('CMX-76: the jump expands the orchestrator\'s folder group when it is collapsed', async () => {
    await setOrchestrator('@1');
    nav.toggleGroup('/home/u');
    assert.ok(rowFor('orch').closest('.side-group').classList.contains('collapsed'));
    assert.equal(nav.jumpToOrchestrator(), true);
    assert.equal(rowFor('orch').closest('.side-group').classList.contains('collapsed'), false,
        'the jump left the orchestrator\'s row hidden in a collapsed group');
});

test('CMX-76: with no orchestrator the inbox icon only opens the log, as before', async () => {
    await setOrchestrator(null);
    window.chela.selectAgent('proj');   // something else is selected; the click must not move it
    clickIsland(document.getElementById('btn-decisions'));
    assert.ok(menuOpen(), 'the inbox icon stopped opening the decisions log');
    assert.equal(document.querySelector('#agent-detail .detail-title').textContent, 'proj');
    assert.equal(nav.jumpToOrchestrator(), false);
    assert.equal(document.getElementById('btn-decisions').getAttribute('aria-label'), 'Decisions inbox');
});

// CMX-81: an idle session is archivable — but never the orchestrator, idle or not.
test('CMX-81: the idle orchestrator is NOT archivable — its row Archive is disabled and Archive all skips it', async () => {
    await setOrchestrator('@1');
    render();
    const archiveOf = name => {
        if (menu()) closeMenu();
        openMenu(rowFor(name).querySelector('.row-more'));
        return menuItems().find(i => i.text === 'Archive');
    };
    assert.equal(archiveOf('orch').disabled, true, 'the orchestrator\'s row offers an enabled Archive');
    assert.equal(archiveOf('home-2').disabled, false, 'control: its idle neighbour is archivable');
    closeMenu();
    openMenu(document.querySelector('.side-group[data-g="/home/u"] .group-more'));
    assert.ok(menuItems().some(i => i.text === 'Archive all (1)'),
        `expected "Archive all (1)", got ${JSON.stringify(menuItems().map(i => i.text))}`);
    closeMenu();
});
