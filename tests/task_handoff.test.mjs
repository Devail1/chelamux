// 📝 CMX-52 — the agent's own `chela task-finished --handoff` note on the Dispatch board.
//
// `runHandoffNote` (runstate.js) is the pure half: NULL and "" both render nothing, and an
// agent-authored multi-line note is flattened to one line. The kanban card (kanban.js) is
// the wiring half: the note must reach the card, HTML-escaped — it is agent-authored text
// spliced into innerHTML, so a `<img onerror>` in it must arrive as text, never as a node.
//
// Run: pnpm exec vitest run tests/task_handoff.test.mjs (tests/test_js_suites.py runs every
// .test.mjs inside pytest, by discovery).
import { beforeAll, test } from 'vitest';
import assert from 'node:assert/strict';
import { bootDashboardDom } from './js_helpers/dashboard_dom.mjs';
import { runHandoffNote } from '../chela/dashboard/static/js/runstate.js';

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
    ({ modules: { kanban: { renderKanban } } } = await bootDashboardDom({
        body: KANBAN_BODY,
        extraModules: ['kanban.js'],
    }));
});

function _payload(run) {
    return {
        configured: true,
        workflows: [{
            path: '/x/WORKFLOW.md', project_key: 'CMX', open_tasks: [], backlog_items: [],
            parked_tasks: [], active_runs: [], recent_runs: [],
            awaiting_review_runs: [{
                task_id: 't-h', title: 'a task', status: 'awaiting_review', pr_state: 'open',
                started_at: '2026-10-11T00:00:00Z', ended_at: '2026-10-11T01:00:00Z',
                attempt: 1, pr_url: null, pr_checks: null, branch_name: 'cmx-1',
                window_name: 'agent-1', ...run,
            }],
        }],
    };
}

// --- runHandoffNote (pure) -----------------------------------------------------------

test('runHandoffNote returns the note, flattened to one line', () => {
    assert.equal(runHandoffNote({ handoff: 'changed X\n\n  look at Y\tfirst' }),
        'changed X look at Y first');
});

test('runHandoffNote shows nothing for NULL, "" and blank — and nothing for a run with no handoff key', () => {
    assert.equal(runHandoffNote({ handoff: null }), null);
    assert.equal(runHandoffNote({ handoff: '' }), null);
    assert.equal(runHandoffNote({ handoff: ' \n\t ' }), null);
    assert.equal(runHandoffNote({}), null);
    assert.equal(runHandoffNote(null), null);
});

test('runHandoffNote drops control characters (an ESC sequence cannot ride along)', () => {
    assert.equal(runHandoffNote({ handoff: 'ok\u001b[31mred\u0007' }), 'ok [31mred');
});

// --- kanban.js: the card's own call site --------------------------------------------

test('an awaiting-review card shows the agent\'s handoff note', () => {
    renderKanban(_payload({ handoff: 'migrated runs.handoff; check the inbox line' }));

    const card = document.querySelector('.kanban-card[data-task-id="t-h"]');
    assert.ok(card, 'setup: no kanban card rendered');
    const note = card.querySelector('.kanban-card-handoff');
    assert.ok(note, 'the card rendered no .kanban-card-handoff — the handoff is not wired in');
    assert.match(note.textContent, /migrated runs\.handoff; check the inbox line/);
});

test('the handoff note is HTML-escaped — markup in it renders as text, never as a node', () => {
    renderKanban(_payload({ handoff: '<img src=x onerror="alert(1)"><b>bold</b>' }));

    const note = document.querySelector('.kanban-card[data-task-id="t-h"] .kanban-card-handoff');
    assert.ok(note, 'setup: no handoff note rendered');
    assert.equal(note.querySelector('img'), null, 'an <img> in the handoff became a real node');
    assert.equal(note.querySelector('b'), null, 'a <b> in the handoff became a real node');
    assert.match(note.textContent, /<img src=x onerror="alert\(1\)"><b>bold<\/b>/);
});

test('a card with no handoff (NULL) or an empty one renders no handoff line — the counterweight', () => {
    for (const handoff of [null, '']) {
        renderKanban(_payload({ handoff }));
        const card = document.querySelector('.kanban-card[data-task-id="t-h"]');
        assert.ok(card, 'setup: no kanban card rendered');
        assert.equal(card.querySelector('.kanban-card-handoff'), null,
            `handoff=${JSON.stringify(handoff)} must render no handoff line`);
    }
});
