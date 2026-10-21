// ⚖️🎚️ CMX-405 — a run's RISK level is VISIBLE on its Work-board card. Runs the REAL
// kanban.js (renderKanban) against a real DOM and reads the rendered chip back. riskChip()'s
// own mapping and the task modal's chip are tested in features/taskmodal/tests (CMX-228).
//
// Run: pnpm exec vitest run tests/risk_chip.test.mjs (tests/test_js_suites.py runs every .test.mjs
// inside pytest, by discovery; needs `pnpm install` for jsdom.)
import { beforeAll, test } from 'vitest';
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
</div>`;

let renderKanban;

beforeAll(async () => {
    ({ modules: { kanban: { renderKanban } } } = await bootDashboardDom({
        body: BODY, extraModules: ['kanban.js'],
    }));
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
