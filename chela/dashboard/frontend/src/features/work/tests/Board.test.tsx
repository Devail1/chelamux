// The Work board, rendered (CMX-233; ported from tests/kanban_flatten, kanban_hidden_columns,
// kanban_tracker_render, kanban_tracker_link, kanban_open_tracker_state, kanban_task_modal_wiring,
// task_handoff, task_progress_chip and risk_chip .test.mjs, which drove legacy kanban.js).
// CMX-316: the desktop board draws the compact card (its face and ⋮ are Compact.test.tsx's),
// Backlog and Done as rails at its edges, and Rows as a sortable table.
import { act, fireEvent, render } from '@testing-library/react';
import STYLE_CSS from '../../../../../static/style.css?raw';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, test, vi } from 'vitest';
import { setWorkPoll, type Run, type WorkPayload, type Workflow } from '../../../shared/store/work.ts';
import { TaskModal, closeTaskModal } from '../../taskmodal/index.ts';
import { Board, toasts } from '../index.ts';
import { must, viewport } from './setup.ts';

const LINEAR = [
  { name: 'Backlog', type: 'backlog', hidden: false },
  { name: 'Todo', type: 'unstarted', hidden: false },
  { name: 'In Progress', type: 'started', hidden: false },
  { name: 'In Review', type: 'started', hidden: false },
  { name: 'Done', type: 'completed', hidden: false },
  { name: 'Canceled', type: 'canceled', hidden: true },
];
const polls = vi.fn(() => Promise.resolve());

beforeEach(() => {
  polls.mockClear();
  setWorkPoll(polls);
});
afterEach(() => {
  act(() => closeTaskModal());
  act(() => toasts.set([]));
});

const run = (o: Partial<Run> & Record<string, unknown> = {}): Run => ({ task_id: 'CMX-1', title: 'a task', status: 'running', attempt: 1, started_at: '2026-10-07T00:00:00Z', ...o });
const wf = (o: Partial<Workflow> = {}): Workflow => ({ path: '/x/WORKFLOW.md', project_key: 'CMX', open_tasks: [], backlog_items: [], parked_tasks: [], active_runs: [], awaiting_review_runs: [], recent_runs: [], ...o });
const payload = (...wfs: Partial<Workflow>[]): WorkPayload => ({ configured: true, workflows: wfs.map(wf) });

let view: ReturnType<typeof render> | null = null;
function show(data: WorkPayload | null) {
  if (view) view.rerender(<><Board live={data} active /><TaskModal /></>);
  else view = render(<><Board live={data} active /><TaskModal /></>);
}
/** A reload: the board mounts again from what the browser kept. */
function reload(data: WorkPayload | null) {
  view?.unmount();
  view = null;
  show(data);
}
afterEach(() => {
  view = null;
});

const board = () => must(document.getElementById('kanban-board'), '#kanban-board');
/** The board left to right: a rail is `[key, 'rail', count]`, a column `[key, label, count]`. */
const layout = () =>
  [...board().querySelectorAll<HTMLElement>('.kanban-rail, .kanban-col')].map((e) =>
    e.classList.contains('kanban-rail') ? [e.dataset.col, 'rail', e.querySelector('.kanban-rail-count')?.textContent] : [e.dataset.col, e.querySelector('.kanban-col-label')?.textContent, e.querySelector('.col-count')?.textContent],
  );
const colLabels = () => [...board().querySelectorAll('.kanban-col-label')].map((e) => e.textContent);
const col = (label: string) => must([...board().querySelectorAll('.kanban-col')].find((c) => c.querySelector('.kanban-col-label')?.textContent === label), `column ${label}`);
const rail = (key: string) => must(board().querySelector<HTMLElement>(`.kanban-rail[data-col="${key}"]`), `rail ${key}`);
const cardOf = (id: string) => must(document.querySelector<HTMLElement>(`.wcard[data-task-id="${id}"]`), `card ${id}`);
const titles = (label: string) => [...col(label).querySelectorAll('.wcard-title')].map((e) => e.textContent);

// --- the desktop board: fixed columns and edge rails (CMX-316) ----------------------------------

