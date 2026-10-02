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
                           tracker_url: 'https://linear.app/acme/issue/CMX-1', blocked: false,
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
let createRejects = null;      // an Error ⇒ the create fetch REJECTS (network down)
let alerts = [];
let work, kanban;

function fetchImpl(url, opts) {
    const u = String(url);
    calls.push({ url: u, opts: opts || null });
    let status = 200, body = {};
    if (u.endsWith('/api/dispatcher/linear/issue') && createRejects) return Promise.reject(createRejects);
    if (u.endsWith('/api/dispatcher/linear/issue')) ({ status, body } = createResponse);
    else if (u.endsWith('/api/dispatcher')) body = dispatchPayload;
    else if (u.includes('/api/agents')) body = [];
    // body undefined ⇒ a non-JSON response (an HTML error page): json() rejects, as in a browser.
    const json = () => (body === undefined ? Promise.reject(new SyntaxError('Unexpected token <'))
                                           : Promise.resolve(body));
    return Promise.resolve({ ok: status < 400, status, json });
}

const $ = sel => document.querySelector(sel);

before(async () => {
    ({ modules: { work, kanban } } = await bootDashboardDom({
        body: `<div id="panel-work"><div class="work-toolbar">${TOOLBAR_BTN}</div>${BOARD}</div>${MODAL}\n${PALETTE}`,
        fetchImpl, canvasStub: true, extraModules: ['work.js', 'kanban.js'],
    }));
    await flush();
    window.alert = msg => { alerts.push(String(msg)); };
    globalThis.alert = window.alert;
});

beforeEach(async () => {
    createRejects = null;
    alerts = [];
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
    clickOnclick($('#newtask-submit'));
    await flush();
    await flush();
}

// Every field exactly as fill() typed it, the form still open, no queue refresh after the
// attempt, and the submit button usable again — what "a failed submit keeps the brief" means.
function assertKept({ title, desc, prio = '2', blockers = ['CMX-1'] }) {
    assert.ok($('#modal-newtask').classList.contains('active'), 'form closed on a failure');
    assert.equal($('#newtask-title').value, title);
    assert.equal($('#newtask-desc').value, desc);
    assert.equal($('#newtask-priority').value, prio);
    assert.deepEqual([...$('#newtask-blocked').selectedOptions].map(o => o.value), blockers);
    assert.equal($('#newtask-submit').disabled, false, 'submit left disabled');
    const i = calls.findIndex(c => c.url.endsWith('/api/dispatcher/linear/issue'));
    assert.ok(i >= 0, 'the create was never attempted');
    assert.ok(!calls.slice(i + 1).some(c => c.url.endsWith('/api/dispatcher')),
        'the queue was refreshed as if the create had succeeded');
    assert.deepEqual(alerts, []);
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
    assert.equal($('#newtask-priority').value, '0');
    assert.deepEqual([...$('#newtask-blocked').selectedOptions].map(o => o.value), []);
    assert.equal($('#newtask-submit').disabled, false);
    assert.deepEqual(alerts, []);
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
        assert.match($('#newtask-error').textContent, /Title is too long/);
        assertKept({ title: 'Keep me', desc: 'a long brief I typed' });
    } finally {
        createResponse = { status: 200, body: { ok: true, issue: { identifier: 'CMX-100', url: '', title: 'x' }, warnings: [] } };
    }
});

test('a failed submit (network error) KEEPS every typed field and the form open', async () => {
    createRejects = new TypeError('Failed to fetch');
    await openForm();
    fill({ title: 'Keep me too', desc: 'typed offline' });
    await submit();
    assert.match($('#newtask-error').textContent, /Request failed — nothing was created\. TypeError: Failed to fetch/);
    assertKept({ title: 'Keep me too', desc: 'typed offline' });
});

test('an HTTP error with no JSON body still keeps the form and names the status', async () => {
    createResponse = { status: 500, body: undefined };
    try {
        await openForm();
        fill({ title: 'Still here', desc: 'brief' });
        await submit();
        assert.match($('#newtask-error').textContent, /HTTP 500/);
        assertKept({ title: 'Still here', desc: 'brief' });
    } finally {
        createResponse = { status: 200, body: { ok: true, issue: { identifier: 'CMX-100', url: '', title: 'x' }, warnings: [] } };
    }
});

