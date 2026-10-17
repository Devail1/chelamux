// CMX-94 — the Runs segment (dispatcher.js) on a Linear workflow vs a TODO.md one.
// Run: node --test tests/runs_linear.test.mjs (tests/test_js_suites.py runs it by discovery).
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import { bootDashboardDom } from './js_helpers/dashboard_dom.mjs';

const BODY = '<div id="dispatcher-empty" style="display:none;"></div><div id="dispatcher-list"></div>';

const URL = 'https://linear.app/acme/issue/CMX-12/tighten-top-row';
let renderDispatcher;
before(async () => {
    ({ modules: { dispatcher: { renderDispatcher } } } =
        await bootDashboardDom({ body: BODY, extraModules: ['dispatcher.js'] }));
});

function task(id, group, extra = {}) {
    return { id, title: `title ${id}`, file: '', line_number: 0, group,
        tracker_state: { ready: 'Todo', in_progress: 'In Progress', backlog: 'Backlog' }[group],
        tracker_url: `https://linear.app/acme/issue/${id}`, risk: 'normal', ...extra };
}

const run = (task_id, status, extra = {}) =>
    ({ task_id, title: task_id, status, project_key: 'CMX', task_number: +task_id.slice(4), ...extra });

function render(wf) {
    renderDispatcher({ configured: true, workflows: [{
        path: '/repo/WORKFLOW.md', open_tasks: [], active_runs: [],
        awaiting_review_runs: [], recent_runs: [], ...wf }] });
    return globalThis.document.getElementById('dispatcher-list');
}

const ids = el => [...el.querySelectorAll('tbody tr')].map(tr => tr.querySelector('td').textContent.trim());

test('linear open tasks: identifier linked, state + risk shown, no file:line, no ×', () => {
    const list = render({ tracker_kind: 'linear', open_tasks: [
        task('CMX-12', 'ready', { tracker_url: URL, risk: 'high', file: '/x/TODO.md', line_number: 7 })] });
    const link = list.querySelector('a.run-tracker-link');
    assert.equal(link.getAttribute('href'), URL);
    assert.equal(link.textContent.trim(), 'CMX-12');
    const row = link.closest('tr');
    assert.equal(row.querySelector('td[data-label="State"]').textContent.trim(), 'Todo');
    assert.equal(row.querySelector('td[data-label="Risk"]').textContent.trim(), 'risk: high');
    assert.equal(list.querySelector('[data-del-kind="source-line"]'), null);
    assert.equal(list.querySelectorAll('table').length, 1);   // empty groups render no table
    assert.equal(list.textContent.includes('TODO.md:7'), false);
});

test('linear open tasks split Ready → In progress → Backlog (collapsed), counts in headers', () => {
    const list = render({ tracker_kind: 'linear', open_tasks: [
        task('CMX-1', 'backlog'), task('CMX-2', 'ready'), task('CMX-3', 'in_progress'),
        task('CMX-4', 'ready'), task('CMX-5', 'backlog'), task('CMX-6', 'backlog')] });
    const heads = [...list.querySelectorAll('.open-section')].map(h => h.textContent.trim());
    assert.deepEqual(heads, ['Ready (Todo) · 2', 'In progress / In review · 1', 'Backlog · 3']);
    const tables = [...list.querySelectorAll('.open-section')].map(h =>
        h.tagName === 'SUMMARY' ? h.parentElement : h.nextElementSibling);
    assert.deepEqual(tables.map(ids), [['CMX-2', 'CMX-4'], ['CMX-3'], ['CMX-1', 'CMX-5', 'CMX-6']]);
    const details = list.querySelector('details.open-backlog');
    assert.equal(details.open, false);
    // The headline count is READY, not every non-terminal issue.
    assert.match(list.querySelector('.card > div').textContent, /^\s*2 ready ·/);
});

test('todo workflow keeps the Source column and the source-line ×', () => {
    const list = render({ tracker_kind: 'markdown', open_tasks: [
        { id: 'abc', title: 'ship it', file: '/x/TODO.md', line_number: 7, group: 'ready' }] });
    const btn = list.querySelector('[data-del-kind="source-line"]');
    assert.ok(btn);
    assert.equal(btn.dataset.file, '/x/TODO.md');
    assert.equal(list.querySelector('td[data-label="Source"]').textContent, '/x/TODO.md:7');
    assert.equal(list.querySelector('.open-section'), null);
    assert.match(list.querySelector('.card > div').textContent, /^\s*1 open task ·/);
});

test('a run CMX-N links to its Linear issue only on a linear workflow', () => {
    const r = run('CMX-12', 'running', { tracker_url: URL });
    let list = render({ tracker_kind: 'linear', active_runs: [r] });
    let a = list.querySelector('[data-runs="active"] a.run-tracker-link');
    assert.equal(a.getAttribute('href'), URL);
    assert.equal(a.textContent, 'CMX-12');
    list = render({ tracker_kind: 'markdown', active_runs: [r] });
    assert.equal(list.querySelector('a.run-tracker-link'), null);
    assert.equal(list.querySelector('[data-runs="active"] .ts').textContent, 'CMX-12');
});

test('needs_human runs sit in their own table above Active, and nowhere else', () => {
    const list = render({ tracker_kind: 'linear',
        active_runs: [run('CMX-1', 'running')],
        awaiting_review_runs: [run('CMX-2', 'needs_human'), run('CMX-3', 'awaiting_review')],
        recent_runs: [run('CMX-4', 'needs_human'), run('CMX-5', 'done')] });
    const ids = sel => [...list.querySelectorAll(`[data-runs="${sel}"] tbody tr`)].map(tr => tr.dataset.taskId);
    assert.deepEqual(ids('needs_human'), ['CMX-2', 'CMX-4']);
    assert.deepEqual(ids('active'), ['CMX-1']);
    assert.deepEqual(ids('awaiting'), ['CMX-3']);
    assert.deepEqual(ids('recent'), ['CMX-5']);
    const order = [...list.querySelectorAll('[data-runs]')].map(d => d.dataset.runs);
    assert.deepEqual(order, ['needs_human', 'active', 'awaiting', 'recent']);
    assert.match(list.querySelector('.card > div').textContent, /2 needing a human/);
});
