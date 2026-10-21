// CMX-290 — a real CLICK on a real kanban card actually opens the task modal.
//
// The two DASHBOARD SUITE members closest to this surface each prove one
// piece and stop short of the boundary a user's mouse actually crosses:
//   - tests/kanban_flatten.test.mjs drives renderKanban() and reads the
//     rendered card back, but never clicks it.
//   - features/taskmodal/tests calls `openTaskModal(item)` directly — proving
//     the modal's OWN rendering, but not that a click on a card ever reaches
//     that call, nor that the modal actually becomes VISIBLE the way a user
//     watching the screen would see it.
// Nothing anywhere simulates the actual chain a click travels: the card's
// `onclick="chela.openTaskModalFromCard(this)"` attribute (kanban.js) ->
// `_kanbanCardIndex` lookup -> `openTaskModal()` (features/taskmodal, CMX-228) ->
// the task modal island's dialog on the page. A judge mutation that drops the
// onclick attribute, breaks the `_kanbanCardIndex` lookup, or never mounts the
// island would leave every other test green.
//
// Both fixtures below are `sliceTemplate()`d straight out of the REAL
// templates/index.html — not hand-typed copies — so this also closes the
// template-drift half of the same gap: a rename of `#kanban-board` or the
// island's `#task-modal-island` slot shows up here, not just in a fixture that
// happens to still agree with today's markup.
//
// Run: pnpm exec vitest run tests/kanban_task_modal_wiring.test.mjs (tests/test_js_suites.py
// runs every .test.mjs inside pytest, by discovery).
import { beforeAll, test } from 'vitest';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';   // needs `pnpm install` — tests/test_js_suites.py enforces it
import { bootDashboardDom, clickIsland, clickOnclick, flush, sliceTemplate } from './js_helpers/dashboard_dom.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const STYLE_CSS = fs.readFileSync(
    path.join(HERE, '..', 'chela', 'dashboard', 'static', 'style.css'), 'utf8');

const KANBAN_BOARD_HTML = sliceTemplate(
    '<div class="work-pane active" id="work-board" data-seg="board">', '<!-- /work-board -->');
const MODAL_TASK_HTML = sliceTemplate('<div id="task-modal-island">', '</div>');

let renderKanban, taskmodal;

beforeAll(async () => {
    ({ modules: { kanban: { renderKanban } } } = await bootDashboardDom({
        body: `${KANBAN_BOARD_HTML}\n${MODAL_TASK_HTML}`,
        extraModules: ['kanban.js'],
    }));
    taskmodal = await import('../chela/dashboard/frontend/src/features/taskmodal/index.ts');
});

/** The task modal's dialog is on the page (Radix portals it to <body>). */
const modalOpen = () => !!document.querySelector('[role="dialog"].modal-task');
/** Its title's rendered inline markdown. */
const titleHtml = () => document.querySelector('#task-modal-content .task-modal-title > span')?.innerHTML;

async function click(el) {
    clickOnclick(el);
    await flush();
}

async function closeModal() {
    taskmodal.closeTaskModal();
    await flush();
}

function _payload(run) {
    return {
        configured: true,
        workflows: [{
            path: '/x/WORKFLOW.md', project_key: 'CMX', open_tasks: [], backlog_items: [],
            active_runs: [], awaiting_review_runs: [], recent_runs: [run],
        }],
    };
}

