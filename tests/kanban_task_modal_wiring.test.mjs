// CMX-290, on the ported board (CMX-233): a real CLICK on a real Work board card opens the task
// modal, on the REAL page. features/work's RTL suite proves the Board and the TaskModal
// together; this one proves the page around them: the template's #panel-work and
// #task-modal-island slots, both islands mounted by the real entry, the one /api/dispatcher
// poll feeding the board, and a click crossing from one island's root to the other's.
//
// Both fixtures are `sliceTemplate()`d out of the REAL templates/index.html, so renaming a slot
// shows up here, not just in a hand-typed fixture that happens to agree with today's markup.
//
// Run: pnpm exec vitest run tests/kanban_task_modal_wiring.test.mjs (tests/test_js_suites.py
// runs every .test.mjs inside pytest, by discovery).
import { beforeAll, test } from 'vitest';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';   // needs `pnpm install` — tests/test_js_suites.py enforces it
import { bootDashboardDom, clickIsland, flush, sliceTemplate } from './js_helpers/dashboard_dom.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const STYLE_CSS = fs.readFileSync(path.join(HERE, '..', 'chela', 'dashboard', 'static', 'style.css'), 'utf8');

const PANEL = sliceTemplate('<div class="panel active" id="panel-work">', '</div>', { terminals_enabled: false });
const MODAL = sliceTemplate('<div id="task-modal-island">', '</div>');

let payload = {};
let pollWork, closeTaskModal;

beforeAll(async () => {
    const fetchImpl = url => Promise.resolve({
        ok: true, status: 200, json: () => Promise.resolve(String(url).endsWith('/api/dispatcher') ? payload : {}),
    });
    await bootDashboardDom({ body: `${PANEL}\n${MODAL}`, fetchImpl, terminalsEnabled: false });
    ({ pollWork } = await import('../chela/dashboard/frontend/src/features/work/index.ts'));
    ({ closeTaskModal } = await import('../chela/dashboard/frontend/src/features/taskmodal/index.ts'));
});

async function show(wf) {
    payload = { configured: true, workflows: [{ path: '/x/WORKFLOW.md', project_key: 'CMX', ...wf }] };
    await pollWork();
    for (let i = 0; i < 4; i++) await flush();
}
const modal = () => document.querySelector('[role="dialog"].modal-task');
const titleHtml = () => modal()?.querySelector('#task-modal-content .task-modal-title > span')?.innerHTML;
async function click(el) {
    assert.ok(el, 'the card to click is missing');
    clickIsland(el);
    for (let i = 0; i < 3; i++) await flush();
}
async function close() {
    closeTaskModal();
    await flush();
}

const run = (task_id, title, status) => ({ task_id, title, status, pr_state: null, started_at: '2026-08-01T00:00:00Z', ended_at: '2026-08-01T01:00:00Z', attempt: 1 });

test('the board is drawn inside the page\'s #panel-work, from the one poll', async () => {
    await show({ recent_runs: [run('t-a', 'a task', 'done')] });
    assert.ok(document.querySelector('#panel-work #work-seg'), 'the Work toolbar is not on the page');
    assert.ok(document.querySelector('#panel-work #work-board .kanban-card[data-task-id="t-a"]'), 'the polled card is not on the board');
});

test('clicking either of TWO cards in one render opens the modal with THAT card, run cards and backlog cards alike', async () => {
    await show({
        open_tasks: [{ id: 't-decoy', title: 'decoy, must never appear' }],
        backlog_items: [{ text: 'first **backlog** idea' }, { text: 'second idea' }],
        parked_tasks: [{ id: 'p1', title: 'parked one', raw: '- [ ] parked one <!-- blocked: a -->' }],
        recent_runs: [run('t-card-a', 'ship **the wall** now', 'done'), run('t-card-b', 'raise the **second** gate', 'closed')],
    });
    assert.equal(modal(), null, 'the task modal starts open');
    const backlog = [...document.querySelectorAll('.kanban-card-backlog')];
    for (const [el, want] of [
        [document.querySelector('.kanban-card[data-task-id="t-card-b"]'), 'raise the <strong>second</strong> gate'],
        [document.querySelector('.kanban-card[data-task-id="t-card-a"]'), 'ship <strong>the wall</strong> now'],
        [backlog[1], 'second idea'],
        [backlog[0], 'first <strong>backlog</strong> idea'],
    ]) {
        await click(el);
        assert.ok(modal(), 'the task modal never opened');
        assert.equal(titleHtml(), want);
    }
    await click(document.querySelector('.kanban-card-parked'));
    assert.match(modal()?.querySelector('.task-modal-brief')?.textContent ?? '', /parked one/,
        'the parked card\'s raw bullet did not reach the modal');
    await close();
});

test('the open modal is VISIBLE under the REAL style.css, and its close button hides it', async () => {
    await show({ recent_runs: [run('t-css', 'a task', 'done')] });
    await click(document.querySelector('.kanban-card[data-task-id="t-css"]'));
    const overlay = modal()?.parentElement;
    assert.ok(overlay, 'setup: the task modal did not open');
    const cssDom = new JSDOM(`<!doctype html><html><head><style>${STYLE_CSS}</style></head><body>${overlay.outerHTML}</body></html>`,
        { pretendToBeVisual: true });
    const copy = cssDom.window.document.body.firstElementChild;
    assert.equal(cssDom.window.getComputedStyle(copy).display, 'flex');
    copy.classList.remove('active');
    assert.equal(cssDom.window.getComputedStyle(copy).display, 'none', '.modal-overlay without .active must stay hidden');
    clickIsland(document.querySelector('.task-modal-close'));
    await flush();
    assert.equal(modal(), null, 'the close button did not hide the modal');
});
