// ➕📐 CMX-6: the Work toolbar's "+ New task" and "+ Init a repo", the New menu's "Init a repo"
// and "Scheduled task", and the ⌘K row, on the REAL page (the module graph, the markup sliced
// out of templates/index.html, a stubbed fetch). The dialogs and the toolbar are React islands
// with their own suites (frontend/src/features/newtask, features/runs, features/work, CMX-231
// and CMX-233); this file pins how the page reaches them:
//   - the toolbar button appears only for a linear workflow;
//   - it, the ⌘K row, the toolbar's and the New menu's "Init a repo" open the island dialogs,
//     filled from the payload the ONE /api/dispatcher poll published (and, for Init, the
//     launcher's dirs);
//   - refreshWork() (what a create, an init and a delete call) is that poll, so the new
//     issue lands on the board as `CMX-N`, linked to its Linear page.
//
// Run: pnpm exec vitest run tests/new_task.test.mjs (tests/test_js_suites.py runs it by discovery).
import { beforeAll, beforeEach, test } from 'vitest';
import assert from 'node:assert/strict';
import { bootDashboardDom, clickIsland, clickOnclick, flush, sliceTemplate } from './js_helpers/dashboard_dom.mjs';

const PANEL = sliceTemplate('<div class="panel active" id="panel-work">', '</div>', { terminals_enabled: false });
const PALETTE = sliceTemplate('<div class="palette-overlay" id="palette"', '</div>\n</div>');
const MENU = sliceTemplate('<div class="popover-item" onclick="chela.hideNewMenu(); chela.openInitRepo()">', 'Scheduled task</div>');
const ISLANDS = sliceTemplate('<div id="newtask-island">', '</div>');

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
let pollWork, launcher, refreshWork, workStore;

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
const button = text => [...document.querySelectorAll('#panel-work button')].find(b => b.textContent === text) || null;
const dialog = name => [...document.querySelectorAll('[role="dialog"]')].find(d => d.querySelector('h3')?.textContent === name) || null;
const settle = async () => { for (let i = 0; i < 4; i++) await flush(); };
const escape = async () => {
    const d = document.querySelector('[role="dialog"]');
    if (d) d.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await settle();
};

beforeAll(async () => {
    ({ modules: { launcher } } = await bootDashboardDom({
        body: `${PANEL}\n<div class="popover" id="new-menu">${MENU}</div>\n${ISLANDS}\n${PALETTE}`,
        fetchImpl, canvasStub: true, extraModules: ['launcher.js'],
    }));
    ({ refreshWork, work: workStore } = await import('../chela/dashboard/frontend/src/shared/store/work.ts'));
    ({ pollWork } = await import('../chela/dashboard/frontend/src/features/work/index.ts'));
    await flush();
});

beforeEach(async () => {
    await escape();
    dispatchPayload = payload();
    await pollWork();
    await settle();
    calls = [];
});

test('the toolbar button shows for a linear workflow and is gone otherwise', async () => {
    assert.ok($('#panel-work #new-task-btn'), 'no "+ New task" for a linear workflow');
    dispatchPayload = payload('markdown');
    await pollWork();
    await settle();
    assert.equal($('#new-task-btn'), null);
});

test('every poll is published to the store the islands read', async () => {
    assert.equal(workStore.get(), dispatchPayload);
    dispatchPayload = payload('markdown');
    await pollWork();
    assert.equal(workStore.get(), dispatchPayload);
});

test('the toolbar button opens the New-task dialog with the polled workflow and blockers', async () => {
    clickIsland($('#new-task-btn'));
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

test('the toolbar\'s "+ Init a repo" and the New menu\'s "Init a repo" open the Init dialog, suggesting the launcher\'s dirs', async () => {
    await launcher.refreshLauncher();
    for (const open of [() => clickIsland(button('+ Init a repo')), () => clickOnclick($('#new-menu [onclick*="openInitRepo"]'))]) {
        open();
        await settle();
        assert.ok(dialog('Init a repo for the dispatcher'), 'the Init dialog did not open');
        assert.deepEqual([...document.querySelectorAll('#init-suggestions option')].map(o => o.getAttribute('value')),
            ['/fav/repo', '/recent/repo']);
        await escape();
    }
});

test('the New menu\'s "Scheduled task" opens the add-schedule dialog', async () => {
    clickOnclick($('#new-menu [onclick*="showAddSchedule"]'));
    await settle();
    assert.ok(dialog('Add Scheduled Task'), 'the add-schedule dialog did not open');
});

// What the server does on a create (tests/test_linear_new_task.py pins that half): the next
// /api/dispatcher read lists the new issue, its `tracker_url` the URL issueCreate returned.
function serverCreates(trackerUrl) {
    const base = payload();
    base.workflows[0].open_tasks.push({ ...base.workflows[0].open_tasks[0],
        id: 'CMX-100', title: 'Add a thing', raw: trackerUrl || 'CMX-100', tracker_url: trackerUrl });
    dispatchPayload = base;
}

test('refreshWork() is the Work poll: a created issue shows on the board as CMX-N, linked', async () => {
    window.chela.selectView('work');
    serverCreates('https://linear.app/acme/issue/CMX-100');
    await refreshWork();
    await settle();
    assert.ok(calls.some(c => c.url.endsWith('/api/dispatcher')), 'refreshWork() did not poll /api/dispatcher');
    const card = document.querySelector('.kanban-card[data-task-id="CMX-100"]');
    assert.ok(card, 'the created issue is not in the queue after the refresh');
    assert.equal(card.querySelectorAll('a').length, 1, 'expected exactly one link on the card');
    const link = card.querySelector('a.kanban-tracker-link');
    assert.equal(link.getAttribute('href'), 'https://linear.app/acme/issue/CMX-100');
    assert.equal(link.getAttribute('target'), '_blank');
    assert.equal(link.textContent.trim(), 'CMX-100');
});

test('a created issue with no https url is a plain id, never a link', async () => {
    window.chela.selectView('work');
    for (const url of [null, 'http://linear.app/acme/issue/CMX-100', 'javascript:alert(1)']) {
        serverCreates(url);
        await refreshWork();
        await settle();
        const card = document.querySelector('.kanban-card[data-task-id="CMX-100"]');
        assert.ok(card, `card missing for ${url}`);
        assert.equal(card.querySelector('a'), null, `linked ${url}`);
        assert.match(card.querySelector('span.kanban-card-id').textContent, /CMX-100/);
    }
});
