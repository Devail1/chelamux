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

function _payload(recentRuns, overrides = {}) {
    return {
        configured: true,
        workflows: [{
            path: '/x/WORKFLOW.md',
            project_key: 'CMX',
            open_tasks: [],
            backlog_items: [],
            parked_tasks: [],
            active_runs: [],
            awaiting_review_runs: [],
            recent_runs: recentRuns,
            ...overrides,
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

// --- 5. 🔴 GUARD (CMX-298) — a parked TODO.md bullet renders in the Backlog lane -----
//
// Before this, `wf.parked_tasks` did not exist in the payload at all, and even if it
// had, kanban.js had no bucket, no card branch and no lane mapping for it — a PARKED
// bullet was invisible on the whole board (Liav, 2026-08-12: "should we see parked in
// backlog?"). This drives the real payload shape `/api/dispatcher` now sends and reads
// the real DOM back: the card must land under the Backlog column head, as its own
// `.kanban-card-parked` (never `.kanban-card-backlog` — that class is reserved for
// actual BACKLOG.md bullets), carrying its blocked reason as visible text.

test('renderKanban: a parked TODO.md bullet renders as its own card in the Backlog lane', () => {
    renderKanban(_payload([], {
        parked_tasks: [{
            id: 'p1', title: 'Add a unit test for the config loader',
            file: '/x/TODO.md', line_number: 14, raw: '- [ ] ...',
            reason: 'waiting on fixtures',
        }],
    }));

    const backlogCol = document.querySelector('.kanban-col-backlog');
    assert.ok(backlogCol, '#kanban-board has no .kanban-col-backlog column');

    const parkedCard = backlogCol.querySelector('.kanban-card-parked');
    assert.ok(parkedCard,
        'the parked task never reached the Backlog column as a .kanban-card-parked card');
    assert.equal(backlogCol.querySelectorAll('.kanban-card-backlog').length, 0,
        'a parked task was rendered as a plain .kanban-card-backlog card, indistinguishable from a real BACKLOG.md bullet');

    assert.match(parkedCard.textContent, /Add a unit test for the config loader/,
        'the parked card does not show the task title');
    assert.match(parkedCard.textContent, /waiting on fixtures/,
        'the parked card does not show its blocked reason as visible text');

    // 🔴 GUARD (round 2, PR #372): the 🔒 cue must render on the WITH-reason branch
    // too, not just the reason-less fallback test 6 pins below. Dropping '🔒 ' from
    // this ternary's other arm left the reason TEXT intact (the assertion above
    // would still pass) while erasing the one thing that marks this a read-only
    // parked card rather than an arbitrary chip of text.
    const reasonEl = parkedCard.querySelector('.kanban-parked-reason');
    assert.ok(reasonEl, 'the parked card has no .kanban-parked-reason element');
    assert.match(reasonEl.textContent, /🔒/,
        `a parked card WITH a reason must still show the 🔒 lock cue — got: "${reasonEl.textContent}"`);

    // 🔴 GUARD (round 2, PR #372): _kanbanFlatten's own header comment claims
    // workflow_path is injected onto parked_tasks so the card still gets a workflow
    // chip. Without that injection, _wfName(undefined) renders the '?' fallback
    // instead of the real workflow name derived from the fixture's wf.path
    // ('/x/WORKFLOW.md' -> 'x') — this reads the rendered chip, not the source line.
    const wfChip = parkedCard.querySelector('.kanban-wf-chip');
    assert.ok(wfChip, 'the parked card has no .kanban-wf-chip element');
    assert.equal(wfChip.textContent, 'x',
        `parked card's workflow chip did not carry workflow_path — got: "${wfChip.textContent}"`);
});

// --- 6. ⭐ COUNTERWEIGHT — a parked card carries no Promote/delete affordance --------
//
// Without this, always rendering the backlog branch's Promote button for ANY card in
// the Backlog lane would also satisfy test 5 above (a parked card sitting right next
// to a real BACKLOG.md one, both promotable) — which would be wrong: a parked bullet
// is already in TODO.md, so "Promote" makes no sense for it.

test('renderKanban: a parked card has no Promote button', () => {
    renderKanban(_payload([], {
        parked_tasks: [{
            id: 'p1', title: 'a parked task', file: '/x/TODO.md',
            line_number: 3, raw: '- [ ] ...', reason: null,
        }],
    }));

    const parkedCard = document.querySelector('.kanban-card-parked');
    assert.ok(parkedCard, 'the parked task never reached the board');
    assert.equal(parkedCard.querySelector('.kanban-promote-btn'), null,
        'a parked card must not carry a Promote button — it is already in TODO.md');

    // 🔴 GUARD: a parked bullet with no `reason` (blocked with no `<!-- blocked:
    // ... -->` text) must still fall back to a visible '🔒 parked' cue — the lock
    // icon is the colourblind-safe signal that this card is read-only, distinct
    // from a real BACKLOG.md bullet, even when there is no reason text to show.
    // Emptying that fallback span (kanban.js's `reason ? ... : ` else-branch)
    // left the card with no cue at all and no assertion here caught it.
    const reasonEl = parkedCard.querySelector('.kanban-parked-reason');
    assert.ok(reasonEl, 'the parked card has no .kanban-parked-reason element');
    assert.match(reasonEl.textContent, /🔒\s*parked/,
        `a reason-less parked card must still show a '🔒 parked' cue — got: "${reasonEl.textContent}"`);
});

// --- 7. 🔴 GUARD (CMX-298) — backlog cards render before parked cards, within the ---
// --- shared Backlog lane -------------------------------------------------------------
//
// _KANBAN_BUCKET_ORDER puts 'backlog' before 'parked' so BACKLOG.md ideas lead and
// TODO.md's parked bullets follow within the lane — per the comment above that
// array in kanban.js. Swapping the two entries in that array is a silent,
// same-membership reorder: every card still lands in the Backlog lane, so nothing
// checking lane membership or per-card class notices. This asserts the actual DOM
// order of the two card types inside .kanban-col-backlog .kanban-cards.

test('renderKanban: within the Backlog lane, a backlog card renders before a parked card', () => {
    renderKanban(_payload([], {
        backlog_items: [{ text: 'a backlog idea', section: null, file: '/x/BACKLOG.md' }],
        parked_tasks: [{
            id: 'p1', title: 'a parked task', file: '/x/TODO.md',
            line_number: 3, raw: '- [ ] ...', reason: null,
        }],
    }));

    const cardsEl = document.querySelector('.kanban-col-backlog .kanban-cards');
    assert.ok(cardsEl, '.kanban-col-backlog has no .kanban-cards element');

    const kids = [...cardsEl.children];
    const backlogIdx = kids.findIndex((el) => el.classList.contains('kanban-card-backlog'));
    const parkedIdx = kids.findIndex((el) => el.classList.contains('kanban-card-parked'));
    assert.ok(backlogIdx !== -1, 'no .kanban-card-backlog card rendered in the Backlog lane');
    assert.ok(parkedIdx !== -1, 'no .kanban-card-parked card rendered in the Backlog lane');
    assert.ok(backlogIdx < parkedIdx,
        `expected the backlog card before the parked card, got backlog at ${backlogIdx} and parked at ${parkedIdx}`);
});
