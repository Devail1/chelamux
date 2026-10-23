// CMX-316: the desktop Work view (Liav's approved mockup, 10-23): ONE header row (the project
// switcher, Board | Runs | Schedules, Pause dispatch, New task, ⋯), the switcher as a dropdown,
// and ⋯ (Init a repo, Pause all agents with its confirm, the Board | Rows layout, the board's
// column options). The board itself (rails, compact cards, the Rows table) is Board.test.tsx's;
// the phone is Phone.test.tsx's.
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, test } from 'vitest';
import { installBridge } from '../../../shared/bridge/index.ts';
import { view } from '../../../shared/store/view.ts';
import { work, type Run, type WorkPayload, type Workflow } from '../../../shared/store/work.ts';
import { NewTask } from '../../newtask/index.ts';
import { TaskModal, closeTaskModal } from '../../taskmodal/index.ts';
import { WorkView, pollWork, toasts } from '../index.ts';
import { must, stubFetch } from './setup.ts';

installBridge(window);

let dispatcher: WorkPayload | null = null;

beforeEach(() => {
  stubFetch((url) => (url === '/api/dispatcher' ? { body: dispatcher } : url === '/api/schedules' || url === '/api/cron' ? { body: [] } : { body: { ok: true } }));
  act(() => view.set('work'));
});
afterEach(() => {
  act(() => {
    closeTaskModal();
    work.set(null);
    toasts.set([]);
  });
});

const run = (o: Partial<Run> & Record<string, unknown> = {}): Run => ({ task_id: 'CMX-1', title: 'a task', status: 'running', attempt: 1, started_at: '2026-10-07T00:00:00Z', ...o });
const wf = (path: string, o: Partial<Workflow> = {}): Workflow => ({ path, tracker_kind: 'linear', open_tasks: [], active_runs: [], awaiting_review_runs: [], recent_runs: [], ...o });
const CHELA = '/p/chelamux/WORKFLOW.md';
const LEAN = '/p/lean-alpha/WORKFLOW.md';
const TWO: WorkPayload = {
  configured: true,
  workflows: [
    wf(CHELA, { active_runs: [run(), run({ task_id: 'CMX-2', title: 'second' })], open_tasks: [{ id: 'CMX-3', title: 'queued one' }] }),
    wf(LEAN, { awaiting_review_runs: [run({ task_id: 'LAP-9', title: 'needs Liav', status: 'needs_human' })], open_tasks: [{ id: 'LAP-1', title: 'lap queued' }, { id: 'LAP-2', title: 'lap queued 2' }] }),
  ],
};

async function mount(data: WorkPayload | null) {
  dispatcher = data;
  const r = render(<><WorkView /><NewTask /><TaskModal /></>);
  if (data) await act(async () => pollWork());
  return r;
}

const head = () => must(document.querySelector<HTMLElement>('header.work-head'), 'the header row');
const title = () => [head().querySelector('.work-head-project')?.textContent, head().querySelector('.work-head-sub')?.textContent];
const openSwitcher = () => fireEvent.click(must(head().querySelector<HTMLElement>('.work-head-title')));
const switcher = () => screen.getByRole('dialog', { name: 'Switch project' });
const radios = () => within(switcher()).getAllByRole('radio').map((r) => [r.closest('label')?.querySelector('.wproj-name')?.textContent, r.getAttribute('aria-checked')]);
function more() {
  const trigger = must(document.getElementById('work-more-btn'));
  if (trigger.getAttribute('aria-expanded') !== 'true') fireEvent.keyDown(trigger, { key: 'Enter' });
  return must(document.querySelector<HTMLElement>('.work-head-menu'), 'the open ⋯ menu');
}
const rows = () => [...more().querySelectorAll('[role^="menuitem"], [role="separator"]')].map((i) => (i.getAttribute('role') === 'separator' ? '---' : [i.textContent, i.getAttribute('aria-checked')].filter((x) => x !== null).join(':')));
const row = (sel: string) => must(more().querySelector<HTMLElement>(sel), sel);

