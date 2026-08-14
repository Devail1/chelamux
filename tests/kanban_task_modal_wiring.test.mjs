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
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';   // needs `npm ci` — tests/test_js_suites.py enforces it
import { bootDashboardDom, clickOnclick, sliceTemplate } from './js_helpers/dashboard_dom.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const STYLE_CSS = fs.readFileSync(
    path.join(HERE, '..', 'chela', 'dashboard', 'static', 'style.css'), 'utf8');

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

test('clicking a REAL rendered kanban card opens the REAL task modal, visibly, with THAT card\'s content — and no function of the fixture alone (index 0, last, middle, or any other f(length)) can fake it', () => {
    // Earlier rounds tried to defeat a positional-shortcut lookup by placing
    // the card under test at a "safe" index — first a non-zero index, then a
    // non-last index, then (this round) discovered that even "the middle of
    // exactly 3" is itself a positional slot: Math.floor(length / 2) resolves
    // to it directly, no data-kidx read required. Chasing a safer index is an
    // arms race with no last round — every fixture of fixed size N has SOME
    // constant that lands on it.
    //
    // The fix that actually closes the class: click TWO different cards in
    // the SAME render (same fixture, same length) and assert a DIFFERENT
    // title for each. A positional lookup is a pure function of the fixture
    // — f(3) computes to exactly one value — so it can match at most one of
    // the two clicks. Hardcoded 0, hardcoded length-1, floor(length/2),
    // length-2, or any formula nobody has thought of yet: all of them fail
    // one of the two assertions below. Only an actual el.dataset.kidx read,
    // which sees WHICH element was clicked, can satisfy both in one render.
    renderKanban({
        configured: true,
        workflows: [{
            path: '/x/WORKFLOW.md', project_key: 'CMX',
            open_tasks: [{ id: 't-decoy', title: 'decoy — must never appear in the modal', raw: 'decoy', body: null }],
            backlog_items: [], active_runs: [], awaiting_review_runs: [],
            recent_runs: [
                {
                    task_id: 't-card-a', title: 'ship **the wall** now', status: 'done', pr_state: 'merged',
                    started_at: '2026-08-01T00:00:00Z', ended_at: '2026-08-01T01:00:00Z',
                    attempt: 1, pr_url: null, pr_checks: null, branch_name: 'cmx-1',
                },
                {
                    task_id: 't-card-b', title: 'raise the **second** gate', status: 'closed', pr_state: 'closed',
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

    const cardA = document.querySelector('.kanban-card[data-task-id="t-card-a"]');
    const cardB = document.querySelector('.kanban-card[data-task-id="t-card-b"]');
    assert.ok(cardA, 'card A is missing — check .kanban-card[data-task-id]');
    assert.ok(cardB, 'card B is missing — check .kanban-card[data-task-id]');
    assert.match(cardA.getAttribute('onclick') || '', /chela\.openTaskModalFromCard\(this\)/,
        'card A is not wired to chela.openTaskModalFromCard(this)');
    assert.match(cardB.getAttribute('onclick') || '', /chela\.openTaskModalFromCard\(this\)/,
        'card B is not wired to chela.openTaskModalFromCard(this)');

    const totalCards = document.querySelectorAll('.kanban-card').length;
    assert.equal(totalCards, 3,
        'setup: expected exactly 3 rendered cards (decoy + card A + card B) — check the fixture above');
    assert.notEqual(cardA.dataset.kidx, cardB.dataset.kidx,
        'setup: card A and card B claimed the SAME data-kidx — the fixture above is not rendering two distinct cards');

    // 🔴 CLICK A — the same two hops (onclick attribute -> window.chela ->
    // handler) a real mouse click takes, compiled by clickOnclick() instead
    // of jsdom's HTML parser (jsdom does not execute inline onclick= on a
    // dispatched click event without runScripts:"dangerously").
    clickOnclick(cardA);

    // 🔴 GUARD: the modal must actually become VISIBLE. showModal('modal-task')
    // (util.js) adding `.active` to the REAL #modal-task (sliced from
    // index.html, whose `.modal-overlay` CSS gates display on that class) is
    // the only thing a user watching the screen would see happen.
    assert.equal(modal.classList.contains('active'), true,
        'the task modal never became visible after clicking card A — openTaskModalFromCard -> ' +
        'openTaskModal -> showModal chain is broken');

    let titleEl = document.querySelector('#task-modal-content .task-modal-title');
    assert.ok(titleEl, 'the task modal opened with no .task-modal-title rendered');
    assert.equal(titleEl.innerHTML, 'ship <strong>the wall</strong> now',
        `task modal title does not match card A — got: "${titleEl.innerHTML}"`);

    // 🔴 CLICK B — SAME render, SAME fixture length, a DIFFERENT clicked
    // element. Whatever answered "card A" for the click above (correctly, by
    // reading el.dataset.kidx, OR by accident via a positional formula that
    // happens to equal card A's index) must now answer "card B" — which no
    // f(fixture) can do, since f(3) cannot be two different values at once.
    clickOnclick(cardB);

    assert.equal(modal.classList.contains('active'), true,
        'the task modal closed or never re-opened after clicking card B');

    titleEl = document.querySelector('#task-modal-content .task-modal-title');
    assert.ok(titleEl, 'the task modal opened with no .task-modal-title rendered');
    assert.equal(titleEl.innerHTML, 'raise the <strong>second</strong> gate',
        `task modal title does not match card B (still showing a stale/wrong card) — got: "${titleEl.innerHTML}"`);
});

test('clicking either of TWO REAL rendered BACKLOG cards — _kCard\'s OTHER renderer, never driven by the test above — opens the REAL task modal with THAT card\'s content, not a hardcoded first one', () => {
    // _kCard returns from TWO places: the backlog branch (no task_id, no
    // branch, no PR — just a BACKLOG.md bullet) and the run-backed branch the
    // test above exclusively drives. Both branches emit their OWN `data-kidx`
    // + `onclick="chela.openTaskModalFromCard(this)"` from source that is
    // duplicated, not shared (kanban.js:170 vs :244) — see docs/defeat_shapes/07
    // (two callers, one guarded).
    //
    // Round 3 closed shape 07 here (this test used to render exactly ONE
    // backlog card and prove the onclick attribute + the click reached the
    // modal) but that single-card fixture reintroduced shape 38 on this
    // SIBLING call site: with only one backlog card ever rendered, its real
    // `data-kidx` is always 0, so `_kCard`'s backlog branch could have
    // emitted the literal `data-kidx="0"` instead of the real `${kidx}` and
    // this test could not have told the difference — the judge proved
    // exactly that mutation stayed green. The fix is the same one shape 38's
    // own guard form prescribes: render two backlog cards in the SAME
    // render, click both, and assert each shows ITS OWN content. A hardcoded
    // `0` (or any other constant) can match at most one of the two clicks.
    renderKanban({
        configured: true,
        workflows: [{
            path: '/x/WORKFLOW.md', project_key: 'CMX',
            open_tasks: [], active_runs: [], awaiting_review_runs: [], recent_runs: [],
            backlog_items: [
                { text: 'the FIRST backlog bullet needs its own click', section: 'Now', file: 'TODO.md' },
                { text: 'the SECOND backlog bullet needs its own click', section: 'Now', file: 'TODO.md' },
            ],
        }],
    });

    const modal = document.getElementById('modal-task');
    modal.classList.remove('active');

    const cards = document.querySelectorAll('.kanban-card-backlog');
    assert.equal(cards.length, 2,
        'setup: expected exactly 2 rendered backlog cards — check the backlog_items fixture above');
    const [cardA, cardB] = cards;
    assert.match(cardA.getAttribute('onclick') || '', /chela\.openTaskModalFromCard\(this\)/,
        'backlog card A is not wired to chela.openTaskModalFromCard(this) — this is the OTHER _kCard ' +
        'renderer, not covered by the run-backed click test above');
    assert.match(cardB.getAttribute('onclick') || '', /chela\.openTaskModalFromCard\(this\)/,
        'backlog card B is not wired to chela.openTaskModalFromCard(this)');
    assert.notEqual(cardA.dataset.kidx, cardB.dataset.kidx,
        'setup: backlog card A and B claimed the SAME data-kidx — the fixture above is not rendering two ' +
        'distinct cards (or the backlog branch is emitting a constant instead of its render-order index)');

    // 🔴 CLICK A
    clickOnclick(cardA);
    assert.equal(modal.classList.contains('active'), true,
        'clicking backlog card A never opened the task modal — the backlog branch\'s wiring is broken');
    let titleEl = document.querySelector('#task-modal-content .task-modal-title');
    assert.ok(titleEl, 'the task modal opened with no .task-modal-title rendered');
    assert.equal(titleEl.innerHTML, 'the FIRST backlog bullet needs its own click',
        `task modal title does not match backlog card A — got: "${titleEl.innerHTML}"`);

    // 🔴 CLICK B — same render, same fixture length, a different clicked
    // element. A hardcoded `data-kidx="0"` (shape 38) would resolve THIS
    // click back to card A's object too, since card A is index 0 here —
    // only an actual el.dataset.kidx read can tell card B apart.
    clickOnclick(cardB);
    assert.equal(modal.classList.contains('active'), true,
        'the task modal closed or never re-opened after clicking backlog card B');
    titleEl = document.querySelector('#task-modal-content .task-modal-title');
    assert.ok(titleEl, 'the task modal opened with no .task-modal-title rendered');
    assert.equal(titleEl.innerHTML, 'the SECOND backlog bullet needs its own click',
        `task modal title does not match backlog card B (still showing card A, or a stale card) — got: "${titleEl.innerHTML}"`);
});

test('the REAL #modal-task VISIBLY appears when .active is added — .modal-overlay.active cascades to display:flex under the REAL style.css, not just a class toggling with nothing rendering it', () => {
    // The click tests above assert `modal.classList.contains('active')` —
    // that the CLASS was added. Neither one asserts the OTHER half of
    // "visibly": that `.modal-overlay`'s CSS actually gates screen visibility
    // on that class. A judge mutation that flips `.modal-overlay.active`'s
    // `display` from `flex` to `none` leaves classList.contains('active')
    // true while the modal never appears on screen — invisible to every
    // assertion above. This test mounts the REAL #modal-task fragment under
    // the REAL style.css in jsdom (which resolves CSS cascade/specificity,
    // same recipe as tests/wire_live_css.test.mjs) and reads the CASCADED
    // `display` value with getComputedStyle, closing that gap directly.
    const cssDom = new JSDOM(
        `<!doctype html><html><head><style>${STYLE_CSS}</style></head><body>${MODAL_TASK_HTML}</body></html>`,
        { pretendToBeVisual: true });
    const modal = cssDom.window.document.getElementById('modal-task');
    assert.ok(modal, 'sliceTemplate did not carry #modal-task into the fixture');

    assert.equal(cssDom.window.getComputedStyle(modal).display, 'none',
        'setup: #modal-task should start hidden (no .active class) — check .modal-overlay\'s base `display` rule');

    modal.classList.add('active');
    assert.equal(cssDom.window.getComputedStyle(modal).display, 'flex',
        '.modal-overlay.active must cascade to display:flex — otherwise the task modal never becomes ' +
        'visible even though .active was added (the classList assertions elsewhere in this file cannot see this)');
});

test('clicking the REAL close button hides the REAL task modal', () => {
    // Earlier tests in this file leave #modal-task open (they never close
    // it), and this suite shares one DOM (bootDashboardDom runs once in
    // `before`) — so entering this test the modal may already carry
    // `.active` regardless of what happens below. Force it CLOSED first so
    // the "setup: the modal did not open" assertion actually proves this
    // test's own click opened it, instead of passing vacuously off a
    // previous test's leftover state.
    document.getElementById('modal-task').classList.remove('active');

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