test('a 200 that says ok:false is a failure, not a success', async () => {
    createResponse = { status: 200, body: { ok: false, error: 'nope' } };
    try {
        await openForm();
        fill({ title: 'T', desc: 'D' });
        await submit();
        assert.match($('#newtask-error').textContent, /nope/);
        assertKept({ title: 'T', desc: 'D' });
    } finally {
        createResponse = { status: 200, body: { ok: true, issue: { identifier: 'CMX-100', url: '', title: 'x' }, warnings: [] } };
    }
});

test('an empty title never POSTs and keeps the form', async () => {
    await openForm();
    fill({ title: '   ', desc: 'a brief with no title' });
    await submit();
    assert.ok(!calls.some(c => c.url.endsWith('/api/dispatcher/linear/issue')));
    assert.match($('#newtask-error').textContent, /title is required/);
    assert.ok($('#modal-newtask').classList.contains('active'));
    assert.equal($('#newtask-desc').value, 'a brief with no title');
});

test('a relation warning on a success is surfaced with the new issue id', async () => {
    const prev = createResponse;
    createResponse = { status: 200, body: { ok: true, issue: { identifier: 'CMX-101', url: '', title: 'x' },
                                            warnings: ['could not mark CMX-101 blocked by CMX-1'] } };
    try {
        await openForm();
        fill();
        await submit();
        assert.deepEqual(alerts, ['CMX-101 created, but: could not mark CMX-101 blocked by CMX-1']);
        assert.ok(!$('#modal-newtask').classList.contains('active'));
    } finally {
        createResponse = prev;
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

// What the server does on a create (tests/test_linear_new_task.py pins that half): the
// next /api/dispatcher read lists the new issue, its `tracker_url` the URL issueCreate
// returned. The card is drawn by CMX-5's _kTrackerLink — there is one link, not two.
function serverCreates(trackerUrl) {
    createResponse = { status: 200, body: { ok: true, warnings: [],
        issue: { identifier: 'CMX-100', url: trackerUrl, title: 'Add a thing' } } };
    const base = payload();
    base.workflows[0].open_tasks.push({ ...base.workflows[0].open_tasks[0],
        id: 'CMX-100', title: 'Add a thing', raw: trackerUrl || 'CMX-100', tracker_url: trackerUrl });
    dispatchPayload = base;
}

test('a created issue shows in the queue as CMX-N ↗, linked to its Linear page', async () => {
    const prev = createResponse;
    try {
        serverCreates('https://linear.app/acme/issue/CMX-100');
        window.chela.selectView('work');     // the board is drawn by the post-submit refresh
        await openForm();
        fill();
        await submit();
        await flush();
        const card = document.querySelector('.kanban-card[data-task-id="CMX-100"]');
        assert.ok(card, 'the created issue is not in the queue after the submit');
        const i = calls.findIndex(c => c.url.endsWith('/api/dispatcher/linear/issue'));
        assert.ok(i >= 0 && calls.slice(i + 1).some(c => c.url.endsWith('/api/dispatcher')),
                  'the board was not refreshed after the create');
        const links = card.querySelectorAll('a');
        assert.equal(links.length, 1, 'expected exactly one link on the card');
        const link = card.querySelector('a.kanban-tracker-link');
        assert.ok(link, 'the created card has no tracker link');
        assert.equal(link.getAttribute('href'), 'https://linear.app/acme/issue/CMX-100');
        assert.equal(link.getAttribute('target'), '_blank');
        assert.match(link.textContent, /CMX-100 ↗/);
    } finally {
        createResponse = prev;
    }
});

test('a created issue with no https url is a plain id, never a link', async () => {
    const prev = createResponse;
    try {
        window.chela.selectView('work');
        for (const url of [null, 'http://linear.app/acme/issue/CMX-100', 'javascript:alert(1)']) {
            serverCreates(url);
            await openForm();
            fill();
            await submit();
            await flush();
            const card = document.querySelector('.kanban-card[data-task-id="CMX-100"]');
            assert.ok(card, `card missing for ${url}`);
            assert.equal(card.querySelector('a'), null, `linked ${url}`);
            const chip = card.querySelector('span.kanban-card-id');
            assert.ok(chip, `no plain id chip for ${url}`);
            assert.match(chip.textContent, /CMX-100/);
        }
    } finally {
        createResponse = prev;
    }
});
