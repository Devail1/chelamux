// 🔗↗️ CMX-5 — every Work card for a Linear task (queued, running, in review, done) shows
// its identifier as a link, `CMX-12 ↗`, to the issue URL the Linear adapter fetched
// (GraphQL `url`, carried as `tracker_url`). Cards from markdown workflows are unchanged.
//
// Runs the REAL kanban.js (renderKanban) against a REAL DOM (jsdom), same approach as
// tests/kanban_flatten.test.mjs, and reads the rendered anchors back — not a source grep.
// The server half (adapter → run row → /api/dispatcher) is tests/test_tracker_url.py.
//
// Run: node --test tests/kanban_tracker_link.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `pnpm install` for jsdom.)
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';   // needs `pnpm install` — tests/test_js_suites.py enforces it
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
    void JSDOM;
    ({ modules: { kanban: { renderKanban } } } = await bootDashboardDom({
        body: BODY, extraModules: ['kanban.js'],
    }));
});

// A distinct URL per card — so an href taken from the WRONG card (or a constant) fails.
const url = n => `https://linear.app/acme/issue/CMX-${n}/slug-${n}`;

function _run(taskId, status, extra = {}) {
    return {
        task_id: taskId, title: `run ${taskId}`, status, attempt: 1, branch_name: taskId.toLowerCase(),
        started_at: '2026-10-01T00:00:00Z', ended_at: '2026-10-01T01:00:00Z',
        pr_url: null, pr_checks: null, ...extra,
    };
}

function _render() {
    renderKanban({
        configured: true,
        workflows: [
            {
                path: '/lin/WORKFLOW.md', project_key: 'CMX', backlog_items: [], parked_tasks: [],
                open_tasks: [{ id: 'CMX-11', title: 'queued', raw: url(11), body: null, tracker_url: url(11) }],
                active_runs: [_run('CMX-12', 'running', { tracker_url: url(12) })],
                awaiting_review_runs: [_run('CMX-13', 'awaiting_review', { tracker_url: url(13) })],
                recent_runs: [_run('CMX-14', 'done', { tracker_url: url(14), pr_state: 'merged' })],
            },
            {
                path: '/md/WORKFLOW.md', project_key: 'MD', backlog_items: [], parked_tasks: [],
                open_tasks: [{ id: 'md-open', title: 'md queued', file: 'TODO.md', line_number: 3, raw: '- [ ] md queued', body: null }],
                active_runs: [_run('md-run', 'running', { tracker_url: null })],
                awaiting_review_runs: [],
                recent_runs: [_run('md-done', 'done', { pr_state: 'merged' })],
            },
        ],
    });
}

const card = id => document.querySelector(`.kanban-card[data-task-id="${id}"]`);

test('every Linear card — queued, running, in review, done — links its identifier to EXACTLY the adapter\'s url', () => {
    _render();
    for (const n of [11, 12, 13, 14]) {
        const id = `CMX-${n}`;
        const c = card(id);
        assert.ok(c, `${id}: card missing from the board`);
        const links = c.querySelectorAll('a.kanban-tracker-link');
        assert.equal(links.length, 1, `${id}: expected exactly one Linear link on the card`);
        const a = links[0];
        assert.equal(a.getAttribute('href'), url(n), `${id}: href is not the adapter's url`);
        assert.equal(a.textContent.trim(), `${id} ↗`, `${id}: link text is not the identifier`);
    }
});

test('a markdown card renders no Linear link (and keeps its plain id chip)', () => {
    _render();
    for (const id of ['md-open', 'md-run', 'md-done']) {
        const c = card(id);
        assert.ok(c, `${id}: card missing from the board`);
        assert.equal(c.querySelectorAll('a').length, 0, `${id}: a markdown card grew a link`);
        assert.ok(c.querySelector('span.kanban-card-id'), `${id}: lost its plain id chip`);
    }
});

test('a non-https tracker_url renders no link at all, never an unsafe href', () => {
    renderKanban({
        configured: true,
        workflows: [{
            path: '/lin/WORKFLOW.md', project_key: 'CMX', backlog_items: [], parked_tasks: [],
            open_tasks: [], awaiting_review_runs: [], recent_runs: [],
            active_runs: [_run('CMX-20', 'running', { tracker_url: 'javascript:alert(1)' })],
        }],
    });
    assert.equal(card('CMX-20').querySelectorAll('a').length, 0);
});

test('the link opens in a new tab and its click never reaches the card\'s own click action', () => {
    _render();
    const a = card('CMX-12').querySelector('a.kanban-tracker-link');
    assert.equal(a.getAttribute('target'), '_blank', 'link does not open a new tab');
    assert.match(a.getAttribute('rel') || '', /\bnoopener\b/, 'new-tab link without rel=noopener');
    // The card's own onclick opens the task modal; the link's inline handler is what keeps a
    // click on it from bubbling there. Run the REAL attribute text against an event spy.
    let stopped = 0;
    const evt = { stopPropagation() { stopped += 1; }, target: a, currentTarget: a };
    new Function('chela', 'event', a.getAttribute('onclick') || '').call(a, window.chela, evt);
    assert.equal(stopped, 1, 'clicking the link would ALSO open the card\'s task modal');
});