// --- the one header row ---------------------------------------------------------------------------

test('ONE header row: the switcher, Board | Runs | Schedules, Pause dispatch, New task and ⋯, in that order; no pill row, no toolbar', async () => {
  await mount(TWO);
  expect(document.querySelectorAll('header.work-head')).toHaveLength(1);
  const kids = [...head().children].map((c) => c.id || c.className);
  expect(kids).toEqual(['work-head-title', 'work-seg', 'work-head-spacer', 'dispatch-hold-btn', 'new-task-btn', 'work-more-btn']);
  expect([...head().querySelectorAll('.work-seg-btn')].map((b) => b.textContent)).toEqual(['Board', 'Runs', 'Schedules']);
  expect(title()).toEqual(['All projects', '2 running · 3 queued · dispatch on']);
  expect(head().querySelector('.work-head-name svg')).not.toBeNull();
  expect(must(document.getElementById('work-more-btn')).getAttribute('aria-label')).toBe('Work options');
  // one primary button; Pause dispatch is the quiet outline
  expect([...head().querySelectorAll('.btn-primary')].map((b) => b.id)).toEqual(['new-task-btn']);
  expect(must(document.getElementById('dispatch-hold-btn')).className).toBe('btn btn-md work-head-hold');
  for (const sel of ['.work-toolbar', '#kanban-filters', '.kanban-filter-chip', '#fleet-pause-btn', '.work-bar', '#kanban-hidden']) {
    expect([sel, document.querySelectorAll(sel).length]).toEqual([sel, 0]);
  }
  // nothing between the header and the board but the board
  expect(head().nextElementSibling?.id).toBe('work-board');
});

// --- the project switcher (a dropdown) ---------------------------------------------------------------

test('the title opens the switcher under it: a search field, All projects first, one row per project with its counts', async () => {
  await mount(TWO);
  openSwitcher();
  expect(switcher().className).toContain('work-switcher');
  const search = within(switcher()).getByRole('searchbox', { name: 'Search projects' });
  expect(document.activeElement).toBe(search);
  expect(radios()).toEqual([['All projects', 'true'], ['chelamux', 'false'], ['lean-alpha', 'false']]);
  expect([...switcher().querySelectorAll('.wproj-row')].map((r) => [r.querySelector('.wproj-counts')?.textContent, r.querySelector('.wproj-needs')?.textContent ?? null])).toEqual([
    ['2 running · 3 queued', null], ['2 running · 1 queued', null], ['0 running · 2 queued', '1 needs you'],
  ]);
  fireEvent.change(search, { target: { value: 'LEAN' } });
  expect(radios().map((r) => r[0])).toEqual(['All projects', 'lean-alpha']);
  fireEvent.change(search, { target: { value: 'zzz' } });
  expect(switcher().querySelector('.wproj-none')?.textContent).toBe('No project matches.');
});

test('picking a project: single-select, the dropdown closes, it becomes the title and the board follows; any close clears the search', async () => {
  await mount(TWO);
  openSwitcher();
  fireEvent.click(within(switcher()).getAllByRole('radio')[2] as HTMLElement);
  expect(screen.queryByRole('dialog', { name: 'Switch project' })).toBeNull();
  expect(title()).toEqual(['lean-alpha', '0 running · 2 queued · dispatch on']);
  expect(JSON.parse(localStorage.getItem('chela_kanban_projects') ?? '')).toEqual([LEAN]);
  expect([...document.querySelectorAll<HTMLElement>('#kanban-board .wcard')].map((c) => c.dataset.taskId)).toEqual(['LAP-1', 'LAP-2', 'LAP-9']);
  openSwitcher();
  expect(radios()).toEqual([['All projects', 'false'], ['lean-alpha', 'true'], ['chelamux', 'false']]);
  fireEvent.change(within(switcher()).getByRole('searchbox'), { target: { value: 'chela' } });
  fireEvent.keyDown(switcher(), { key: 'Escape' });
  openSwitcher();
  expect((within(switcher()).getByRole('searchbox') as HTMLInputElement).value).toBe('');
  fireEvent.click(within(switcher()).getAllByRole('radio')[0] as HTMLElement);
  expect(title()[0]).toBe('All projects');
  expect(localStorage.getItem('chela_kanban_projects')).toBe('[]');
});