test('chela\'s lanes: Backlog a rail on the left; To Do, In Progress and Review columns; Done and Archived rails on the right', () => {
  show(payload({ active_runs: [run()], backlog_items: [{ text: 'idea' }, { text: 'idea 2' }], recent_runs: [run({ task_id: 'd1', status: 'done' }), run({ task_id: 'c1', status: 'closed' })] }));
  expect(layout()).toEqual([
    ['backlog', 'rail', '2'], ['todo', 'To Do', '0'], ['in_progress', 'In Progress', '1'], ['review', 'Review', '0'], ['done', 'rail', '1'], ['archived', 'rail', '1'],
  ]);
  // the edges sit outside the scrolling middle, so they never scroll away
  expect([...must(board().querySelector('.kanban-middle')).children].map((c) => (c as HTMLElement).dataset.col)).toEqual(['todo', 'in_progress', 'review']);
  expect([...board().children].map((c) => c.className)).toEqual([
    'kanban-rail kanban-rail-left kanban-col-backlog', 'kanban-middle', 'kanban-rail kanban-rail-right kanban-col-done', 'kanban-rail kanban-rail-right kanban-col-archived',
  ]);
  expect([rail('backlog').getAttribute('aria-label'), rail('done').getAttribute('aria-label')]).toEqual(['Expand Backlog, 2 tasks', 'Expand Done, 1 task']);
  expect(rail('backlog').querySelector('.kanban-rail-label')?.textContent).toBe('Backlog');
});

test('with tracker_columns the columns ARE Linear\'s visible states (Backlog and Done as rails); without, chela\'s lanes', () => {
  show(payload({ tracker_columns: LINEAR }));
  expect(layout()).toEqual([['tr-backlog', 'rail', '0'], ['tr-todo', 'Todo', '0'], ['tr-in-progress', 'In Progress', '0'], ['tr-in-review', 'In Review', '0'], ['tr-done', 'rail', '0']]);
  show(payload({}));
  expect(layout().map((c) => c[0])).toEqual(['backlog', 'todo', 'in_progress', 'review', 'done', 'archived']);
});

test('a click on a rail opens it as a column at the same edge; its collapse button folds it back', () => {
  show(payload({ recent_runs: [run({ task_id: 'd1', status: 'done', pr_state: 'merged' }), run({ task_id: 'c1', status: 'closed' })] }));
  fireEvent.click(rail('archived'));
  const archived = col('Archived');
  expect([archived.className, archived.querySelector('.col-count')?.textContent]).toEqual(['kanban-col kanban-col-archived kanban-col-edge kanban-col-edge-right', '1']);
  expect(archived.querySelector('.wcard-closed .wcard-state')?.textContent?.trim()).toBe('closed, not merged');
  expect(board().lastElementChild).toBe(archived);
  expect(layout().map((c) => c[1])).toEqual(['rail', 'To Do', 'In Progress', 'Review', 'rail', 'Archived']);
  const fold = must(archived.querySelector<HTMLElement>('.kanban-col-collapse'));
  expect([fold.getAttribute('aria-label'), fold.getAttribute('aria-expanded')]).toEqual(['Collapse Archived', 'true']);
  fireEvent.click(fold);
  expect(layout().map((c) => c[1])).toEqual(['rail', 'To Do', 'In Progress', 'Review', 'rail', 'rail']);
  // a middle column has no collapse control
  expect(col('Review').querySelector('.kanban-col-collapse')).toBeNull();
});

test('the open rails survive a reload, per project view; a junk record is all rails', () => {
  const data = payload({ path: '/a/WORKFLOW.md', backlog_items: [{ text: 'idea' }] }, { path: '/b/WORKFLOW.md', recent_runs: [run({ status: 'done' })] });
  show(data);
  fireEvent.click(rail('done'));
  fireEvent.click(rail('backlog'));
  expect(JSON.parse(localStorage.getItem('chela_work_rails') ?? '')).toEqual({ '*': ['done', 'backlog'] });
  reload(data);
  expect(layout().map((c) => c[1])).toEqual(['Backlog', 'To Do', 'In Progress', 'Review', 'Done', 'rail']);
  // another project view keeps its own
  localStorage.setItem('chela_kanban_projects', '["/b/WORKFLOW.md"]');
  reload(data);
  expect(layout().map((c) => c[1])).toEqual(['rail', 'To Do', 'In Progress', 'Review', 'rail', 'rail']);
  fireEvent.click(rail('archived'));
  expect(JSON.parse(localStorage.getItem('chela_work_rails') ?? '')).toEqual({ '*': ['done', 'backlog'], '/b/WORKFLOW.md': ['archived'] });
  localStorage.setItem('chela_kanban_projects', '[]');
  for (const junk of ['{bad', '[1]', '{"*":"done"}']) {
    localStorage.setItem('chela_work_rails', junk);
    reload(data);
    expect(layout().map((c) => c[1])).toEqual(['rail', 'To Do', 'In Progress', 'Review', 'rail', 'rail']);
  }
});

test('a localStorage that throws still renders the rails, and a click still opens one for the page', () => {
  const deny = () => {
    throw new DOMException('denied', 'SecurityError');
  };
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(deny);
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(deny);
  show(payload({ recent_runs: [run({ status: 'done' })] }));
  expect(layout().map((c) => c[1])).toEqual(['rail', 'To Do', 'In Progress', 'Review', 'rail', 'rail']);
  fireEvent.click(rail('done'));
  expect(colLabels()).toContain('Done');
});

