// 🗂️📐 CMX-23 — the Work board, RENDERED, with Linear's workflow states as its columns.
//
// tests/kanban_linear_model.test.mjs proves the pure model (trackerColumns / trackerBoard /
// runStateBadges), but its only guard on kanban.js itself was a source regex — and the
// judge's mutation battery proved both call sites survive with the suite green:
//   - renderKanban's `if (tracker) {` -> `if (false && tracker) {` (chela's 6 lanes again)
//   - _kCard's `+ runStateBadges(card)` -> `+ [].concat()` (judging / rework N vanish)
// This file drives the REAL renderKanban through a real DOM and reads the board back.
// Each test carries its negative control.
//
// Run: node --test tests/kanban_tracker_render.test.mjs (tests/test_js_suites.py runs every
// .test.mjs inside pytest, by discovery).
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import { bootDashboardDom } from './js_helpers/dashboard_dom.mjs';

const KANBAN_BODY = `
<div class="work-pane active" id="work-board" data-seg="board">
  <div class="kanban-filters" id="kanban-filters"></div>
  <div class="kanban-mobile-controls" id="kanban-mobile-controls">
    <div class="kanban-nav-strip" id="kanban-nav-strip" aria-label="Jump to column"></div>
  </div>
  <div class="kanban-board" id="kanban-board"></div>
  <div id="kanban-empty" class="work-empty" style="display:none;"></div>
</div>`;

// A fake team's states, in Linear's board order (the server sorts them).
const LINEAR = [
    { name: 'Backlog', type: 'backlog', hidden: false },
    { name: 'Todo', type: 'unstarted', hidden: false },
    { name: 'In Progress', type: 'started', hidden: false },
    { name: 'In Review', type: 'started', hidden: false },
    { name: 'Done', type: 'completed', hidden: false },
    { name: 'Canceled', type: 'canceled', hidden: true },
    { name: 'Duplicate', type: 'canceled', hidden: true },
];

let renderKanban;

before(async () => {
    ({ modules: { kanban: { renderKanban } } } = await bootDashboardDom({
        body: KANBAN_BODY, extraModules: ['kanban.js'],
    }));
});

function _run(overrides = {}) {
    return {
        task_id: 'CMX-1', title: 'a task', status: 'running', pr_state: null,
        started_at: '2026-10-07T00:00:00Z', ended_at: null, attempt: 1, pr_url: null,
        pr_checks: null, branch_name: 'cmx-1', window_name: 'agent-1', ...overrides,
    };
}

// `tracker_columns` present ⇒ a Linear workflow; absent ⇒ any other tracker kind.
function _payload({ active = [], review = [], recent = [], columns = LINEAR } = {}) {
    return {
        configured: true,
        workflows: [{
            path: '/x/WORKFLOW.md', project_key: 'CMX', open_tasks: [], backlog_items: [],
            parked_tasks: [], active_runs: active, awaiting_review_runs: review,
            recent_runs: recent, ...(columns ? { tracker_columns: columns } : {}),
        }],
    };
}

const board = () => document.querySelector('#kanban-board');
const colLabels = () => [...board().querySelectorAll('.kanban-col-label')].map(e => e.textContent);
const colOf = label => [...board().querySelectorAll('.kanban-col')]
    .find(c => c.querySelector('.kanban-col-label').textContent === label);
const chips = el => [...el.querySelectorAll('.kanban-state-chip')].map(e => e.textContent);

test('with tracker_columns the board\'s columns ARE Linear\'s visible states, in Linear\'s order', () => {
    renderKanban(_payload());
    assert.deepEqual(colLabels(), ['Backlog', 'Todo', 'In Progress', 'In Review', 'Done'],
        'renderKanban did not render the tracker columns — `if (tracker)` is not taken');
    assert.equal(board().style.getPropertyValue('--kanban-cols'), '5',
        'the grid must be sized to the columns actually rendered');
});

test('negative control: no tracker_columns ⇒ chela\'s own lanes, not Linear\'s', () => {
    renderKanban(_payload({ columns: null }));
    const labels = colLabels();
    assert.ok(!labels.includes('In Review') && !labels.includes('Todo'),
              `a non-Linear workflow rendered tracker columns: ${labels}`);
    assert.equal(board().style.getPropertyValue('--kanban-cols'), String(labels.length));
});

test('a died run is a red "failed" badge INSIDE its Linear column, never a column of its own', () => {
    renderKanban(_payload({ recent: [_run({
        status: 'failed', tracker_state: 'In Review', pr_url: 'https://github.com/o/r/pull/9',
    })] }));
    assert.ok(!colLabels().some(l => /fail/i.test(l)), `a "failed" column rendered: ${colLabels()}`);
    const review = colOf('In Review');
    const card = review.querySelector('.kanban-card');
    assert.ok(card, 'the failed run is not inside In Review');
    assert.ok(card.querySelector('.kanban-state-chip.st-failed'), 'no red failed badge on the card');
    // Control: In Progress holds nothing.
    assert.equal(colOf('In Progress').querySelectorAll('.kanban-card').length, 0);
});

test('run state rides on the rendered card: judging, rework N, blocked race', () => {
    renderKanban(_payload({
        active: [_run({ task_id: 'CMX-2', tracker_state: 'In Progress', judge_state: 'running',
                        rework_count: 2 })],
        review: [_run({ task_id: 'CMX-3', status: 'awaiting_review', tracker_state: 'In Review',
                        judge_state: 'blocked_race', pr_url: 'https://github.com/o/r/pull/3' })],
    }));
    const working = chips(colOf('In Progress').querySelector('.kanban-card'));
    assert.ok(working.includes('⚖️ judging') && working.includes('🔁 rework 2'),
              `run-state badges missing from the card: ${working}`);
    assert.ok(chips(colOf('In Review').querySelector('.kanban-card')).includes('🧊 blocked race'));
});

test('negative control: a quiet run carries its status pill and no run-state badge', () => {
    renderKanban(_payload({ active: [_run({ tracker_state: 'In Progress', judge_state: 'clean',
                                            rework_count: 0 })] }));
    const got = chips(colOf('In Progress').querySelector('.kanban-card'));
    assert.deepEqual(got.filter(t => /judging|rework|blocked race/.test(t)), []);
    assert.ok(got.length >= 1, 'the status pill itself must still render');
});
