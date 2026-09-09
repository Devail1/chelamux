// issue #462 — the task-progress chip (`_taskProgressChip`, chela/dashboard/static/js/
// dispatcher.js) surfaces a dispatched run's own `done/total` task-list progress on the
// wall. This PR added the helper, a new `<th>Tasks</th>` column in the dispatcher runs
// table, a call site in kanban.js's card renderer, and a CSS class — with no test of any
// kind: `_taskProgressChip` was exported but never imported by any test file, and neither
// call site was ever driven through a real render. A judge mutation battery proved every
// one of the following survives with the whole suite green:
//   - dispatcher.js's `_cell('Tasks', _taskProgressChip(r.tasks))` -> `_cell('Tasks', '')`
//   - kanban.js's `const taskChip = _taskProgressChip(card.tasks);` -> `= '';`
//   - the chip's own `${tasks.done}/${tasks.total}` label -> `''`
//   - the chip's own in-progress tooltip line, dead-coded behind `if (false && ...)`
// This file drives BOTH real call sites through a real DOM (renderDispatcher's table,
// renderKanban's card) and reads the rendered chip back, closing all four at once.
//
// Run: node --test tests/task_progress_chip.test.mjs (tests/test_js_suites.py runs every
// .test.mjs inside pytest, by discovery).
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import { bootDashboardDom } from './js_helpers/dashboard_dom.mjs';

const DISPATCHER_BODY = `
<div class="work-pane" id="work-runs" data-seg="runs">
  <div id="dispatcher-empty" class="work-empty" style="display:none;"></div>
  <div id="dispatcher-list"></div>
</div>`;

const KANBAN_BODY = `
<div class="work-pane active" id="work-board" data-seg="board">
  <div class="kanban-filters" id="kanban-filters"></div>
  <div class="kanban-mobile-controls" id="kanban-mobile-controls">
    <div class="kanban-nav-strip" id="kanban-nav-strip" aria-label="Jump to column"></div>
  </div>
  <div class="kanban-board" id="kanban-board"></div>
  <div id="kanban-empty" class="work-empty" style="display:none;"></div>
</div>`;

let renderDispatcher;
let renderKanban;

before(async () => {
    ({ modules: { dispatcher: { renderDispatcher }, kanban: { renderKanban } } } =
        await bootDashboardDom({
            body: `${DISPATCHER_BODY}\n${KANBAN_BODY}`,
            extraModules: ['dispatcher.js', 'kanban.js'],
        }));
});

function _run(overrides = {}) {
    return {
        task_id: 't-1', title: 'a task', status: 'running', pr_state: null,
        started_at: '2026-09-09T00:00:00Z', ended_at: null, attempt: 1,
        pr_url: null, pr_checks: null, branch_name: 'cmx-1', window_name: 'agent-1',
        ...overrides,
    };
}

function _dispatcherPayload(run) {
    return {
        configured: true,
        workflows: [{
            path: '/x/WORKFLOW.md', project_key: 'CMX', open_tasks: [],
            active_runs: [run], awaiting_review_runs: [], recent_runs: [],
        }],
    };
}

function _kanbanPayload(run) {
    return {
        configured: true,
        workflows: [{
            path: '/x/WORKFLOW.md', project_key: 'CMX', open_tasks: [], backlog_items: [],
            parked_tasks: [], active_runs: [], awaiting_review_runs: [], recent_runs: [run],
        }],
    };
}

const TASKS = {
    total: 4, done: 3,
    in_progress: { id: 't-4', subject: 'wire the tooltip guard' },
    blocked: [
        { id: 't-5', subject: 'ship the release notes', blocked_by: ['t-1', 't-2'] },
        { id: 't-6', subject: 'audit the migration', blocked_by: [] },
    ],
};

// --- dispatcher.js's runs table: the WIRING call site --------------------------------

test('the dispatcher runs table renders the run\'s ACTUAL done/total progress inside its Tasks column, not an empty cell', () => {
    renderDispatcher(_dispatcherPayload(_run({ tasks: TASKS })));

    const row = document.querySelector('.dispatcher-table tbody tr');
    assert.ok(row, 'no dispatcher run row rendered — check renderDispatcher/_renderRunsTable');
    const cell = row.querySelector('td[data-label="Tasks"]');
    assert.ok(cell, 'the Tasks column cell is missing — check the <th>Tasks</th> wiring');

    const chip = cell.querySelector('.task-progress-chip');
    assert.ok(chip, 'the Tasks cell rendered no .task-progress-chip — _cell(\'Tasks\', ' +
        '_taskProgressChip(r.tasks)) is not wired, or the call site was swapped for a constant');
    assert.match(chip.textContent, /3\/4/,
        `the chip must show the run's actual done/total label ("3/4") — got: "${chip.textContent}"`);
});