test('no "Hidden columns" panel: an empty column is drawn, saying so in words; a column hidden in ⋯ is not drawn', () => {
  show(payload({ tracker_columns: LINEAR, active_runs: [run({ tracker_state: 'In Progress' })] }));
  expect(document.querySelector('#kanban-hidden, .kanban-hidden-panel, .kanban-hidden-row')).toBeNull();
  expect(document.getElementById('work-board')?.textContent).not.toMatch(/Hidden/);
  expect(col('Todo').querySelector('.kanban-empty-col')?.textContent).toBe('Nothing here');
  expect(document.getElementById('work-board')?.textContent).not.toMatch(/[—]/);
  localStorage.setItem('chela_work_hidden_columns', '["tr-todo","tr-done"]');
  reload(payload({ tracker_columns: LINEAR, active_runs: [run({ tracker_state: 'In Progress' })] }));
  expect(layout().map((c) => c[0])).toEqual(['tr-backlog', 'tr-in-progress', 'tr-in-review']);
  for (const junk of ['{bad', '{"a":1}']) {
    localStorage.setItem('chela_work_hidden_columns', junk);
    reload(payload({ tracker_columns: LINEAR }));
    expect(layout()).toHaveLength(5);
  }
});

test('open Linear tasks land by their state (any casing), fall back to Todo, and an unknown state with no Todo is Unmapped', () => {
  const t = (id: string, state: string | null) => ({ id, title: `title ${id}`, tracker_state: state });
  show(payload({ tracker_columns: LINEAR, open_tasks: [t('CMX-1', 'backlog'), t('CMX-2', 'Todo'), t('CMX-3', null)] }));
  expect(rail('tr-backlog').querySelector('.kanban-rail-count')?.textContent).toBe('1');
  fireEvent.click(rail('tr-backlog'));
  expect(titles('Backlog')).toEqual(['title CMX-1']);
  expect(titles('Todo')).toEqual(['title CMX-2', 'title CMX-3']);
  show(payload({ tracker_columns: LINEAR.filter((c) => c.type !== 'unstarted'), open_tasks: [t('CMX-9', 'Triage')] }));
  expect(titles('Unmapped')).toEqual(['title CMX-9']);
});

test('a tracker state name is text in the column header, never markup', () => {
  show(payload({ tracker_columns: [{ name: 'Todo', type: 'unstarted' }, { name: '<b>QA</b>', type: 'started' }] }));
  expect(colLabels()).toEqual(['Todo', '<b>QA</b>']);
  expect(board().querySelector('.kanban-col-label b')).toBeNull();
});

test('CMX-193: Done says "latest" only when the server capped its runs, on both boards', () => {
  for (const cols of [LINEAR, undefined]) {
    const key = cols ? 'tr-done' : 'done';
    show(payload({ tracker_columns: cols, recent_runs_capped: true, recent_runs: [run({ status: 'done', tracker_state: 'Done' })] }));
    expect(rail(key).querySelector('.kanban-rail-label')?.textContent).toBe('Done · latest');
    show(payload({ tracker_columns: cols, recent_runs_capped: false, recent_runs: [run({ status: 'done', tracker_state: 'Done' })] }));
    expect(rail(key).querySelector('.kanban-rail-label')?.textContent).toBe('Done');
  }
});

test('the cards are the compact card: no branch line and no ✕ on the face; the branch is in its ⋮', () => {
  show(payload({ active_runs: [run({ task_id: 'h2', branch_name: 'dogfood/abc', pr_url: 'https://github.com/o/r/pull/9', pr_checks: 'passing' })], open_tasks: [{ id: 'o1', title: 'open', file: '/r/TODO.md', line_number: 4 }] }));
  expect(document.querySelectorAll('.kanban-card, .kanban-delete-btn, .kanban-requeue-btn, .kanban-merge-btn')).toHaveLength(0);
  expect(cardOf('h2').textContent).not.toContain('dogfood/abc');
  expect(cardOf('o1').textContent).not.toContain('TODO.md:4');
  expect([...cardOf('h2').querySelectorAll('.wcard-meta > span')].map((s) => s.textContent?.trim())).toEqual(['h2', '#9', 'running']);
  fireEvent.keyDown(must(cardOf('h2').querySelector('.wcard-menu-btn')), { key: 'Enter' });
  expect(document.querySelector('.wcard-overflow-menu .wcard-branch-item')?.textContent).toBe('dogfood/abc');
});

// --- the project filter --------------------------------------------------------------------------

