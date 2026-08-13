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
import { JSDOM } from 'jsdom';
import { KANBAN_LANE_LABELS } from '../chela/dashboard/static/js/kanbanlanemodel.js';

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
    const dom = new JSDOM(`<!doctype html><html><body>${BODY}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
    for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement',
        'Element', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'CustomEvent',
        'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
        Object.defineProperty(globalThis, k, {
            value: dom.window[k], writable: true, configurable: true,
        });
    }
    dom.window.matchMedia = q => ({
        media: q, matches: false,
        addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
    });
    globalThis.fetch = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;
    globalThis.TERMINALS_ENABLED = dom.window.TERMINALS_ENABLED = true;

    // Same module-graph-import approach as tests/dispatch_hold.test.mjs: import
    // main.js first so the whole app module graph (kanban.js included) evaluates
    // against a real window, then pull renderKanban out of the same cached instance.
    await import('../chela/dashboard/static/js/main.js');
    ({ renderKanban } = await import('../chela/dashboard/static/js/kanban.js'));
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
