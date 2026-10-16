// 🙈 CMX-136 — the Work board's "Hidden columns" panel (Linear-style). The pure model
// (chela/dashboard/static/js/kanbanhiddenmodel.js) plus the REAL renderKanban in a real DOM:
// an empty column is listed in the panel, not drawn; a click pins it open (persisted per
// viewer); a hidden row is a drop target; a drag freezes the layout. Each test carries its
// negative control.
//
// Run: node --test tests/kanban_hidden_columns.test.mjs (tests/test_js_suites.py runs every
// .test.mjs inside pytest, by discovery).
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { bootDashboardDom, clickOnclick } from './js_helpers/dashboard_dom.mjs';
import {
    hiddenSummary, loadColPrefs, saveColPrefs, splitColumns,
} from '../chela/dashboard/static/js/kanbanhiddenmodel.js';

const KANBAN_BODY = `
<div class="work-pane active" id="work-board" data-seg="board">
  <div class="kanban-filters" id="kanban-filters"></div>
  <div class="kanban-nav-strip" id="kanban-nav-strip"></div>
  <div class="kanban-board-wrap"><div class="kanban-board" id="kanban-board"></div></div>
  <div id="kanban-empty" class="work-empty" style="display:none;"></div>
</div>`;

const STATES = [
    { name: 'Backlog', type: 'backlog', hidden: false },
    { name: 'Todo', type: 'unstarted', hidden: false },
    { name: 'In Progress', type: 'started', hidden: false },
    { name: 'In Review', type: 'started', hidden: false },
    { name: 'Done', type: 'completed', hidden: false },
];

let renderKanban;
let reloads = 0;

before(async () => {
    ({ modules: { kanban: { renderKanban } } } = await bootDashboardDom({
        body: KANBAN_BODY, extraModules: ['kanban.js'],
        // A click's pollWork() never lands: each test paints the board itself.
        fetchImpl: () => new Promise(() => {}),
    }));
});

beforeEach(() => {
    localStorage.clear();
    for (const k of ['tr-backlog', 'tr-todo', 'tr-in-progress', 'tr-in-review', 'tr-done']) {
        window.chela.kanbanHideCol(k);
    }
});

const run = o => ({ task_id: 'CMX-1', title: 't', status: 'running', attempt: 1,
                    started_at: '2026-10-14T00:00:00Z', ...o });
const payload = (active = []) => ({ configured: true, workflows: [{
    path: '/x/WORKFLOW.md', project_key: 'CMX', open_tasks: [], backlog_items: [],
    parked_tasks: [], active_runs: active, awaiting_review_runs: [], recent_runs: [],
    tracker_columns: STATES }] });

const board = () => document.querySelector('#kanban-board');
const panel = () => document.querySelector('#kanban-hidden');
const boardCols = () => [...board().querySelectorAll('.kanban-col-label')].map(e => e.textContent);
const rows = () => [...panel().querySelectorAll('.kanban-hidden-row')];
const rowLabels = () => rows().map(r => r.querySelector('.kanban-hidden-label').textContent);
const row = label => rows().find(r => r.querySelector('.kanban-hidden-label').textContent === label);
const cols = (...spec) => spec.map(([key, n]) => ({ key, label: key.toUpperCase(), cards: Array(n).fill({}) }));

// An inline handler compiled off the REAL attribute, with an event that records preventDefault.
function fire(el, attr) {
    const ev = { target: el, currentTarget: el, prevented: false,
                 preventDefault() { this.prevented = true; }, stopPropagation() {} };
    new Function('chela', 'event', el.getAttribute(attr)).call(el, window.chela, ev);
    return ev;
}

// ---- the pure model -------------------------------------------------------------------

test('splitColumns: empty → hidden, carded → visible, both in board order; a pin keeps an empty one', () => {
    const { visible, hidden } = splitColumns(cols(['a', 0], ['b', 2], ['c', 0], ['d', 1]), {});
    assert.deepEqual(visible.map(c => c.key), ['b', 'd']);
    assert.deepEqual(hidden.map(c => c.key), ['a', 'c']);
    const pinned = splitColumns(cols(['a', 0], ['b', 2], ['c', 0]), { c: 'show' });
    assert.deepEqual(pinned.visible.map(c => c.key), ['b', 'c']);
    assert.deepEqual(pinned.hidden.map(c => c.key), ['a']);
});