test('clicking a REAL rendered kanban card opens the REAL task modal, visibly, with THAT card\'s content — and no function of the fixture alone (index 0, last, middle, or any other f(length)) can fake it', async () => {
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

    assert.ok(document.getElementById('task-modal-island'), 'sliceTemplate did not carry #task-modal-island into the fixture');
    assert.equal(modalOpen(), false,
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
    await click(cardA);

    // 🔴 GUARD: the modal must actually appear: the island's dialog on the page
    // is the only thing a user watching the screen would see happen.
    assert.equal(modalOpen(), true,
        'the task modal never became visible after clicking card A — openTaskModalFromCard -> ' +
        'openTaskModal -> island chain is broken');

    let title = titleHtml();
    assert.ok(title != null, 'the task modal opened with no .task-modal-title rendered');
    assert.equal(title, 'ship <strong>the wall</strong> now',
        `task modal title does not match card A — got: "${title}"`);

    // 🔴 CLICK B — SAME render, SAME fixture length, a DIFFERENT clicked
    // element. Whatever answered "card A" for the click above (correctly, by
    // reading el.dataset.kidx, OR by accident via a positional formula that
    // happens to equal card A's index) must now answer "card B" — which no
    // f(fixture) can do, since f(3) cannot be two different values at once.
    await click(cardB);

    assert.equal(modalOpen(), true,
        'the task modal closed or never re-opened after clicking card B');

    title = titleHtml();
    assert.ok(title != null, 'the task modal opened with no .task-modal-title rendered');
    assert.equal(title, 'raise the <strong>second</strong> gate',
        `task modal title does not match card B (still showing a stale/wrong card) — got: "${title}"`);
});

test('clicking either of TWO REAL rendered BACKLOG cards — _kCard\'s OTHER renderer, never driven by the test above — opens the REAL task modal with THAT card\'s content, not a hardcoded first one', async () => {
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

    await closeModal();

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
    await click(cardA);
    assert.equal(modalOpen(), true,
        'clicking backlog card A never opened the task modal — the backlog branch\'s wiring is broken');
    let title = titleHtml();
    assert.ok(title != null, 'the task modal opened with no .task-modal-title rendered');
    assert.equal(title, 'the FIRST backlog bullet needs its own click',
        `task modal title does not match backlog card A — got: "${title}"`);

    // 🔴 CLICK B — same render, same fixture length, a different clicked
    // element. A hardcoded `data-kidx="0"` (shape 38) would resolve THIS
    // click back to card A's object too, since card A is index 0 here —
    // only an actual el.dataset.kidx read can tell card B apart.
    await click(cardB);
    assert.equal(modalOpen(), true,
        'the task modal closed or never re-opened after clicking backlog card B');
    title = titleHtml();
    assert.ok(title != null, 'the task modal opened with no .task-modal-title rendered');
    assert.equal(title, 'the SECOND backlog bullet needs its own click',
        `task modal title does not match backlog card B (still showing card A, or a stale card) — got: "${title}"`);
});

test('clicking either of TWO REAL rendered PARKED cards — _kCard\'s THIRD renderer, added by CMX-298 — opens the REAL task modal wired to THAT card, with its `raw` bullet text intact through the JS flatten hop', async () => {
    // _kCard now returns from THREE places (run-backed, backlog, parked). The two tests
    // above each cover one of the first two branches; this is the third, and it carries
    // its own risk the other two don't: a parked task object has no `brief` and no
    // `body`, so taskmodalmodel.js's briefSource(item) (brief -> body -> raw) has `raw`
    // as its ONLY source for the modal's brief pane. tests/test_taskmodal_data.py pins
    // `raw` surviving the /api/dispatcher hop (server side); nothing before this pinned
    // it surviving _kanbanFlatten's OWN `raw: t.raw` copy (client side) — the judge
    // proved nulling that line left every existing test green, because none of them
    // read the flattened card object or opened the modal for a parked card.
    //
    // Same shape-38 counterweight as the backlog test above: two parked cards, same
    // render, click both, assert each shows ITS OWN title AND its own raw-sourced brief
    // text. A hardcoded index or a card missing its `raw` payload can satisfy at most
    // one assertion below, never both for both cards.
    renderKanban({
        configured: true,
        workflows: [{
            path: '/x/WORKFLOW.md', project_key: 'CMX',
            open_tasks: [], active_runs: [], awaiting_review_runs: [], recent_runs: [], backlog_items: [],
            parked_tasks: [
                {
                    id: 'p-card-a', title: 'parked bullet A', file: 'TODO.md', line_number: 10,
                    raw: 'the RAW body unique to parked card A', reason: 'waiting on fixtures',
                },
                {
                    id: 'p-card-b', title: 'parked bullet B', file: 'TODO.md', line_number: 20,
                    raw: 'the RAW body unique to parked card B', reason: 'waiting on review',
                },
            ],
        }],
    });

    await closeModal();

    const cards = document.querySelectorAll('.kanban-card-parked');
    assert.equal(cards.length, 2,
        'setup: expected exactly 2 rendered parked cards — check the parked_tasks fixture above');
    const [cardA, cardB] = cards;
    assert.match(cardA.getAttribute('onclick') || '', /chela\.openTaskModalFromCard\(this\)/,
        'parked card A is not wired to chela.openTaskModalFromCard(this) — this is the THIRD _kCard ' +
        'renderer, not covered by the run-backed or backlog click tests above');
    assert.match(cardB.getAttribute('onclick') || '', /chela\.openTaskModalFromCard\(this\)/,
        'parked card B is not wired to chela.openTaskModalFromCard(this)');
    assert.notEqual(cardA.dataset.kidx, cardB.dataset.kidx,
        'setup: parked card A and B claimed the SAME data-kidx — the fixture above is not rendering two ' +
        'distinct cards');

    // 🔴 CLICK A
    await click(cardA);
    assert.equal(modalOpen(), true,
        'clicking parked card A never opened the task modal — the parked branch\'s onclick wiring is broken');

    let title = titleHtml();
    assert.ok(title != null, 'the task modal opened with no .task-modal-title rendered');
    assert.equal(title, 'parked bullet A',
        `task modal title does not match parked card A — got: "${title}"`);

    // 🔴 GUARD: raw must survive _kanbanFlatten's copy onto the card object. A parked
    // task has no brief/body, so this is the ONLY way its brief pane can show anything
    // but "No brief recorded for this task."
    let briefEl = document.querySelector('#task-modal-content .task-modal-brief');
    assert.ok(briefEl, 'the task modal opened with no .task-modal-brief rendered');
    assert.match(briefEl.textContent, /the RAW body unique to parked card A/,
        `the modal's brief pane does not show parked card A's raw bullet text — kanban.js is dropping ` +
        `\`raw\` on the _kanbanFlatten hop — got: "${briefEl.textContent}"`);

    // 🔴 CLICK B — SAME render, a DIFFERENT clicked element.
    await click(cardB);
    assert.equal(modalOpen(), true,
        'the task modal closed or never re-opened after clicking parked card B');

    title = titleHtml();
    assert.ok(title != null, 'the task modal opened with no .task-modal-title rendered');
    assert.equal(title, 'parked bullet B',
        `task modal title does not match parked card B (still showing a stale/wrong card) — got: "${title}"`);

    briefEl = document.querySelector('#task-modal-content .task-modal-brief');
    assert.ok(briefEl, 'the task modal opened with no .task-modal-brief rendered');
    assert.match(briefEl.textContent, /the RAW body unique to parked card B/,
        `the modal's brief pane does not show parked card B's raw bullet text (still showing card A's, ` +
        `or dropped entirely) — got: "${briefEl.textContent}"`);
});

test('the open task modal is VISIBLE under the REAL style.css: its .modal-overlay.active overlay computes display:flex', async () => {
    // The click tests above assert the dialog is in the DOM. The OTHER half of
    // "visibly" is that `.modal-overlay`'s CSS gates screen visibility on the
    // `.active` class the kit's Dialog renders: a flip of `.modal-overlay.active`'s
    // `display` from `flex` to `none` leaves the dialog in the DOM and off the
    // screen. This mounts the dialog's REAL rendered overlay under the REAL
    // style.css in jsdom (which resolves the cascade) and reads the computed value.
    renderKanban(_payload({
        task_id: 't-css', title: 'a task', status: 'done', pr_state: 'merged',
        started_at: '2026-08-01T00:00:00Z', ended_at: '2026-08-01T01:00:00Z',
        attempt: 1, pr_url: null, pr_checks: null, branch_name: 'cmx-3',
    }));
    await click(document.querySelector('.kanban-card[data-task-id="t-css"]'));
    const overlay = document.querySelector('[role="dialog"].modal-task')?.parentElement;
    assert.ok(overlay, 'setup: the task modal did not open');
    const cssDom = new JSDOM(
        `<!doctype html><html><head><style>${STYLE_CSS}</style></head><body>${overlay.outerHTML}</body></html>`,
        { pretendToBeVisual: true });
    const copy = cssDom.window.document.body.firstElementChild;
    assert.equal(cssDom.window.getComputedStyle(copy).display, 'flex',
        'the open task modal\'s overlay does not cascade to display:flex — it never becomes visible');
    copy.classList.remove('active');
    assert.equal(cssDom.window.getComputedStyle(copy).display, 'none',
        '.modal-overlay without .active must stay hidden — the visibility gate is gone');
    await closeModal();
});

test('clicking the REAL close button hides the REAL task modal', async () => {
    // Earlier tests in this file leave the modal open (this suite shares one
    // DOM). Force it CLOSED first so the "setup: the modal did not open"
    // assertion proves this test's own click opened it.
    await closeModal();

    renderKanban(_payload({
        task_id: 't-close', title: 'a task', status: 'done', pr_state: 'merged',
        started_at: '2026-08-01T00:00:00Z', ended_at: '2026-08-01T01:00:00Z',
        attempt: 1, pr_url: null, pr_checks: null, branch_name: 'cmx-2',
    }));
    await click(document.querySelector('.kanban-card[data-task-id="t-close"]'));
    assert.equal(modalOpen(), true, 'setup: the modal did not open');

    const closeBtn = document.querySelector('.task-modal-close');
    assert.ok(closeBtn, 'the modal has no .task-modal-close button');
    clickIsland(closeBtn);
    await flush();

    assert.equal(modalOpen(), false, 'the close button did not actually hide the modal');
});