test('a saved selection is read back on mount; a vanished project is dropped, never an empty board', () => {
  localStorage.setItem('chela_kanban_projects', '["/b/WORKFLOW.md"]');
  show(payload({ path: '/a/WORKFLOW.md', active_runs: [run({ task_id: 'A-1' })] }, { path: '/b/WORKFLOW.md', active_runs: [run({ task_id: 'B-1' })] }));
  expect([...board().querySelectorAll<HTMLElement>('.wcard')].map((c) => c.dataset.taskId)).toEqual(['B-1']);
  show(payload({ path: '/a/WORKFLOW.md', active_runs: [run({ task_id: 'A-1' })] }));
  expect([...board().querySelectorAll<HTMLElement>('.wcard')].map((c) => c.dataset.taskId)).toEqual(['A-1']);
});

test('the filter picks the board: a Linear project gets its columns, a markdown one its lanes; each only its own cards', () => {
  const data = payload({ tracker_columns: LINEAR, active_runs: [run({ task_id: 'CMX-1', tracker_state: 'In Progress' })] }, { path: '/y/WORKFLOW.md', active_runs: [run({ task_id: 'MD-1' })] });
  localStorage.setItem('chela_kanban_projects', '["/y/WORKFLOW.md"]');
  show(data);
  expect(layout().map((c) => c[0])).toEqual(['backlog', 'todo', 'in_progress', 'review', 'done', 'archived']);
  expect(col('In Progress').querySelector<HTMLElement>('.wcard')?.dataset.taskId).toBe('MD-1');
});

// --- before data, unconfigured ------------------------------------------------------------------

test('nothing before the first poll; the explainer when unconfigured or no workflow; no pills ever', () => {
  show(null);
  expect(document.getElementById('kanban-empty')).toBeNull();
  expect(document.getElementById('kanban-board')).toBeNull();
  expect(must(document.getElementById('work-board')).children).toHaveLength(0);
  for (const data of [{ configured: false }, { configured: true, workflows: [] }]) {
    show(data);
    expect(document.querySelector('#kanban-empty .work-empty-title')?.textContent).toBe('A board for work in flight');
    expect(document.getElementById('kanban-board')).toBeNull();
  }
  expect(document.querySelector('#kanban-filters, .kanban-filter-chip')).toBeNull();
});

// --- the card click opens the task modal --------------------------------------------------------

const modalTitle = () => document.querySelector('[role="dialog"].modal-task .task-modal-title')?.textContent ?? null;

test('clicking a card opens the task modal with THAT card, for every card kind', () => {
  localStorage.setItem('chela_work_rails', '{"*":["backlog"]}');
  show(payload({ backlog_items: [{ text: 'idea one' }, { text: 'idea two' }], parked_tasks: [{ id: 'p1', title: 'parked one', raw: '- [ ] parked one <!-- blocked: x -->' }],
    active_runs: [run({ task_id: 'r1', title: 'first run' }), run({ task_id: 'r2', title: 'second run' }), run({ task_id: 'r3', title: 'third run' })] }));
  const cards = [...board().querySelectorAll<HTMLElement>('.wcard')];
  for (const [i, want] of [[1, 'idea two'], [0, 'idea one'], [2, 'parked one'], [4, 'second run'], [5, 'third run'], [3, 'first run']] as const) {
    fireEvent.click(must(cards[i]));
    expect(modalTitle()).toContain(want);
    act(() => closeTaskModal());
  }
});

test('a poll that adds a card above keeps the cards below theirs: an open delete confirm survives', () => {
  show(payload({ active_runs: [run({ task_id: 'B', started_at: '2026-10-01' })] }));
  fireEvent.keyDown(must(cardOf('B').querySelector('.wcard-menu-btn')), { key: 'Enter' });
  fireEvent.click(must([...document.querySelectorAll<HTMLElement>('.wcard-overflow-menu [role="menuitem"]')].find((i) => i.textContent === 'Delete')));
  const open = document.querySelector('.wcard-overflow-menu');
  if (open) fireEvent.keyDown(open, { key: 'Escape' });
  show(payload({ active_runs: [run({ task_id: 'A', started_at: '2026-10-02' }), run({ task_id: 'B', started_at: '2026-10-01' })] }));
  expect([...col('In Progress').querySelectorAll<HTMLElement>('.wcard')].map((c) => c.dataset.taskId)).toEqual(['A', 'B']);
  expect(cardOf('B').querySelector('.kanban-confirm-msg')?.textContent).toBe('Delete this?');
  expect(cardOf('A').querySelector('.kanban-confirm')).toBeNull();
});

// --- Rows on the desktop: a table (CMX-316) -------------------------------------------------------

