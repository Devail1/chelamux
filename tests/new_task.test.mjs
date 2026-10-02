// ➕📐 CMX-6 — the Work view's "New task" form, in a REAL DOM.
//
// Runs the real dashboard module graph (main.js → work.js → newtask.js, nav.js, kanban.js)
// against the REAL markup sliced out of templates/index.html, with a stubbed fetch, and
// drives it the way a click does (the element's own onclick attribute). What it pins:
//   - the toolbar button appears only for a linear workflow;
//   - a submit POSTs exactly the form's fields to the dashboard route (never to Linear);
//   - a Linear error is SHOWN and every typed field is KEPT;
//   - a success clears + closes the form and refreshes the queue;
//   - the ⌘K palette has a "New task" row that opens the form;
//   - an open Linear card's id is a `CMX-N ↗` link to the issue.
//
// Run: node --test tests/new_task.test.mjs (tests/test_js_suites.py runs it by discovery).
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { bootDashboardDom, clickOnclick, flush, sliceTemplate } from './js_helpers/dashboard_dom.mjs';

const TOOLBAR_BTN = sliceTemplate('<button class="btn-accent" id="new-task-btn"', '+ New task</button>');
const MODAL = sliceTemplate('<div class="modal-overlay" id="modal-newtask">', '<!-- /modal-newtask -->');
const PALETTE = sliceTemplate('<div class="palette-overlay" id="palette"', '</div>\n</div>');
const BOARD = sliceTemplate(
    '<div class="work-pane active" id="work-board" data-seg="board">', '<!-- /work-board -->');

const WF = '/repo/WORKFLOW.md';
function payload(kind = 'linear') {
    return {
        configured: true, dispatch_hold: null,
        workflows: [{
            path: WF, exists: true, project_key: 'CMX', tracker_kind: kind, error: null,
            open_tasks: [{ id: 'CMX-1', title: 'first open task', file: '', line_number: 1,
                           raw: 'https://linear.app/acme/issue/CMX-1', body: null,
                           url: 'https://linear.app/acme/issue/CMX-1', blocked: false,
                           unmet_depends: [], unresolved_depends: [] }],
            backlog_items: [], parked_tasks: [],
            active_runs: [{ task_id: 'CMX-2', title: 'running task', status: 'running' }],
            awaiting_review_runs: [], recent_runs: [],
        }],
    };
}

let dispatchPayload = payload();
let createResponse = { status: 200, body: { ok: true, issue: { identifier: 'CMX-100', url: 'https://linear.app/acme/issue/CMX-100', title: 'x' }, warnings: [] } };
let calls = [];
let work, kanban;

function fetchImpl(url, opts) {
    const u = String(url);
    calls.push({ url: u, opts: opts || null });
    let status = 200, body = {};
    if (u.endsWith('/api/dispatcher/linear/issue')) ({ status, body } = createResponse);
    else if (u.endsWith('/api/dispatcher')) body = dispatchPayload;
    else if (u.includes('/api/agents')) body = [];
    return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });
}

const $ = sel => document.querySelector(sel);

before(async () => {
    ({ modules: { work, kanban } } = await bootDashboardDom({
        body: `<div id="panel-work"><div class="work-toolbar">${TOOLBAR_BTN}</div>${BOARD}</div>${MODAL}\n${PALETTE}`,
        fetchImpl, canvasStub: true, extraModules: ['work.js', 'kanban.js'],
    }));
    await flush();
});

beforeEach(async () => {
    dispatchPayload = payload();
    await work.pollWork();
    calls = [];
    window.chela.closeModal('modal-newtask');
});

async function openForm() {
    clickOnclick($('#new-task-btn'));
    await flush();
}

function fill({ title = 'Add a thing', desc = '## Brief\n\nDo **it**.', prio = '2', blockers = ['CMX-1'] } = {}) {
    $('#newtask-title').value = title;
    $('#newtask-desc').value = desc;
    $('#newtask-priority').value = prio;
    for (const o of $('#newtask-blocked').options) o.selected = blockers.includes(o.value);
}

