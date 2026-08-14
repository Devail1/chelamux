// CMX-290 — a real CLICK on a real kanban card actually opens the task modal.
//
// The two DASHBOARD SUITE members closest to this surface each prove one
// piece and stop short of the boundary a user's mouse actually crosses:
//   - tests/kanban_flatten.test.mjs drives renderKanban() and reads the
//     rendered card back, but never clicks it.
//   - tests/taskmodal_render.test.mjs calls `taskmodal.openTaskModal(item)`
//     directly — proving the modal's OWN rendering, but not that a click on a
//     card ever reaches that call, nor that the modal actually becomes
//     VISIBLE (showModal()'s `.active` class toggle) the way a user watching
//     the screen would see it.
// Nothing anywhere simulates the actual chain a click travels: the card's
// `onclick="chela.openTaskModalFromCard(this)"` attribute (kanban.js) ->
// `_kanbanCardIndex` lookup -> `openTaskModal()` (taskmodal.js) ->
// `showModal('modal-task')` (util.js, adds `.active`). A judge mutation that
// drops the onclick attribute, breaks the `_kanbanCardIndex` lookup, or
// deletes the `showModal()` call would leave every existing test green.
//
// Both fixtures below are `sliceTemplate()`d straight out of the REAL
// templates/index.html — not hand-typed copies — so this also closes the
// template-drift half of the same gap: a rename of `#kanban-board` or
// `#modal-task` (or the loss of `modal-overlay`'s visibility-gating class)
// shows up here, not just in a fixture that happens to still agree with
// today's markup.
//
// Run: node --test tests/kanban_task_modal_wiring.test.mjs (tests/test_js_suites.py
// runs every .test.mjs inside pytest, by discovery).
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import { bootDashboardDom, clickOnclick, sliceTemplate } from './js_helpers/dashboard_dom.mjs';

const KANBAN_BOARD_HTML = sliceTemplate(
    '<div class="work-pane active" id="work-board" data-seg="board">', '<!-- /work-board -->');
const MODAL_TASK_HTML = sliceTemplate(
    '<div class="modal-overlay" id="modal-task">', '<!-- /modal-task -->');

let renderKanban;

before(async () => {
    ({ modules: { kanban: { renderKanban } } } = await bootDashboardDom({
        body: `${KANBAN_BOARD_HTML}\n${MODAL_TASK_HTML}`,
        extraModules: ['kanban.js'],
    }));
});

function _payload(run) {
    return {
        configured: true,
        workflows: [{
            path: '/x/WORKFLOW.md', project_key: 'CMX', open_tasks: [], backlog_items: [],
            active_runs: [], awaiting_review_runs: [], recent_runs: [run],
        }],
    };
}

test('clicking a REAL rendered kanban card opens the REAL task modal, visibly, with that card\'s content', () => {
    // A DECOY card in the Open lane, which _KANBAN_BUCKET_ORDER (kanban.js)
    // renders BEFORE the Done lane the card under test lands in — so the
    // decoy claims data-kidx="0" and the real card gets a NON-ZERO index.
    // A single-card fixture can never prove the data-kidx lookup runs at
    // all: with exactly one card, index 0 IS the clicked card whether or
    // not openTaskModalFromCard reads `el.dataset.kidx` or just hardcodes
    // `_kanbanCardIndex[0]`. Two cards make those two behaviours diverge.
    renderKanban({
        configured: true,
        workflows: [{
            path: '/x/WORKFLOW.md', project_key: 'CMX',
            open_tasks: [{ id: 't-decoy', title: 'decoy — must never appear in the modal', raw: 'decoy', body: null }],
            backlog_items: [], active_runs: [], awaiting_review_runs: [],
            recent_runs: [{
                task_id: 't-wiring', title: 'ship **the wall** now', status: 'done', pr_state: 'merged',
                started_at: '2026-08-01T00:00:00Z', ended_at: '2026-08-01T01:00:00Z',
                attempt: 1, pr_url: null, pr_checks: null, branch_name: 'cmx-1',
            }],
        }],
    });

    const modal = document.getElementById('modal-task');
    assert.ok(modal, 'sliceTemplate did not carry #modal-task into the fixture');
    assert.equal(modal.classList.contains('active'), false,
        'the task modal starts open — the test below could not tell a real open from a no-op');

    const card = document.querySelector('.kanban-card[data-task-id="t-wiring"]');
    assert.ok(card, 'the rendered card is missing — check .kanban-card[data-task-id]');
    assert.match(card.getAttribute('onclick') || '', /chela\.openTaskModalFromCard\(this\)/,
        'the card is not wired to chela.openTaskModalFromCard(this)');
    assert.notEqual(card.dataset.kidx, '0',
        'setup: the clicked card claimed data-kidx 0 — the decoy card must render first, or a ' +
        'hardcoded _kanbanCardIndex[0] resolve would pass this test for the wrong reason');

    // 🔴 THE CLICK ITSELF — the same two hops (onclick attribute -> window.chela
    // -> handler) a real mouse click takes, compiled by clickOnclick() instead
    // of jsdom's HTML parser (jsdom does not execute inline onclick= on a
    // dispatched click event without runScripts:"dangerously").
    clickOnclick(card);

    // 🔴 GUARD: the modal must actually become VISIBLE. showModal('modal-task')
    // (util.js) adding `.active` to the REAL #modal-task (sliced from
    // index.html, whose `.modal-overlay` CSS gates display on that class) is
    // the only thing a user watching the screen would see happen.
    assert.equal(modal.classList.contains('active'), true,
        'the task modal never became visible — openTaskModalFromCard -> openTaskModal -> showModal chain is broken ' +
        'somewhere, even though the earlier guards below may still pass');

    // 🔴 GUARD: and it must show the CLICKED card's own content, not a stale
    // or wrong one — proves _kanbanCardIndex's data-kidx lookup actually
    // resolved to the object this click's card corresponds to.
    const titleEl = document.querySelector('#task-modal-content .task-modal-title');
    assert.ok(titleEl, 'the task modal opened with no .task-modal-title rendered');
    assert.equal(titleEl.innerHTML, 'ship <strong>the wall</strong> now',
        `task modal title does not match the clicked card — got: "${titleEl.innerHTML}"`);
});

test('clicking the REAL close button hides the REAL task modal', () => {
    renderKanban(_payload({
        task_id: 't-close', title: 'a task', status: 'done', pr_state: 'merged',
        started_at: '2026-08-01T00:00:00Z', ended_at: '2026-08-01T01:00:00Z',
        attempt: 1, pr_url: null, pr_checks: null, branch_name: 'cmx-2',
    }));
    const modal = document.getElementById('modal-task');
    clickOnclick(document.querySelector('.kanban-card[data-task-id="t-close"]'));
    assert.equal(modal.classList.contains('active'), true, 'setup: the modal did not open');

    const closeBtn = document.querySelector('.task-modal-close');
    assert.ok(closeBtn, 'the modal has no .task-modal-close button');
    assert.match(closeBtn.getAttribute('onclick') || '', /chela\.closeTaskModal\(\)/,
        'the close button is not wired to chela.closeTaskModal()');
    clickOnclick(closeBtn);

    assert.equal(modal.classList.contains('active'), false,
        'the close button did not actually hide the modal — closeTaskModal -> closeModal\'s `.active` removal is broken');
});