const PR = (n: number) => `https://github.com/o/r/pull/${n}`;
const NOW = Date.parse('2026-10-23T12:00:00Z');
const TABLE = payload({
  active_runs: [
    run({ task_id: 'CMX-162', project_key: 'CMX', task_number: 162, title: 'killed judge', pr_url: PR(847), pr_checks: 'pending', started_at: '2026-10-23T09:00:00Z' }),
    run({ task_id: 'CMX-303', project_key: 'CMX', task_number: 303, title: 'nightly sweep', pr_url: PR(838), pr_checks: 'failing', risk: 'high', rework_count: 1, started_at: '2026-10-23T11:30:00Z' }),
  ],
  awaiting_review_runs: [run({ task_id: 'CMX-314', project_key: 'CMX', task_number: 314, title: 'flaky', status: 'awaiting_review', pr_url: PR(848), pr_checks: 'passing', ended_at: '2026-10-21T12:00:00Z' })],
  open_tasks: [{ id: 'CMX-9', title: 'queued nine', risk: 'normal' }, { id: 'CMX-10', title: 'queued ten', risk: 'high' }, { id: 'CMX-11', title: 'queued eleven' }],
  recent_runs: [run({ task_id: 'CMX-1', status: 'done', ended_at: '2026-10-20T00:00:00Z' })],
});

function showRows(data: WorkPayload = TABLE) {
  vi.spyOn(Date, 'now').mockReturnValue(NOW);
  localStorage.setItem('chela_work_desktop_layout', 'rows');
  show(data);
}
const table = () => must(document.querySelector<HTMLTableElement>('table#work-table'), 'the Rows table');
const heads = () => [...table().querySelectorAll('thead th')].map((th) => th.textContent);
const cells = () => [...table().querySelectorAll<HTMLElement>('tbody')].map((g) => [g.dataset.col, ...[...g.querySelectorAll('.wtable-row')].map((r) => [...r.querySelectorAll('td:not(.wtable-menu)')].map((td) => td.textContent).join('|'))]);
const sortBy = (label: string) => fireEvent.click(must([...table().querySelectorAll<HTMLElement>('.wtable-sort')].find((b) => b.textContent === label), `sort ${label}`));
const ids = (key: string) => [...table().querySelectorAll(`tbody[data-col="${key}"] .wtable-id`)].map((td) => td.textContent);

test('Rows on the desktop is a table: ID, title, state, PR, CI, risk, age and a row ⋮, grouped by status in reading order', () => {
  showRows();
  const pane = must(document.getElementById('work-board'));
  expect([pane.className, pane.dataset.layout]).toEqual(['work-pane active', 'rows']);
  expect(document.getElementById('kanban-board')).toBeNull();
  expect(heads()).toEqual(['ID', 'Title', 'State', 'PR', 'CI', 'Risk', 'Age', 'Actions']);
  expect(cells()).toEqual([
    ['in_progress', 'CMX-303|nightly sweep|running|#838|failing|high|30m', 'CMX-162|killed judge|running|#847|pending||3h'],
    ['review', 'CMX-314|flaky|awaiting review|#848|passing||2d'],
    ['todo', 'CMX-9|queued nine|open|||normal|', 'CMX-10|queued ten|open|||high|', 'CMX-11|queued eleven|open||||'],
    // Done starts folded, as on the phone
    ['done'],
  ]);
  // orange only on a real warning, and always on a word
  expect([...table().querySelectorAll('td.warn')].map((td) => td.textContent)).toEqual(['failing', 'high', 'high']);
  expect(table().querySelectorAll('tbody[data-col="in_progress"] .wtable-row .wcard-menu-btn')).toHaveLength(2);
  expect(table().querySelector('.wtable-row[data-task-id="CMX-303"]')?.className).toBe('wtable-row wtable-row-running');
  expect(table().querySelector('thead th:last-child .sr-only')).not.toBeNull();
});

test('a column head sorts within each group: ascending, descending, then the board\'s order; blanks last both ways', () => {
  showRows();
  const sortOf = () => [...table().querySelectorAll('thead th')].map((th) => th.getAttribute('aria-sort')).slice(0, 7);
  expect(sortOf()).toEqual(Array(7).fill('none'));
  sortBy('Risk');
  expect(ids('todo')).toEqual(['CMX-10', 'CMX-9', 'CMX-11']);
  expect(sortOf()[5]).toBe('ascending');
  sortBy('Risk');
  expect(ids('todo')).toEqual(['CMX-9', 'CMX-10', 'CMX-11']);
  expect(sortOf()[5]).toBe('descending');
  sortBy('Risk');
  expect(ids('todo')).toEqual(['CMX-9', 'CMX-10', 'CMX-11']);
  expect(sortOf()[5]).toBe('none');
  sortBy('ID');
  expect(ids('todo')).toEqual(['CMX-9', 'CMX-10', 'CMX-11']);
  sortBy('ID');
  expect(ids('todo')).toEqual(['CMX-11', 'CMX-10', 'CMX-9']);
  // the board's order (newest first) is the baseline each pick moves away from
  for (const [label, asc] of [['Title', ['CMX-162', 'CMX-303']], ['State', ['CMX-303', 'CMX-162']], ['Age', ['CMX-303', 'CMX-162']], ['CI', ['CMX-303', 'CMX-162']], ['PR', ['CMX-303', 'CMX-162']]] as const) {
    sortBy(label);
    expect(ids('in_progress'), `${label} ascending`).toEqual(asc);
    sortBy(label);
    expect(ids('in_progress'), `${label} descending`).toEqual(label === 'State' ? asc : [...asc].reverse());
  }
  // a blank sorts last in either direction
  sortBy('CI');
  expect(ids('todo')).toEqual(['CMX-9', 'CMX-10', 'CMX-11']);
});

