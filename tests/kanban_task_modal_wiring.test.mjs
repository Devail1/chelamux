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
    // TWO decoys straddle the card under test — one BEFORE it, one AFTER —
    // so the clicked card's data-kidx is neither the first index nor the
    // last. A single decoy (first-only) leaves the LAST-registered card
    // indistinguishable from the data-kidx-read card: with only a
    // before-decoy, the card under test is *also* the most recently pushed
    // entry in _kanbanCardIndex, so a lookup that ignores data-kidx and
    // just resolves `_kanbanCardIndex[_kanbanCardIndex.length - 1]` (a
    // "most recent" shortcut) would pass identically to a real
    // `el.dataset.kidx` read. _KANBAN_BUCKET_ORDER / KANBAN_LANES
    // (kanban.js / kanbanlanemodel.js) render Open before Done before
    // Archived, so: decoy-first (open_tasks, todo lane) -> t-wiring
    // (recent_runs status=done, done lane) -> decoy-last (recent_runs
    // status=closed, archived lane). Neither a hardcoded index 0 NOR a
    // hardcoded "most recent" index can resolve to the middle card — only
    // an actual data-kidx read can.
    renderKanban({
        configured: true,
        workflows: [{
            path: '/x/WORKFLOW.md', project_key: 'CMX',
            open_tasks: [{ id: 't-decoy-first', title: 'decoy — must never appear in the modal', raw: 'decoy', body: null }],
            backlog_items: [], active_runs: [], awaiting_review_runs: [],
            recent_runs: [
                {
                    task_id: 't-wiring', title: 'ship **the wall** now', status: 'done', pr_state: 'merged',
                    started_at: '2026-08-01T00:00:00Z', ended_at: '2026-08-01T01:00:00Z',
                    attempt: 1, pr_url: null, pr_checks: null, branch_name: 'cmx-1',
                },
                {
                    task_id: 't-decoy-last', title: 'decoy — must never appear in the modal', status: 'closed', pr_state: 'closed',
                    started_at: '2026-08-01T00:00:00Z', ended_at: '2026-08-01T00:30:00Z',
                    attempt: 1, pr_url: null, pr_checks: null, branch_name: 'cmx-9',
                },
            ],
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

    const totalCards = document.querySelectorAll('.kanban-card').length;
    assert.equal(totalCards, 3,
        'setup: expected exactly 3 rendered cards (decoy-first + t-wiring + decoy-last) — check the fixture above');
    assert.notEqual(card.dataset.kidx, '0',
        'setup: the clicked card claimed data-kidx 0 — the before-decoy must render first, or a ' +
        'hardcoded _kanbanCardIndex[0] resolve would pass this test for the wrong reason');
    assert.notEqual(card.dataset.kidx, String(totalCards - 1),
        'setup: the clicked card claimed the LAST data-kidx — the after-decoy must render last, or a ' +
        'hardcoded "most recently registered card" resolve would pass this test for the wrong reason');

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
