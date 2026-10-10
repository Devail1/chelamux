// CMX-67 — sidebar part 3, IN A REAL DOM: the judge's live progress on the run row, and
// cluster counts that are checked against MORE than one row.
//
// 1. A dispatched run is ONE row (CMX-35). While its judge's DETACHED battery runs, that
//    row reads the battery's label (`⚖️ testing · 3/6 · 15m`) — CMX-40's model and its
//    /api/agents `judge_battery` field, not a recount. The label never names the
//    experiment running now (it may be held out). A battery whose pid died before a
//    verdict reads "judge run died" with the needs-a-human shape — never idle, never
//    "awaiting review".
// 2. Every count the sidebar shows (Pinned / Needs you / Dispatched) equals the rows it
//    heads. CMX-50: the old assertions staged ONE row per cluster, so a count hardcoded
//    to `1` passed them; here every countable cluster that CAN hold several holds 2-3.
//
// Run: node --test tests/sidebar_run_progress_counts.test.mjs  (pytest runs it via
// tests/test_js_suites.py)
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { bootDashboardDom } from './js_helpers/dashboard_dom.mjs';
import { buildItems, DISPATCHED_KEY, runState } from '../chela/dashboard/static/js/sidebarmodel.js';

const BODY = `
<aside class="sidebar">
  <div class="side-list" id="sidebar-agents"></div>
</aside>
<div id="agent-detail"></div>
<span id="hdr-agents"></span><span id="hdr-next"></span><span id="hdr-updated"></span>`;

let nav, util, orchestrator;

before(async () => {
    ({ modules: { util, nav, orchestrator } } = await bootDashboardDom({
        body: BODY,
        canvasStub: true,
        terminalsEnabled: false,
        fetchImpl: (url, opts) => {
            const u = String(url);
            let body = /\/api\/(agents|summary)/.test(u) ? [] : {};
            // the orchestrator slot: subscribing @900 makes it the Pinned row
            if (u.includes('/api/orchestrator/subscribe')) {
                const { wid } = JSON.parse(opts.body);
                body = { ok: true, wid, name: 'orch', state: 'registered', why: '', queued: 0 };
            }
            return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
        },
        extraModules: ['util.js', 'nav.js', 'orchestrator.js'],
    }));
});

beforeEach(() => { localStorage.clear(); });

const wants = a => !!a && (a.needs_human === true || a.session_status === 'waiting');

let _wid = 100;
const win = (name, over = {}) => ({
    name, window_id: `@${_wid++}`, online: true, claude_running: true, session_status: 'idle', ...over,
});
const WT = '/srv/u/.chela/worktrees/chelamux';
const runCard = (tid, title, role, over = {}) => ({ task_id: tid, title, status: 'running', judge_state: '', role, ...over });

function render(rows) {
    util.setAgentsCache(rows);
    nav.renderSidebarAgents(rows);
}
const host = () => document.getElementById('sidebar-agents');
const runRow = tid => host().querySelector(`.agent-row[data-run="${tid}"]`);

// `current` carries a guard text no surface may print (CMX-395: held-out experiments).
const SECRET = 'SECRET-held-out-guard-text';
const TESTING = { state: 'testing', done: 3, total: 6, label: '⚖️ testing · 3/6 · 15m', current: `g.py: ${SECRET}` };
const DIED = { state: 'died', done: 2, total: 6, label: '⚖️ judge run died — no verdict', current: `g.py: ${SECRET}` };

// A run whose agent finished and whose judge is up: status awaiting_review,
// judge_state running — the CMX-35 row reads plain "judging" without the battery.
function judgedRun(tid, battery, over = {}) {
    const card = role => runCard(tid, `Run ${tid}`, role, { status: 'awaiting_review', judge_state: 'running', ...over });
    return [
        win(`liavacc/${tid.toLowerCase()}-x`, { cwd: `${WT}/${tid}`, dispatched: true, run: card('agent') }),
        win(`judge-liavacc/${tid.toLowerCase()}-x`, { cwd: `${WT}/judge-${tid}`, dispatched: true,
            run: card('judge'), judge_battery: battery }),
    ];
}

// --- 1. judge progress on the run row ----------------------------------------------

test('a live battery shows its progress on the run\'s ONE row: "⚖️ testing · 3/6 · 15m"', () => {
    render([...judgedRun('CMX-81', TESTING), ...judgedRun('CMX-82', null)]);
    const row = runRow('CMX-81');
    assert.ok(row, 'no run row for CMX-81');
    assert.equal(host().querySelectorAll('.agent-row[data-run="CMX-81"]').length, 1, 'the run must stay ONE row');
    assert.equal(row.querySelector('.ar-state').textContent, '⚖️ testing · 3/6 · 15m');
    assert.ok(row.querySelector('.term-status-dot').classList.contains('judging'), row.querySelector('.term-status-dot').className);
    // control: the same run shape with no battery is plain "judging"
    assert.equal(runRow('CMX-82').querySelector('.ar-state').textContent, 'judging');
});

test('the run row never names the experiment running now — counts and elapsed only', () => {
    render([...judgedRun('CMX-83', TESTING), ...judgedRun('CMX-84', DIED)]);
    assert.ok(!host().innerHTML.includes(SECRET), 'the sidebar printed the battery\'s current experiment');
});