test('a re-sort moves an id-less row rather than remounting it: a focused backlog bullet keeps its focus', () => {
  localStorage.setItem('chela_kanban_collapsed', '[]');
  showRows(payload({ backlog_items: [{ text: 'bravo idea' }, { text: 'alpha idea' }] }));
  const row = () => [...table().querySelectorAll<HTMLElement>('tbody[data-col="backlog"] .wtable-row')];
  const bravo = must(row()[0]);
  bravo.focus();
  sortBy('Title');
  expect(row().map((r) => r.querySelector('.wtable-title-text')?.textContent)).toEqual(['alpha idea', 'bravo idea']);
  expect(row()[1]).toBe(bravo);
  expect(document.activeElement).toBe(bravo);
});

test('the keyboard walks the rows (↑ ↓ Home End); Enter opens the task; a group head folds and persists', () => {
  showRows();
  const rows = () => [...table().querySelectorAll<HTMLElement>('.wtable-row')];
  rows()[0]?.focus();
  fireEvent.keyDown(must(rows()[0]), { key: 'ArrowDown' });
  expect(document.activeElement).toBe(rows()[1]);
  fireEvent.keyDown(must(rows()[1]), { key: 'End' });
  expect(document.activeElement).toBe(rows()[5]);
  fireEvent.keyDown(must(rows()[5]), { key: 'ArrowDown' });
  expect(document.activeElement).toBe(rows()[5]);
  fireEvent.keyDown(must(rows()[5]), { key: 'Home' });
  expect(document.activeElement).toBe(rows()[0]);
  fireEvent.keyDown(must(rows()[0]), { key: 'ArrowUp' });
  expect(document.activeElement).toBe(rows()[0]);
  fireEvent.keyDown(must(rows()[2]), { key: 'Enter' });
  expect(modalTitle()).toContain('flaky');
  act(() => closeTaskModal());
  fireEvent.click(must(rows()[3]));
  expect(modalTitle()).toContain('queued nine');
  act(() => closeTaskModal());
  const fold = must(table().querySelector<HTMLElement>('tbody[data-col="review"] .wtable-group-btn'));
  expect(fold.getAttribute('aria-expanded')).toBe('true');
  fireEvent.click(fold);
  expect(cells()[1]).toEqual(['review']);
  expect(JSON.parse(localStorage.getItem('chela_kanban_collapsed') ?? '')).toContain('review');
});

test('Rows with nothing to show says so; a junk layout is the Board', () => {
  showRows(payload({}));
  expect(document.querySelector('#work-board .kanban-empty-col')?.textContent).toBe('Nothing here');
  localStorage.setItem('chela_work_desktop_layout', 'grid');
  reload(payload({}));
  expect(document.getElementById('work-board')?.dataset.layout).toBe('board');
});

test('an empty column on a phone is still left out (CMX-136), unless pinned', () => {
  viewport.phone = true;
  localStorage.setItem('chela_work_phone_layout', 'board');
  show(payload({ tracker_columns: LINEAR, active_runs: [run({ tracker_state: 'In Progress' })] }));
  expect([...document.querySelectorAll('.wtab-label')].map((e) => e.textContent)).toEqual(['In Progress']);
  localStorage.setItem('chela_kanban_colprefs', '{"tr-todo":"show"}');
  reload(payload({ tracker_columns: LINEAR, active_runs: [run({ tracker_state: 'In Progress' })] }));
  expect([...document.querySelectorAll('.wtab-label')].map((e) => e.textContent)).toEqual(['Todo', 'In Progress']);
});

// --- the phone layouts (CMX-315) ---------------------------------------------------------------

const groups = () => [...document.querySelectorAll<HTMLElement>('#work-rows .wrows-group')].map((g) => [g.dataset.col, g.querySelector('.wrows-count')?.textContent, g.querySelectorAll('.wrow').length]);

