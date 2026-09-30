// ⚖️🎚️ CMX-405 — a run's RISK level is VISIBLE: on its Work-board card and in its task
// modal. Runs the REAL kanban.js (renderKanban) and taskmodal.js (openTaskModal) against a
// real DOM and reads the rendered chip back, plus riskChip()'s own mapping.
//
// Run: node --test tests/risk_chip.test.mjs (tests/test_js_suites.py runs every .test.mjs
// inside pytest, by discovery; needs `pnpm install` for jsdom.)
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import { bootDashboardDom } from './js_helpers/dashboard_dom.mjs';

const BODY = `
<div class="work-pane active" id="work-board" data-seg="board">
  <div class="kanban-filters" id="kanban-filters"></div>
  <div class="kanban-mobile-controls" id="kanban-mobile-controls">
    <div class="kanban-nav-strip" id="kanban-nav-strip" aria-label="Jump to column"></div>
  </div>
  <div class="kanban-board" id="kanban-board"></div>
  <div id="kanban-empty" class="work-empty" style="display:none;"></div>
</div>
<div id="modal-task" class="modal">
  <div id="task-modal-content"></div>
</div>`;

let renderKanban;
let taskmodal;
let riskChip;

before(async () => {
    ({ modules: { kanban: { renderKanban }, taskmodal } } = await bootDashboardDom({
        body: BODY, extraModules: ['kanban.js', 'taskmodal.js'],
    }));
    // After the boot: taskmodalmodel.js -> knowledge.js -> util.js reads `window` at load.
    ({ riskChip } = await import('../chela/dashboard/static/js/taskmodalmodel.js'));
});

function _run(over) {
    return {
        task_id: 'abc123', title: 'do a thing', status: 'awaiting_review',
        workflow_path: '/x/WORKFLOW.md', task_number: 7, branch_name: 'cmx-7', ...over,
    };
}

function _payload(run) {
    return {
        configured: true,
        workflows: [{
            path: '/x/WORKFLOW.md', project_key: 'CMX', open_tasks: [], backlog_items: [],
            parked_tasks: [], active_runs: [], awaiting_review_runs: [run], recent_runs: [],
        }],
    };
}

test('riskChip: each level is its own WORD; anything else renders nothing', () => {
    assert.equal(riskChip({ risk: 'high' }).label, 'risk: high');
    assert.equal(riskChip({ risk: 'normal' }).label, 'risk: normal');
    assert.equal(riskChip({ risk: 'low' }).label, 'risk: low');
    assert.notEqual(riskChip({ risk: 'high' }).cls, riskChip({ risk: 'low' }).cls);
    assert.equal(riskChip({ risk: 'low', risk_reason: 'marker' }).reason, 'marker');
    assert.equal(riskChip({}), null);
    assert.equal(riskChip({ risk: null }), null);
    assert.equal(riskChip({ risk: 'extreme' }), null);
});

test('the Work-board card shows the run\'s risk level, with its reason as the tooltip', () => {
    renderKanban(_payload(_run({ risk: 'low', risk_reason: 'marker' })));
    const card = document.querySelector('.kanban-card[data-task-id="abc123"]');
    assert.ok(card, 'the run card did not render');
    const chip = card.querySelector('.kanban-risk-chip');
    assert.ok(chip, `no risk chip on the card: ${card.innerHTML}`);
    assert.equal(chip.textContent, 'risk: low');
    assert.equal(chip.getAttribute('title'), 'marker');

    renderKanban(_payload(_run({ risk: 'high', risk_reason: "inferred: BOUNDARIES touch 'judge.py'" })));
    const high = document.querySelector('.kanban-card[data-task-id="abc123"] .kanban-risk-chip');
    assert.equal(high.textContent, 'risk: high');
});

test('the task modal shows the run\'s risk level and why it has it', () => {
    taskmodal.openTaskModal(_run({ risk: 'high', risk_reason: "inferred: BOUNDARIES touch 'judge.py'" }));
    const content = document.querySelector('#task-modal-content');
    const chip = content.querySelector('.kanban-risk-chip');
    assert.ok(chip, `no risk chip in the modal: ${content.innerHTML}`);
    assert.equal(chip.textContent, 'risk: high');
    assert.match(content.innerHTML, /inferred: BOUNDARIES touch/);
});
