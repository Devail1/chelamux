// KANBAN BOARD RENDER — pins _kanbanFlatten's per-status bucketing of `recent_runs`
// (chela/dashboard/static/js/kanban.js), the piece tests/kanban_lane_model.test.mjs
// cannot reach: laneOf() alone proves the STRING 'closed' maps to the archived lane,
// but nothing proved a `closed` run arriving in `recent_runs` actually KEEPS its
// 'closed' status on the way there. A mutation collapsing
// `(r.status === 'done' || r.status === 'failed' || r.status === 'closed') ? r.status
// : 'done'` back to dropping the `closed` arm silently coerces every closed-not-merged
// row into a 'done' card — and the rest of the suite stayed green, because nothing
// exercised the real DOM this function produces.
//
// Runs the REAL kanban.js (renderKanban) against a REAL DOM (jsdom) — same
// module-graph-import approach as tests/dispatch_hold.test.mjs — and asserts on the
// rendered card/column HTML, not a source grep.
//
// Run: node --test tests/kanban_flatten.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `npm ci` for jsdom.)
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import { KANBAN_LANE_LABELS } from '../chela/dashboard/static/js/kanbanlanemodel.js';
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

before(async () => {
    // Same module-graph-import approach as tests/dispatch_hold.test.mjs: main.js
    // first so the whole app module graph (kanban.js included) evaluates against
    // a real window, then pull renderKanban out of the same cached instance.
    ({ modules: { kanban: { renderKanban } } } = await bootDashboardDom({
        body: BODY, extraModules: ['kanban.js'],
    }));
});

function _payload(recentRuns) {
    return {
        configured: true,
        workflows: [{
            path: '/x/WORKFLOW.md',
            project_key: 'CMX',
            open_tasks: [],
            backlog_items: [],
            active_runs: [],
            awaiting_review_runs: [],
            recent_runs: recentRuns,
        }],
    };
}

function _run(overrides) {
    return {
        task_id: 't1', title: 'a task', status: 'closed',
        started_at: '2026-08-01T00:00:00Z', ended_at: '2026-08-01T01:00:00Z',
        attempt: 1, pr_url: null, pr_state: 'closed', pr_checks: null,
        branch_name: 'cmx-1', ...overrides,
    };
}

// --- 1. 🔴 a closed-not-merged run renders in Archived, as its OWN status ---------

test('renderKanban: a closed-not-merged run renders in the archived column, not done', () => {
    renderKanban(_payload([_run({ status: 'closed' })]));

    const archivedCol = document.querySelector('.kanban-col-archived');
    const doneCol = document.querySelector('.kanban-col-done');
    assert.ok(archivedCol, '#kanban-board has no .kanban-col-archived column');
    assert.ok(doneCol, '#kanban-board has no .kanban-col-done column');

    const closedCard = archivedCol.querySelector('.kanban-card-closed');
    assert.ok(closedCard,
        'the closed run never reached the archived column as a kanban-card-closed card');
    assert.equal(doneCol.querySelectorAll('.kanban-card').length, 0,
        'a closed-not-merged run was coerced into the Done column');

    // Round 7 (PR #334): the archived state must be readable WITHOUT colour — the pill
    // needs a rendered WORD, not just a class a red-weak reader can't see. A mutation
    // that empties the pill's label while leaving STATUS_CHIPS.closed's source literal
    // and the escHtml(chipMeta.label) call intact left every class-only assertion here
    // green, so this checks rendered textContent instead of a CSS hook.
    const stateChip = closedCard.querySelector('.kanban-state-chip');
    assert.ok(stateChip, 'the closed card has no .kanban-state-chip pill at all');
    assert.match(stateChip.textContent, /closed, not merged/,
        `closed card's status pill has no readable label text — got: "${stateChip.textContent}"`);
});

// --- 2. ⭐ COUNTERWEIGHT — a genuinely done run still lands in Done, not archived ----
//
// Without this, always routing recent_runs into 'archived' would also satisfy test 1.