test('loadColPrefs: only "show" survives; throwing storage, junk JSON and arrays are the default {}', () => {
    const st = v => ({ getItem: () => v });
    assert.deepEqual(loadColPrefs(st('{"a":"show","b":"hide","c":1}')), { a: 'show' });
    assert.deepEqual(loadColPrefs({ getItem: () => { throw new RangeError('denied'); } }), {});
    assert.deepEqual(loadColPrefs(st('{not json')), {});
    assert.deepEqual(loadColPrefs(st('["show"]')), {});
    assert.deepEqual(loadColPrefs(st('null')), {});
    assert.deepEqual(loadColPrefs(st(null)), {});
});

test('saveColPrefs writes the literal key and reports a refused write', () => {
    const mem = {};
    assert.equal(saveColPrefs({ setItem: (k, v) => { mem[k] = v; } }, { a: 'show' }), true);
    assert.deepEqual(mem, { chela_kanban_colprefs: '{"a":"show"}' });
    assert.equal(saveColPrefs({ setItem: () => { throw new TypeError('quota'); } }, {}), false);
});

test('hiddenSummary is the phone one-liner; nothing hidden → empty', () => {
    assert.equal(hiddenSummary(cols(['backlog', 0], ['review', 0])), 'Hidden: BACKLOG · REVIEW');
    assert.equal(hiddenSummary([]), '');
});

// ---- the rendered board ---------------------------------------------------------------

test('an empty column is in the panel, not the board; a carded one is on the board', () => {
    renderKanban(payload([run({ tracker_state: 'In Progress' })]));
    assert.deepEqual(boardCols(), ['In Progress']);
    assert.deepEqual(rowLabels(), ['Backlog', 'Todo', 'In Review', 'Done']);
    assert.deepEqual(rows().map(r => r.querySelector('.col-count').textContent), ['0', '0', '0', '0']);
    assert.equal(board().style.getPropertyValue('--kanban-cols'), '1', 'visible columns share the width');
    assert.notEqual(panel().style.display, 'none');
    assert.equal(panel().querySelector('.kanban-hidden-inline').textContent,
                 'Hidden: Backlog · Todo · In Review · Done');
    // The swipe nav strip lists only the drawn columns.
    assert.deepEqual([...document.querySelectorAll('.kanban-nav-label')].map(e => e.textContent), ['In Progress']);
});

test('a count change moves a column between the panel and the board', () => {
    renderKanban(payload([run({ tracker_state: 'Todo' })]));
    assert.ok(boardCols().includes('Todo') && !rowLabels().includes('Todo'));
    renderKanban(payload([]));
    assert.ok(!boardCols().includes('Todo') && rowLabels().includes('Todo'));
});

test('nothing hidden → no panel; an unconfigured board hides it too', () => {
    renderKanban(payload(STATES.map((s, i) => run({ task_id: `CMX-${i}`, tracker_state: s.name }))));
    assert.equal(panel().style.display, 'none');
    renderKanban(payload([]));
    assert.notEqual(panel().style.display, 'none');
    renderKanban({ configured: false });
    assert.equal(panel().style.display, 'none');
});

test('clicking a hidden row shows it as a column and persists; Hide puts it back', async t => {
    renderKanban(payload([]));
    clickOnclick(row('In Review'));
    assert.deepEqual(JSON.parse(localStorage.getItem('chela_kanban_colprefs')), { 'tr-in-review': 'show' });
    renderKanban(payload([]));
    assert.deepEqual(boardCols(), ['In Review']);
    assert.ok(!rowLabels().includes('In Review'));
    // A reload: a fresh copy of the module reads the pin back from storage.
    const saved = { ...window.chela };
    t.after(() => Object.assign(window.chela, saved));
    const fresh = await import(`../chela/dashboard/static/js/kanban.js?reload=${++reloads}`);
    fresh.renderKanban(payload([]));
    assert.deepEqual(boardCols(), ['In Review'], 'the pin did not survive a reload');
    // Control: Hide clears it.
    fire(board().querySelector('.kanban-col-hide'), 'onclick');
    assert.equal(localStorage.getItem('chela_kanban_colprefs'), '{}');
    fresh.renderKanban(payload([]));
    assert.deepEqual(boardCols(), []);
});

test('storage that throws → the default (empty hidden), and a click still works for the session', async () => {
    const real = globalThis.localStorage;
    const boom = { getItem() { throw new DOMException('denied', 'SecurityError'); },
                   setItem() { throw new DOMException('denied', 'SecurityError'); }, clear() {} };
    const saved = { ...window.chela };
    Object.defineProperty(globalThis, 'localStorage', { value: boom, configurable: true, writable: true });
    try {
        const fresh = await import(`../chela/dashboard/static/js/kanban.js?reload=${++reloads}`);
        fresh.renderKanban(payload([run({ tracker_state: 'Done' })]));
        assert.deepEqual(boardCols(), ['Done']);
        assert.ok(rowLabels().includes('Todo'));
        clickOnclick(row('Todo'));            // must not throw
        fresh.renderKanban(payload([run({ tracker_state: 'Done' })]));
        assert.deepEqual(boardCols(), ['Todo', 'Done']);
    } finally {
        Object.defineProperty(globalThis, 'localStorage', { value: real, configurable: true, writable: true });
        Object.assign(window.chela, saved);
    }
});

