// issue #462: the task-progress chip on a kanban card (kanban.js's own call site, through
// taskProgressChipHtml, frontend/src/features/runs/legacy.ts). A judge mutation battery once
// proved the call site, the label and the tooltip all survived with the suite green; this
// drives the real card render and reads the chip back. The Runs table's chip is the React
// table's (frontend/src/features/runs/tests/Runs.test.tsx, CMX-231).
//
// Run: pnpm exec vitest run tests/task_progress_chip.test.mjs (tests/test_js_suites.py runs every
// .test.mjs inside pytest, by discovery).
import { beforeAll, test } from 'vitest';
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

let renderKanban;

beforeAll(async () => {
    ({ modules: { kanban: { renderKanban } } } =
        await bootDashboardDom({ body: KANBAN_BODY, extraModules: ['kanban.js'] }));
});

function _run(overrides = {}) {
    return {
        task_id: 't-1', title: 'a task', status: 'running', pr_state: null,
        started_at: '2026-09-09T00:00:00Z', ended_at: null, attempt: 1,
        pr_url: null, pr_checks: null, branch_name: 'cmx-1', window_name: 'agent-1',
        ...overrides,
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

// --- kanban.js's card renderer: the WIRING call site ----------------------------------

test('a kanban card renders the run\'s task-progress chip, with its tooltip: kanban.js\'s own call site', () => {
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
    assert.equal(chip.getAttribute('title'), '3 of 4 tasks done\nIn progress: wire the tooltip guard\n'
        + '"ship the release notes" blocked by t-1, t-2\n"audit the migration" blocked');
});

test('a kanban card with no task data renders no chip at all — the counterweight guard, kanban.js\'s own call site', () => {
    renderKanban(_kanbanPayload(_run({ task_id: 't-card-none', tasks: null, status: 'running' })));

    const card = document.querySelector('.kanban-card[data-task-id="t-card-none"]');
    assert.ok(card, 'setup: no kanban card rendered');
    assert.equal(card.querySelector('.task-progress-chip'), null,
        'a card whose run has tasks:null must render no chip at all — never "0/0"');
});