// --- ⋯: layout and column options ----------------------------------------------------------------------

test('⋯ holds Init a repo, Pause all agents, the Board | Rows layout and Column options', async () => {
  await mount(TWO);
  expect(rows()).toEqual(['Init a repo', 'Pause all agents', '---', 'Board:true', 'Rows:false', '---', 'Column options6 of 6']);
  expect(row('[data-action="pause-all"]').className).toContain('wmenu-warn');
});

test('⋯ Rows switches the board to the table and persists; Board switches back; Column options is the Board\'s only', async () => {
  const r = await mount(TWO);
  fireEvent.click(row('[data-layout="rows"]'));
  expect(document.querySelector('table#work-table')).not.toBeNull();
  expect(document.getElementById('kanban-board')).toBeNull();
  expect(localStorage.getItem('chela_work_desktop_layout')).toBe('rows');
  // the menu stays open on a layout pick, and offers no column options for the table
  expect(rows()).toEqual(['Init a repo', 'Pause all agents', '---', 'Board:false', 'Rows:true']);
  r.unmount();
  await mount(TWO);
  expect(document.querySelector('table#work-table')).not.toBeNull();
  fireEvent.click(row('[data-layout="board"]'));
  expect(document.getElementById('kanban-board')).not.toBeNull();
  expect(localStorage.getItem('chela_work_desktop_layout')).toBe('board');
});

test('⋯ Column options: one checkbox per column; unchecking hides it from the board and persists; Back returns', async () => {
  await mount(TWO);
  fireEvent.click(row('[data-action="columns"]'));
  expect(rows()).toEqual(['Columns', '---', 'Backlog:true', 'To Do:true', 'In Progress:true', 'Review:true', 'Done:true', 'Archived:true']);
  const cols = () => [...document.querySelectorAll<HTMLElement>('#kanban-board [data-col]')].map((c) => c.dataset.col);
  expect(cols()).toEqual(['backlog', 'todo', 'in_progress', 'review', 'done', 'archived']);
  fireEvent.click(row('[data-col="archived"]'));
  fireEvent.click(row('[data-col="todo"]'));
  expect(cols()).toEqual(['backlog', 'in_progress', 'review', 'done']);
  expect(JSON.parse(localStorage.getItem('chela_work_hidden_columns') ?? '')).toEqual(['archived', 'todo']);
  expect(rows()).toContain('To Do:false');
  fireEvent.click(row('[data-col="todo"]'));
  expect(cols()).toEqual(['backlog', 'todo', 'in_progress', 'review', 'done']);
  fireEvent.click(must([...more().querySelectorAll<HTMLElement>('[role="menuitem"]')].find((i) => i.textContent === 'Columns')));
  expect(rows()).toContain('Column options5 of 6'); // Archived is still hidden
  // closing the menu forgets the page: the next open is the main one
  fireEvent.click(row('[data-action="columns"]'));
  fireEvent.keyDown(more(), { key: 'Escape' });
  expect(rows()[0]).toBe('Init a repo');
});

test('an unconfigured payload: no column options, the explainer, and the header all the same', async () => {
  await mount({ configured: false });
  expect(rows()).toEqual(['Init a repo', 'Pause all agents', '---', 'Board:true', 'Rows:false']);
  expect(document.getElementById('kanban-empty')).not.toBeNull();
  expect(title()).toEqual(['All projects', '0 running · 0 queued · dispatch on']);
});