test('the dispatcher runs table renders NOTHING in the Tasks column when a run has no task data — the counterweight guard', () => {
    renderDispatcher(_dispatcherPayload(_run({ task_id: 't-2', tasks: null })));

    const row = document.querySelector('.dispatcher-table tbody tr');
    const cell = row.querySelector('td[data-label="Tasks"]');
    assert.ok(cell, 'the Tasks column cell is missing');
    assert.equal(cell.querySelector('.task-progress-chip'), null,
        'a run with tasks:null must render no chip at all — never "0/0"');
});

test('the dispatcher runs table\'s <th>Tasks</th> header stays aligned with the Tasks <td> — dropping or moving the header must not go unnoticed', () => {
    renderDispatcher(_dispatcherPayload(_run({ tasks: TASKS })));

    const headers = [...document.querySelectorAll('.dispatcher-table thead th')];
    const row = document.querySelector('.dispatcher-table tbody tr');
    const cells = [...row.querySelectorAll('td')];
    assert.equal(headers.length, cells.length,
        `header/cell count mismatch (${headers.length} <th> vs ${cells.length} <td>) — a ` +
        'dropped <th> shifts every column in the table under its neighbour\'s heading');

    const tasksHeaderIndex = headers.findIndex(th => th.textContent.trim() === 'Tasks');
    assert.notEqual(tasksHeaderIndex, -1, 'no <th>Tasks</th> found in the dispatcher runs table header');
    const tasksCellIndex = cells.findIndex(td => td.getAttribute('data-label') === 'Tasks');
    assert.equal(tasksHeaderIndex, tasksCellIndex,
        'the <th>Tasks</th> header is not at the same column index as the Tasks <td> — the ' +
        'header and cell have drifted apart');
});

// --- the chip's tooltip: the current in-progress task's subject ----------------------

test('the chip\'s tooltip names the CURRENT in-progress task\'s subject', () => {
    renderDispatcher(_dispatcherPayload(_run({ task_id: 't-3', tasks: TASKS })));

    const chip = document.querySelector('.dispatcher-table .task-progress-chip');
    assert.ok(chip, 'setup: no chip rendered');
    assert.match(chip.getAttribute('title') || '', /In progress: wire the tooltip guard/,
        `the chip's tooltip must name the in-progress task's subject — got title: ` +
        `"${chip.getAttribute('title')}"`);
});

// --- the chip's tooltip: blocked-by relationships (the changelog's sibling promise) --

test('the chip\'s tooltip also names each BLOCKED task, with its blocked-by ids when present', () => {
    renderDispatcher(_dispatcherPayload(_run({ task_id: 't-3b', tasks: TASKS })));

    const chip = document.querySelector('.dispatcher-table .task-progress-chip');
    assert.ok(chip, 'setup: no chip rendered');
    const title = chip.getAttribute('title') || '';
    assert.match(title, /"ship the release notes" blocked by t-1, t-2/,
        `the chip's tooltip must name a blocked task and its blocked-by ids — got title: "${title}"`);
    assert.match(title, /"audit the migration" blocked(?!\s+by)/,
        `a blocked task with no blocked-by ids must still be named, without a dangling "by" — got title: "${title}"`);
});

// --- kanban.js's card renderer: the SECOND, independently-guarded WIRING call site ----

test('a kanban card renders the SAME task-progress chip as the dispatcher table — kanban.js\'s own call site, not shared code with dispatcher.js\'s', () => {
    renderKanban(_kanbanPayload(_run({ task_id: 't-card', tasks: TASKS, status: 'running' })));

    const card = document.querySelector('.kanban-card[data-task-id="t-card"]');
    assert.ok(card, 'no kanban card rendered for the run — check renderKanban/_kCard');

    const chip = card.querySelector('.task-progress-chip');
    assert.ok(chip, 'the kanban card rendered no .task-progress-chip — kanban.js\'s ' +
        '`const taskChip = _taskProgressChip(card.tasks);` call site is not wired into the ' +
        'card, or was swapped for a constant');
    assert.match(chip.textContent, /3\/4/,
        `the kanban card's chip must show the run's actual done/total label ("3/4") — got: ` +
        `"${chip.textContent}"`);
});

test('a kanban card with no task data renders no chip at all — the counterweight guard, kanban.js\'s own call site', () => {
    renderKanban(_kanbanPayload(_run({ task_id: 't-card-none', tasks: null, status: 'running' })));

    const card = document.querySelector('.kanban-card[data-task-id="t-card-none"]');
    assert.ok(card, 'setup: no kanban card rendered');
    assert.equal(card.querySelector('.task-progress-chip'), null,
        'a card whose run has tasks:null must render no chip at all — never "0/0"');
});