async function submit() {
    await clickOnclick($('#newtask-submit'));
    await flush();
}

test('the toolbar button shows for a linear workflow and hides otherwise', async () => {
    assert.equal($('#new-task-btn').style.display, '');
    dispatchPayload = payload('markdown');
    await work.pollWork();
    assert.equal($('#new-task-btn').style.display, 'none');
});

test('the form opens with the linear workflow and the open issues as blockers', async () => {
    await openForm();
    assert.ok($('#modal-newtask').classList.contains('active'));
    assert.deepEqual([...$('#newtask-workflow').options].map(o => o.value), [WF]);
    assert.deepEqual([...$('#newtask-blocked').options].map(o => o.value), ['CMX-1', 'CMX-2']);
});

test('a submit POSTs exactly the form fields to the dashboard route', async () => {
    await openForm();
    fill();
    await submit();
    const post = calls.find(c => c.url.endsWith('/api/dispatcher/linear/issue'));
    assert.ok(post, 'no POST to the dashboard route');
    assert.equal(post.opts.method, 'POST');
    assert.deepEqual(JSON.parse(post.opts.body), {
        workflow_path: WF, title: 'Add a thing', description: '## Brief\n\nDo **it**.',
        priority: 2, blocked_by: ['CMX-1'],
    });
    // Never straight to Linear from the browser.
    assert.ok(!calls.some(c => c.url.includes('linear.app')));
});

test('success clears and closes the form, and refreshes the queue', async () => {
    await openForm();
    fill();
    await submit();
    assert.ok(!$('#modal-newtask').classList.contains('active'), 'form still open');
    assert.equal($('#newtask-title').value, '');
    assert.equal($('#newtask-desc').value, '');
    const i = calls.findIndex(c => c.url.endsWith('/api/dispatcher/linear/issue'));
    assert.ok(calls.slice(i + 1).some(c => c.url.endsWith('/api/dispatcher')),
        'the queue was not refreshed after the create');
});

test('a Linear error is shown and the typed brief is kept', async () => {
    createResponse = { status: 502, body: { ok: false, error: 'graphql: Title is too long' } };
    try {
        await openForm();
        fill({ title: 'Keep me', desc: 'a long brief I typed' });
        await submit();
        assert.ok($('#modal-newtask').classList.contains('active'), 'form closed on an error');
        assert.match($('#newtask-error').textContent, /Title is too long/);
        assert.equal($('#newtask-title').value, 'Keep me');
        assert.equal($('#newtask-desc').value, 'a long brief I typed');
        assert.equal($('#newtask-priority').value, '2');
        assert.deepEqual([...$('#newtask-blocked').selectedOptions].map(o => o.value), ['CMX-1']);
    } finally {
        createResponse = { status: 200, body: { ok: true, issue: { identifier: 'CMX-100', url: '', title: 'x' }, warnings: [] } };
    }
});

test('the ⌘K palette has a "New task" row that opens the form', async () => {
    window.chela.openPalette();
    window.chela._renderPalette('New task');
    const row = [...document.querySelectorAll('#palette-list .palette-item')]
        .find(el => el.querySelector('.pi-title').textContent === 'New task');
    assert.ok(row, 'no "New task" row in the palette');
    clickOnclick(row);
    await flush();
    assert.ok($('#modal-newtask').classList.contains('active'));
});

test('an open Linear card links its id to the issue — CMX-N ↗', () => {
    kanban.renderKanban(payload());
    const link = document.querySelector('.kanban-card a.kanban-card-id');
    assert.ok(link, 'the open card id is not a link');
    assert.equal(link.getAttribute('href'), 'https://linear.app/acme/issue/CMX-1');
    assert.equal(link.getAttribute('target'), '_blank');
    assert.match(link.textContent, /CMX-1 ↗/);
});