test('a phone draws Rows by default: one list grouped by status, in-flight work first, empty groups left out; no pills, no columns', () => {
  viewport.phone = true;
  show(payload({ active_runs: [run(), run({ task_id: 'CMX-2' })], awaiting_review_runs: [run({ task_id: 'CMX-3', status: 'awaiting_review' })], open_tasks: [{ id: 'CMX-4', title: 'queued' }] }));
  const pane = must(document.getElementById('work-board'));
  expect([pane.className, pane.dataset.layout]).toEqual(['work-pane active work-phone-rows', 'rows']);
  expect(groups()).toEqual([['in_progress', '2', 2], ['review', '1', 1], ['todo', '1', 1]]);
  expect(document.querySelectorAll('.kanban-filter-chip, .kanban-col, .kanban-card, #kanban-board').length).toBe(0);
  expect(must(document.querySelector('.wrows-group[data-col="todo"] .wrow-title')).textContent).toBe('queued');
});

test('an inactive Board pane is not .active, on a phone or wider', () => {
  render(<Board live={payload({})} active={false} />);
  expect(document.getElementById('work-board')?.className).toBe('work-pane');
  view?.unmount();
  document.body.replaceChildren();
  viewport.phone = true;
  render(<Board live={payload({})} active={false} />);
  expect(document.getElementById('work-board')?.className).toBe('work-pane work-phone-rows');
});

test('Rows: a head (click or Space) folds its group and persists; Backlog, Done and Archived start folded', () => {
  viewport.phone = true;
  const data = payload({ active_runs: [run()], backlog_items: [{ text: 'idea', file: '/x/BACKLOG.md' }], recent_runs: [run({ task_id: 'D', status: 'done' }), run({ task_id: 'C', status: 'closed' })] });
  show(data);
  const head = (key: string) => must(document.querySelector<HTMLElement>(`.wrows-group[data-col="${key}"] .wrows-head`));
  expect(groups()).toEqual([['in_progress', '1', 1], ['backlog', '1', 0], ['done', '1', 0], ['archived', '1', 0]]);
  expect([head('backlog').getAttribute('aria-expanded'), head('in_progress').getAttribute('aria-expanded')]).toEqual(['false', 'true']);
  fireEvent.click(head('backlog'));
  fireEvent.keyDown(head('in_progress'), { key: ' ' });
  expect(groups()).toEqual([['in_progress', '1', 0], ['backlog', '1', 1], ['done', '1', 0], ['archived', '1', 0]]);
  expect(JSON.parse(localStorage.getItem('chela_kanban_collapsed') ?? '')).toEqual(['done', 'archived', 'tr-backlog', 'tr-done', 'in_progress']);
  view?.unmount();
  view = null;
  show(data);
  expect(groups()).toEqual([['in_progress', '1', 0], ['backlog', '1', 1], ['done', '1', 0], ['archived', '1', 0]]);
  fireEvent.keyDown(head('backlog'), { key: 'x' });
  expect(head('backlog').getAttribute('aria-expanded')).toBe('true');
});

test('Rows: a junk collapsed set is the default; a Linear board folds its Backlog and Done', () => {
  viewport.phone = true;
  localStorage.setItem('chela_kanban_collapsed', '{bad');
  show(payload({ tracker_columns: LINEAR, active_runs: LINEAR.slice(0, 5).map((c, i) => run({ task_id: `CMX-${i}`, tracker_state: c.name })) }));
  expect(groups()).toEqual([['tr-in-progress', '1', 1], ['tr-in-review', '1', 1], ['tr-todo', '1', 1], ['tr-backlog', '1', 0], ['tr-done', '1', 0]]);
});

test('before the first poll a phone draws no list and no "Nothing here"', () => {
  viewport.phone = true;
  show(null);
  expect(must(document.getElementById('work-board')).children.length).toBe(0);
});

test('Rows with nothing to show says so', () => {
  viewport.phone = true;
  show(payload({}));
  expect(document.querySelector('#work-board .kanban-empty-col')?.textContent).toBe('Nothing here');
  expect(document.getElementById('work-rows')).toBeNull();
});