test('renderKanban: a genuinely done run still renders in the done column, not archived', () => {
    renderKanban(_payload([_run({ status: 'done', pr_state: 'merged' })]));

    const archivedCol = document.querySelector('.kanban-col-archived');
    const doneCol = document.querySelector('.kanban-col-done');
    assert.equal(archivedCol.querySelectorAll('.kanban-card').length, 0,
        'a genuinely done run leaked into the archived column');
    assert.ok(doneCol.querySelector('.kanban-card-done'),
        'a genuinely done run never reached the done column');
});

// --- 3. ⭐ GUARD (round 8, PR #334) — the lane HEAD label reaches the DOM ------------
//
// tests/kanban_lane_model.test.mjs's "lane label: archived lane says 'Archived' in
// words" only ever asserted the KANBAN_LANE_LABELS.archived source constant — it
// never proved that constant reaches the rendered column head. _kCol interpolates
// `label` straight into `<span class="kanban-col-label">${label}</span>`
// (kanban.js); emptying that interpolation leaves every lane head on the board
// wordless — hue and card count are the only cue left, which fails Liav (red-weak)
// specifically. This renders the REAL board and reads the REAL span's textContent,
// so it goes red on that exact corruption.

test('renderKanban: the Archived and Done lane heads render their labels as text, distinct from each other', () => {
    renderKanban(_payload([_run({ status: 'closed' }), _run({ task_id: 't2', status: 'done', pr_state: 'merged' })]));

    const archivedLabel = document.querySelector('.kanban-col-archived .kanban-col-label');
    const doneLabel = document.querySelector('.kanban-col-done .kanban-col-label');
    assert.ok(archivedLabel, '.kanban-col-archived has no .kanban-col-label span');
    assert.ok(doneLabel, '.kanban-col-done has no .kanban-col-label span');

    // 🔴 GUARD: this is the assertion the source-only test above could not make —
    // if _kCol's `${label}` interpolation is dropped, both spans go empty and this
    // fails while the source-constant test stays green.
    assert.equal(archivedLabel.textContent, KANBAN_LANE_LABELS.archived,
        `archived lane head does not render its label — got: "${archivedLabel.textContent}"`);
    assert.equal(doneLabel.textContent, KANBAN_LANE_LABELS.done,
        `done lane head does not render its label — got: "${doneLabel.textContent}"`);
    assert.notEqual(archivedLabel.textContent, doneLabel.textContent,
        'archived and done lane heads render identical text — no cue beyond colour');
});

// --- 4. 🔴 GUARD (round 3, PR #350) — a card title actually goes through knInline --------
//
// kanban.js's header comment claims knowledge.js's knInline is "reused verbatim ... by
// kanban.js for inline card text", and _kCard's own comment says the same — but every
// existing card fixture in this file uses a plain title ('a task'), for which knInline is
// the identity function, so the call at kanban.js:152 could be dead-coded (bypassed
// entirely, falling straight back to `displayTitle(...)` with no HTML escaping at all) and
// nothing above would notice. This drives a title with a mid-string `**bold**` span — the
// one case displayTitle() deliberately leaves untouched (see taskmodal_model.test.mjs) so
// knInline is the thing that has to render it — through the REAL renderKanban() and reads
// the REAL card title element back.

test('renderKanban: a card title with a mid-string bold span renders through knInline as <strong>, not literal asterisks', () => {
    renderKanban(_payload([_run({ title: 'ship **the wall** now', status: 'done', pr_state: 'merged' })]));

    const titleEl = document.querySelector('.kanban-card-done .kanban-card-title');
    assert.ok(titleEl, 'the done card has no .kanban-card-title element');
    assert.equal(titleEl.innerHTML, 'ship <strong>the wall</strong> now',
        `kanban card title did not render through knInline — got: "${titleEl.innerHTML}"`);
});
