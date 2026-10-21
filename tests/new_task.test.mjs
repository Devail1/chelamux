// ➕📐 CMX-6: the Work toolbar's "+ New task" and "+ Init a repo", and the ⌘K row, on the REAL
// page (the module graph, the markup sliced out of templates/index.html, a stubbed fetch).
// The dialogs themselves are React islands with their own suites (CMX-231:
// frontend/src/features/newtask and features/runs); this file pins the legacy wiring:
//   - the toolbar button appears only for a linear workflow;
//   - its onclick, the ⌘K row and "+ Init a repo" open the island dialogs, filled from the
//     payload work.js's ONE poll published (and, for Init, the launcher's dirs);
//   - refreshWork() (what a create, an init and a delete call) is work.js's poll, so the new
//     issue lands on the board as `CMX-N ↗`, linked to its Linear page.
//
// Run: pnpm exec vitest run tests/new_task.test.mjs (tests/test_js_suites.py runs it by discovery).
import { beforeAll, beforeEach, test } from 'vitest';
import assert from 'node:assert/strict';
import { bootDashboardDom, clickOnclick, flush, sliceTemplate } from './js_helpers/dashboard_dom.mjs';

const TOOLBAR_BTN = sliceTemplate('<button class="btn-accent" id="new-task-btn"', '+ New task</button>');
const INIT_BTN = sliceTemplate('<button class="btn-accent" onclick="chela.openInitRepo()"', '+ Init a repo</button>');
const PALETTE = sliceTemplate('<div class="palette-overlay" id="palette"', '</div>\n</div>');
const BOARD = sliceTemplate(
    '<div class="work-pane active" id="work-board" data-seg="board">', '<!-- /work-board -->');
const ISLANDS = sliceTemplate('<div class="work-pane" id="work-runs"', '</div>')
    + sliceTemplate('<div id="newtask-island">', '</div>');

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
let calls = [];
let work, launcher, refreshWork, workStore;

function fetchImpl(url, opts) {
    const u = String(url);
    calls.push({ url: u, opts: opts || null });
    let body = {};
    if (u.endsWith('/api/dispatcher')) body = dispatchPayload;
    else if (u.endsWith('/api/launcher')) body = { favorites: [{ path: '/fav/repo' }], recent: [{ path: '/recent/repo' }] };
    else if (u.includes('/api/agents')) body = [];
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
}

const $ = sel => document.querySelector(sel);
const dialog = name => [...document.querySelectorAll('[role="dialog"]')].find(d => d.querySelector('h3')?.textContent === name) || null;
const settle = async () => { for (let i = 0; i < 4; i++) await flush(); };
const escape = async () => {
    const d = document.querySelector('[role="dialog"]');
    if (d) d.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await settle();
};

beforeAll(async () => {
    ({ modules: { work } } = await bootDashboardDom({
        body: `<div id="panel-work"><div class="work-toolbar">${TOOLBAR_BTN}${INIT_BTN}</div>${BOARD}${ISLANDS}</div>\n${PALETTE}`,
        fetchImpl, canvasStub: true, extraModules: ['work.js', 'kanban.js'],
    }));
    // CMX-219: the launcher is the launch menu island's (frontend/src/features/launcher)
    launcher = await import('../chela/dashboard/frontend/src/features/launcher/index.ts');
    ({ refreshWork, work: workStore } = await import('../chela/dashboard/frontend/src/shared/store/work.ts'));
    await flush();
});

beforeEach(async () => {
    await escape();
    dispatchPayload = payload();
    await work.pollWork();
    calls = [];
});

test('the toolbar button shows for a linear workflow and hides otherwise', async () => {
    assert.equal($('#new-task-btn').style.display, '');
    dispatchPayload = payload('markdown');
    await work.pollWork();
    assert.equal($('#new-task-btn').style.display, 'none');
});

test('every poll is published to the store the islands read', async () => {
    assert.equal(workStore.get(), dispatchPayload);
    dispatchPayload = payload('markdown');
    await work.pollWork();
    assert.equal(workStore.get(), dispatchPayload);
});

test('the toolbar button opens the New-task dialog with the polled workflow and blockers', async () => {
    clickOnclick($('#new-task-btn'));
    await settle();
    const d = dialog('New task');
    assert.ok(d, 'the New-task dialog did not open');
    assert.equal(d.querySelector('[role="combobox"]').textContent, 'CMX');
    assert.deepEqual([...d.querySelectorAll('select[multiple] option')].map(o => o.value), ['CMX-1', 'CMX-2']);
});

test('the ⌘K palette has a "New task" row that opens the dialog', async () => {
    window.chela.openPalette();
    window.chela._renderPalette('New task');
    const row = [...document.querySelectorAll('#palette-list .palette-item')]
        .find(el => el.querySelector('.pi-title').textContent === 'New task');
    assert.ok(row, 'no "New task" row in the palette');
    clickOnclick(row);
    await settle();
    assert.ok(dialog('New task'), 'the palette row did not open the dialog');
});

test('"+ Init a repo" opens the Init dialog, suggesting the launcher\'s dirs', async () => {
    await launcher.refreshLauncher();
    clickOnclick($('[onclick="chela.openInitRepo()"]'));
    await settle();
    assert.ok(dialog('Init a repo for the dispatcher'), 'the Init dialog did not open');
    assert.deepEqual([...document.querySelectorAll('#init-suggestions option')].map(o => o.getAttribute('value')),
        ['/fav/repo', '/recent/repo']);
});

// What the server does on a create (tests/test_linear_new_task.py pins that half): the next
// /api/dispatcher read lists the new issue, its `tracker_url` the URL issueCreate returned.
function serverCreates(trackerUrl) {
    const base = payload();
    base.workflows[0].open_tasks.push({ ...base.workflows[0].open_tasks[0],
        id: 'CMX-100', title: 'Add a thing', raw: trackerUrl || 'CMX-100', tracker_url: trackerUrl });
    dispatchPayload = base;
}

test('refreshWork() is work.js\'s poll: a created issue shows on the board as CMX-N ↗', async () => {
    window.chela.selectView('work');
    serverCreates('https://linear.app/acme/issue/CMX-100');
    await refreshWork();
    await flush();
    assert.ok(calls.some(c => c.url.endsWith('/api/dispatcher')), 'refreshWork() did not poll /api/dispatcher');
    const card = document.querySelector('.kanban-card[data-task-id="CMX-100"]');
    assert.ok(card, 'the created issue is not in the queue after the refresh');
    assert.equal(card.querySelectorAll('a').length, 1, 'expected exactly one link on the card');
    const link = card.querySelector('a.kanban-tracker-link');
    assert.equal(link.getAttribute('href'), 'https://linear.app/acme/issue/CMX-100');
    assert.equal(link.getAttribute('target'), '_blank');
    assert.match(link.textContent, /CMX-100 ↗/);
});

test('a created issue with no https url is a plain id, never a link', async () => {
    window.chela.selectView('work');
    for (const url of [null, 'http://linear.app/acme/issue/CMX-100', 'javascript:alert(1)']) {
        serverCreates(url);
        await refreshWork();
        await flush();
        const card = document.querySelector('.kanban-card[data-task-id="CMX-100"]');
        assert.ok(card, `card missing for ${url}`);
        assert.equal(card.querySelector('a'), null, `linked ${url}`);
        assert.match(card.querySelector('span.kanban-card-id').textContent, /CMX-100/);
    }
});