test('a hidden row is a drop target: hover outlines it, a drop hands the card + column to kanbanMoveCard', () => {
    const card = run({ task_id: 'CMX-7', tracker_state: 'In Progress' });
    renderKanban(payload([card]));
    const el = board().querySelector('.kanban-card[data-kidx]');
    assert.equal(el.draggable, true);
    const target = row('In Review');
    // Control: no drag in flight → not a drop target.
    assert.equal(fire(target, 'ondragover').prevented, false);
    el.dispatchEvent(new window.Event('dragstart', { bubbles: true }));
    assert.equal(fire(target, 'ondragover').prevented, true);
    assert.ok(target.classList.contains('kanban-hidden-row-over'));
    fire(target, 'ondragleave');
    assert.ok(!target.classList.contains('kanban-hidden-row-over'));

    const moves = [];
    const realMove = window.chela.kanbanMoveCard;
    window.chela.kanbanMoveCard = (c, col) => moves.push([c.task_id, col.key, col.label]);
    try {
        fire(target, 'ondragover');
        const ev = fire(target, 'ondrop');
        assert.equal(ev.prevented, true);
        assert.ok(!target.classList.contains('kanban-hidden-row-over'));
        assert.deepEqual(moves, [['CMX-7', 'tr-in-review', 'In Review']]);
    } finally {
        window.chela.kanbanMoveCard = realMove;
        el.dispatchEvent(new window.Event('dragend', { bubbles: true }));
    }
});

test('the layout does not change mid-drag; the parked poll paints on dragend', () => {
    renderKanban(payload([run({ tracker_state: 'In Progress' })]));
    const el = board().querySelector('.kanban-card[data-kidx]');
    el.dispatchEvent(new window.Event('dragstart', { bubbles: true }));
    renderKanban(payload([run({ tracker_state: 'Todo' })]));   // a poll lands mid-drag
    assert.deepEqual(boardCols(), ['In Progress'], 'the board re-laid out under the drag');
    assert.ok(rowLabels().includes('Todo'));
    board().dispatchEvent(new window.Event('dragend'));
    assert.deepEqual(boardCols(), ['Todo'], 'the poll that landed mid-drag was never painted');
    // Control: with no drag in flight a poll paints at once.
    renderKanban(payload([run({ tracker_state: 'Done' })]));
    assert.deepEqual(boardCols(), ['Done']);
});

test('the default kanbanMoveCard says plainly that nothing moved, and announces the drop', () => {
    const seen = [];
    const on = e => seen.push([e.detail.card.task_id, e.detail.to]);
    document.addEventListener('chela:kanban-move', on);
    try {
        window.chela.kanbanMoveCard({ task_id: 'CMX-9' }, { key: 'tr-done', label: 'Done' });
    } finally {
        document.removeEventListener('chela:kanban-move', on);
    }
    assert.deepEqual(seen, [['CMX-9', 'tr-done']]);
    const toast = [...document.querySelectorAll('.kanban-toast')].pop();
    assert.match(toast.textContent, /CMX-9 was not moved to Done/);
    toast.remove();
});

test('a dragstart that is not on a card is ignored: no error, no drop target', () => {
    renderKanban(payload([run({ tracker_state: 'In Progress' })]));
    const errors = [];
    const onErr = e => { errors.push(e.error || e.message); e.preventDefault(); };
    window.addEventListener('error', onErr);
    try {
        board().querySelector('.kanban-col-head').dispatchEvent(new window.Event('dragstart', { bubbles: true }));
    } finally {
        window.removeEventListener('error', onErr);
    }
    assert.deepEqual(errors, []);
    assert.equal(fire(row('Todo'), 'ondragover').prevented, false);
});

test('dragstart puts the card index on the dataTransfer (Firefox starts no drag without it)', () => {
    renderKanban(payload([run({ tracker_state: 'In Progress' })]));
    const el = board().querySelector('.kanban-card[data-kidx]');
    const set = [];
    const ev = new window.Event('dragstart', { bubbles: true });
    Object.defineProperty(ev, 'dataTransfer', { value: { setData: (t, v) => set.push([t, v]) } });
    el.dispatchEvent(ev);
    el.dispatchEvent(new window.Event('dragend', { bubbles: true }));
    assert.deepEqual(set, [['text/plain', el.dataset.kidx]]);
});