test('Board on a phone: the status tabs ARE the columns, no column header; a tap scrolls there and underlines it, a swipe moves the underline', () => {
  localStorage.setItem('chela_work_phone_layout', 'board');
  viewport.phone = true;
  const scrolled: [string | undefined, unknown][] = [];
  Element.prototype.scrollIntoView = function (this: HTMLElement, opts?: unknown) {
    scrolled.push([this.dataset.col, opts]);
  } as typeof Element.prototype.scrollIntoView;
  show(payload({ active_runs: [run()], awaiting_review_runs: [run({ task_id: 'CMX-3', status: 'awaiting_review' })], open_tasks: [{ id: 'CMX-4', title: 'queued' }] }));
  const tabs = () => [...document.querySelectorAll<HTMLElement>('.wtab')];
  expect(tabs().map((t) => [t.dataset.col, t.textContent, t.getAttribute('aria-current')])).toEqual([['todo', 'To Do 1', 'true'], ['in_progress', 'In Progress 1', 'false'], ['review', 'Review 1', 'false']]);
  expect([...board().children].map((c) => [c.className, c.querySelectorAll('.wcard').length])).toEqual([['wcol kanban-col-todo', 1], ['wcol kanban-col-in_progress', 1], ['wcol kanban-col-review', 1]]);
  expect(document.querySelectorAll('.kanban-col-head, .kanban-card').length).toBe(0);
  scrolled.length = 0;
  fireEvent.click(must(tabs()[2]));
  expect(scrolled).toContainEqual(['review', { behavior: 'smooth', inline: 'start', block: 'nearest' }]);
  expect(tabs().map((t) => t.className)).toEqual(['wtab', 'wtab', 'wtab active']);
  Object.defineProperty(board(), 'clientWidth', { configurable: true, value: 390 });
  board().scrollLeft = 392;
  fireEvent.scroll(board());
  expect(tabs().map((t) => t.getAttribute('aria-current'))).toEqual(['false', 'true', 'false']);
  Object.defineProperty(board(), 'clientWidth', { configurable: true, value: 0 });
  board().scrollLeft = 800;
  fireEvent.scroll(board());
  expect(tabs().map((t) => t.getAttribute('aria-current'))).toEqual(['false', 'true', 'false']);
});

test('Board on a phone with no column to draw says so', () => {
  localStorage.setItem('chela_work_phone_layout', 'board');
  viewport.phone = true;
  show(payload({}));
  expect(document.querySelector('#work-board .kanban-empty-col')?.textContent).toBe('Nothing here');
  expect(document.querySelectorAll('.wtab').length).toBe(0);
});

test('an unconfigured payload is the empty state on a phone too', () => {
  viewport.phone = true;
  show({ configured: false, workflows: [] });
  expect(document.getElementById('kanban-empty')).not.toBeNull();
  expect(document.getElementById('work-rows')).toBeNull();
});

// --- Liav 2026-10-19: a column is a lane, not a share of the window -----------------------------

// CMX-311: jsdom parses the 240 KB style.css on insertion, ~2 s on a loaded machine: once, in
// beforeAll (the hook's own 10 s budget), not inside a test's 5 s, and only around these tests.
describe('under style.css', () => {
  let sheet: HTMLStyleElement;
  beforeAll(() => {
    sheet = document.createElement('style');
    sheet.textContent = STYLE_CSS;
    document.head.append(sheet);
  });
  afterAll(() => sheet.remove());

  test('a column carries a max width, and the board scrolls sideways rather than squeezing (style.css)', () => {
    show(payload({ active_runs: [run()] }));
    // CMX-316: a fixed 320px lane, inside the 10-19 rule's 360-420px cap
    const c = getComputedStyle(col('In Progress'));
    expect([c.flexBasis, c.flexGrow, c.flexShrink, c.width, c.maxWidth]).toEqual(['320px', '0', '0', '320px', '400px']);
    const middle = getComputedStyle(must(board().querySelector('.kanban-middle')));
    expect([middle.display, middle.overflowX, middle.minWidth]).toEqual(['flex', 'auto', '0px']);
    // the edges never shrink or scroll: Done is never cut off
    const r = getComputedStyle(rail('done'));
    expect([r.flexBasis, r.flexShrink, r.width]).toEqual(['44px', '0', '44px']);
    expect(getComputedStyle(board()).overflowX).not.toBe('auto');
  });

  test('a rail writes its name down the rail; an open edge column is a lane like the others', () => {
    localStorage.setItem('chela_work_rails', '{"*":["done"]}');
    show(payload({ recent_runs: [run({ status: 'done' })] }));
    expect(getComputedStyle(must(rail('backlog').querySelector('.kanban-rail-label'))).writingMode).toBe('vertical-rl');
    expect(getComputedStyle(col('Done')).flexBasis).toBe('320px');
  });

  test('a parked card and its reason are on screen under style.css, a long reason clamped (CMX-298)', () => {
    localStorage.setItem('chela_work_rails', '{"*":["backlog"]}');
    show(payload({ parked_tasks: [{ id: 'p1', title: 'parked', reason: 'a fairly long blocked reason that would need to ellipsize past the card edge' }] }));
    const card = must(document.querySelector('.wcard-parked'));
    const note = must(card.querySelector('.wcard-note'));
    const [c, r] = [getComputedStyle(card), getComputedStyle(note)];
    expect([Number(c.opacity) > 0, c.display, c.visibility]).toEqual([true, 'flex', 'visible']);
    expect([r.display === 'none', r.visibility, r.overflow]).toEqual([false, 'visible', 'hidden']);
    expect(note.textContent?.trim()).toBe('a fairly long blocked reason that would need to ellipsize past the card edge');
  });
});