test('a battery whose pid died before a verdict reads "judge run died" — never idle, never awaiting review', () => {
    // status awaiting_review + no judge_state would read "awaiting review" without the battery
    render([...judgedRun('CMX-85', DIED, { judge_state: '' })]);
    const row = runRow('CMX-85');
    const word = row.querySelector('.ar-state').textContent;
    assert.match(word, /judge run died/);
    assert.ok(!/idle|awaiting review/.test(word), word);
    assert.ok(row.querySelector('.term-status-dot').classList.contains('waiting'),
        'a died judge wants a human: the waiting shape, not idle');
});

test('model: precedence — a blocked window outranks the battery; a recorded verdict outranks a dead status file', () => {
    const item = (battery, over = {}, agentOver = {}) => buildItems([
        { name: 'a', window_id: '@1', ...agentOver, run: { task_id: 'T', status: 'awaiting_review', judge_state: 'running', role: 'agent', ...over } },
        { name: 'j', window_id: '@2', judge_battery: battery, run: { task_id: 'T', status: 'awaiting_review', judge_state: 'running', role: 'judge', ...over } },
    ])[0];
    assert.deepEqual(runState(item(TESTING), wants), { word: '⚖️ testing · 3/6 · 15m', shape: 'judging' });
    assert.deepEqual(runState(item(TESTING, {}, { session_status: 'waiting' }), wants), { word: 'waiting', shape: 'waiting' });
    assert.deepEqual(runState(item(DIED), wants), { word: '⚖️ judge run died — no verdict', shape: 'waiting' });
    // the verdict landed (judge_state clean): the run finished, a leftover file is not a death
    assert.deepEqual(runState(item(DIED, { judge_state: 'clean' }), wants), { word: 'awaiting review', shape: 'idle' });
    assert.deepEqual(runState(item(null), wants), { word: 'judging', shape: 'judging' });
});

// --- 2. real cluster counts ---------------------------------------------------------

// Every count element the sidebar draws, paired with the rows of the cluster it heads.
function countedClusters() {
    const out = [];
    for (const c of host().querySelectorAll('.side-triage')) {
        out.push({ name: c.querySelector('.triage-head').firstChild.textContent.trim(),
            count: c.querySelector('.triage-count').textContent, rows: c.querySelectorAll('.agent-row').length });
    }
    for (const g of host().querySelectorAll('.side-group')) {
        const el = g.querySelector('.group-head .group-count');
        if (el) out.push({ name: g.dataset.g, count: el.textContent, rows: g.querySelectorAll('.group-rows .agent-row').length });
    }
    return out;
}

test('every count shown equals the rows it heads — staged with 2-3 rows per cluster', async () => {
    const rows = [
        // Needs you: 3 rows
        win('ask-1', { session_status: 'waiting', cwd: '/srv/code/alpha' }),
        win('ask-2', { needs_human: true, cwd: '/srv/code/beta' }),
        win('ask-3', { session_status: 'waiting', cwd: '/srv/code/alpha' }),
        // Dispatched: 2 runs (one of them two windows — still one row)
        ...judgedRun('CMX-91', TESTING),
        win('liavacc/cmx-92-y', { cwd: `${WT}/CMX-92`, dispatched: true, run: runCard('CMX-92', 'Run CMX-92', 'agent') }),
        // folder groups: 2 and 3 rows
        win('a-1', { cwd: '/srv/code/alpha' }), win('a-2', { cwd: '/srv/code/alpha' }),
        win('b-1', { cwd: '/srv/code/beta' }), win('b-2', { cwd: '/srv/code/beta' }), win('b-3', { cwd: '/srv/code/beta' }),
        // the orchestrator — Pinned
        { ...win('orch', { cwd: '/srv/code/alpha' }), window_id: '@900' },
    ];
    util.setAgentsCache(rows);
    await orchestrator.orchestratorSubscribe('@900');
    render(rows);
    try {
        const got = countedClusters();
        const byName = Object.fromEntries(got.map(c => [c.name, c]));
        // the staged lengths, so a constant count cannot pass
        assert.equal(byName['Needs you'] && byName['Needs you'].count, '3');
        assert.equal(byName[DISPATCHED_KEY] && byName[DISPATCHED_KEY].count, '2');
        // Pinned holds the ONE orchestrator slot by construction (the inbox has one
        // owner), so it cannot be staged with 2-3 — it is still held to its rows.
        assert.equal(byName.Pinned && byName.Pinned.count, '1');
        for (const c of got) assert.equal(c.count, String(c.rows), `${c.name}: count ${c.count} ≠ ${c.rows} rows`);
        // folder groups show NO count (the desktop's quiet header) — so none can drift
        const folders = [...host().querySelectorAll('.side-group')].filter(g => g.dataset.g.startsWith('/'));
        assert.deepEqual(folders.map(g => [g.dataset.g, g.querySelectorAll('.agent-row').length]),
            [['/srv/code/alpha', 2], ['/srv/code/beta', 3]], 'setup: folder groups must be staged with 2-3 rows');
        for (const g of folders) assert.equal(g.querySelector('.group-count'), null, `${g.dataset.g} grew a count`);
    } finally {
        await orchestrator.orchestratorRelease('@900');
    }
});
