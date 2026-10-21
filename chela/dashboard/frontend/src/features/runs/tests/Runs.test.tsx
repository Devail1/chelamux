// The Runs segment (CMX-231; ported from tests/runs_linear.test.mjs, the runs-table half of
// tests/task_progress_chip.test.mjs and the init case of em_dash_rewrite_branches.test.mjs).
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { setWorkPoll, work, type Run, type Workflow } from '../../../shared/store/work.ts';
import { InitRepo, Runs, launcherPaths, openInitRepo, postWorkDelete } from '../index.ts';

const URL = 'https://linear.app/acme/issue/CMX-12/tighten-top-row';
let calls: { url: string; init?: RequestInit }[] = [];
let reply: (url: string) => Promise<{ status: number; body: unknown }> = () => Promise.resolve({ status: 200, body: {} });
const polls = vi.fn(() => Promise.resolve());

beforeEach(() => {
  calls = [];
  polls.mockClear();
  setWorkPoll(polls);
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const { status, body } = await reply(url);
    return { ok: status < 400, status, json: () => (body === undefined ? Promise.reject(new SyntaxError('<')) : Promise.resolve(body)) };
  });
});
afterEach(() => {
  act(() => work.set(null));
  cleanup();
  vi.unstubAllGlobals();
});

function task(id: string, group: string, extra = {}) {
  return { id, title: `title ${id}`, file: '', line_number: 0, group, tracker_state: ({ ready: 'Todo', in_progress: 'In Progress', backlog: 'Backlog' } as Record<string, string>)[group],
    tracker_url: `https://linear.app/acme/issue/${id}`, risk: 'normal', ...extra };
}
const run = (task_id: string, status: string, extra: Partial<Run> = {}): Run => ({ task_id, title: task_id, status, project_key: 'CMX', task_number: +task_id.slice(4), ...extra });

function show(wf: Partial<Workflow>) {
  render(<Runs />);
  act(() => work.set({ configured: true, workflows: [{ path: '/repo/WORKFLOW.md', open_tasks: [], active_runs: [], awaiting_review_runs: [], recent_runs: [], ...wf }] }));
  return document.getElementById('dispatcher-list') as HTMLElement;
}
const firstCells = (el: Element | null) => [...(el?.querySelectorAll('tbody tr') ?? [])].map((tr) => tr.querySelector('td')?.textContent?.trim());
const summary = (list: HTMLElement) => list.querySelector('.runs-summary')?.textContent ?? '';

test('nothing before the first poll; the explainer when no workflow is configured', () => {
  render(<Runs />);
  expect(document.querySelector('.work-empty')).toBeNull();
  act(() => work.set({ configured: false, workflows: [] }));
  expect(document.querySelector('.work-empty-title')?.textContent).toBe('Turn a TODO list into pull requests');
  act(() => work.set({ configured: true, workflows: [] }));
  expect(document.querySelector('.work-empty')).not.toBeNull();
  expect(document.getElementById('dispatcher-list')).toBeNull();
});

test('linear open tasks: identifier linked, state + risk shown, no file:line, no ×', () => {
  const list = show({ tracker_kind: 'linear', open_tasks: [task('CMX-12', 'ready', { tracker_url: URL, risk: 'high', file: '/x/TODO.md', line_number: 7, blocked: true })] });
  const link = list.querySelector('a.run-tracker-link') as HTMLAnchorElement;
  expect(link.getAttribute('href')).toBe(URL);
  expect(link.textContent).toBe('CMX-12');
  const row = link.closest('tr') as HTMLElement;
  expect(row.querySelector('td[data-label="State"]')?.textContent).toBe('Todo · blocked');
  expect(row.querySelector('td[data-label="Risk"]')?.textContent).toBe('risk: high');
  expect(list.querySelector('.dispatcher-delete-btn')).toBeNull();
  expect(list.querySelectorAll('table').length).toBe(1); // empty groups render no table
  expect(list.textContent).not.toContain('TODO.md:7');
});

test('linear open tasks split Ready, In progress, Backlog (collapsed), counts in headers', () => {
  const list = show({ tracker_kind: 'linear', open_tasks: [task('CMX-1', 'backlog'), task('CMX-2', 'ready'), task('CMX-3', 'in_progress'), task('CMX-4', 'ready'), task('CMX-5', 'backlog'), task('CMX-6', 'backlog')] });
  const heads = [...list.querySelectorAll('.open-section')];
  expect(heads.map((h) => h.textContent)).toEqual(['Ready (Todo) · 2', 'In progress / In review · 1', 'Backlog · 3']);
  const tables = heads.map((h) => (h.tagName === 'SUMMARY' ? h.parentElement : h.nextElementSibling));
  expect(tables.map(firstCells)).toEqual([['CMX-2', 'CMX-4'], ['CMX-3'], ['CMX-1', 'CMX-5', 'CMX-6']]);
  expect((list.querySelector('details.open-backlog') as HTMLDetailsElement).open).toBe(false);
  expect(summary(list)).toMatch(/^2 ready ·/); // READY, not every non-terminal issue
});

test('a todo workflow keeps the Source column and the source-line ×', () => {
  const list = show({ tracker_kind: 'markdown', open_tasks: [{ id: 'abc', title: 'ship it', file: '/x/TODO.md', line_number: 7, group: 'ready' }] });
  expect(list.querySelector('td[data-label="Source"]')?.textContent).toBe('/x/TODO.md:7');
  expect(list.querySelector('.open-section')).toBeNull();
  expect(list.querySelector('.dispatcher-delete-btn')).not.toBeNull();
  expect(summary(list)).toMatch(/^1 open task ·/);
  act(() => work.set({ configured: true, workflows: [{ path: 'p', open_tasks: [{ id: 'a', title: 't', file: 'f' }, { id: 'b', title: 'u', file: 'f' }] }] }));
  expect(summary(document.getElementById('dispatcher-list') as HTMLElement)).toMatch(/^2 open tasks ·/);
});

test('an open task with no file or no title has no ×; a workflow error is shown', () => {
  const list = show({ error: 'bad yaml', open_tasks: [{ id: 'a', title: 't', file: '' }, { id: 'b', title: '', file: '/f' }] });
  expect(list.querySelector('.dispatcher-delete-btn')).toBeNull();
  expect(list.querySelector('.runs-error')?.textContent).toBe('Error: bad yaml');
});

test('a run CMX-N links to its Linear issue only on a linear workflow', () => {
  const r = run('CMX-12', 'running', { tracker_url: URL });
  let list = show({ tracker_kind: 'linear', active_runs: [r] });
  expect(list.querySelector('[data-runs="active"] a.run-tracker-link')?.getAttribute('href')).toBe(URL);
  cleanup();
  list = show({ tracker_kind: 'markdown', active_runs: [r] });
  expect(list.querySelector('a.run-tracker-link')).toBeNull();
  expect(list.querySelector('[data-runs="active"] .ts')?.textContent).toBe('CMX-12');
});

test('needs_human runs sit in their own table above Active, and nowhere else', () => {
  const list = show({ tracker_kind: 'linear', active_runs: [run('CMX-1', 'running')], awaiting_review_runs: [run('CMX-2', 'needs_human'), run('CMX-3', 'awaiting_review')],
    recent_runs: [run('CMX-4', 'needs_human'), run('CMX-5', 'done')] });
  const ids = (sel: string) => [...list.querySelectorAll<HTMLElement>(`[data-runs="${sel}"] tbody tr`)].map((tr) => tr.dataset.taskId);
  expect(ids('needs_human')).toEqual(['CMX-2', 'CMX-4']);
  expect([ids('active'), ids('awaiting'), ids('recent')]).toEqual([['CMX-1'], ['CMX-3'], ['CMX-5']]);
  expect([...list.querySelectorAll<HTMLElement>('[data-runs]')].map((d) => d.dataset.runs)).toEqual(['needs_human', 'active', 'awaiting', 'recent']);
  expect(summary(list)).toBe('0 ready · 2 needing a human · 1 active · 1 awaiting review · 1 recent');
  expect(list.querySelector('[data-runs="recent"]')?.previousElementSibling?.textContent).toBe('Recent runs');
});

const TASKS = { total: 4, done: 3, in_progress: { subject: 'wire the tooltip guard' }, blocked: [{ subject: 'ship the release notes', blocked_by: ['t-1', 't-2'] }, { subject: 'audit the migration', blocked_by: [] }] };

test('a run row: badge, task progress under the Tasks heading, PR, times, attempt', () => {
  const list = show({ active_runs: [run('CMX-7', 'needs_human', { tasks: TASKS, pr_url: 'https://github.com/o/r/pull/42', branch_name: 'b7', window_name: 'w7', attempt: 2, started_at: '2026-09-09T10:05:00Z', title: 'x'.repeat(90) })] });
  const row = list.querySelector('[data-runs="needs_human"] tbody tr') as HTMLElement;
  const cell = (l: string) => row.querySelector(`td[data-label="${l}"]`) as HTMLElement;
  expect(cell('Status').querySelector('.badge')?.className).toBe('badge badge-needs-human');
  const chip = cell('Tasks').querySelector('.task-progress-chip') as HTMLElement;
  expect(chip.textContent?.trim()).toBe('3/4');
  expect(chip.title).toBe('3 of 4 tasks done\nIn progress: wire the tooltip guard\n"ship the release notes" blocked by t-1, t-2\n"audit the migration" blocked');
  const heads = [...list.querySelectorAll('[data-runs="needs_human"] thead th')].map((th) => th.textContent);
  const cells = [...row.querySelectorAll('td')];
  expect(heads.length).toBe(cells.length);
  expect(heads.indexOf('Tasks')).toBe(cells.findIndex((td) => td.dataset.label === 'Tasks'));
  const pr = cell('PR').querySelector('a.pr-badge') as HTMLAnchorElement;
  expect([pr.textContent, pr.href, pr.target]).toEqual(['#42', 'https://github.com/o/r/pull/42', '_blank']);
  expect([cell('Branch').textContent, cell('Window').textContent, cell('Attempt').textContent]).toEqual(['b7', 'w7', '2']);
  expect(cell('Started').textContent).toMatch(/^\d\d:\d\d Sep \d+$/);
  expect(cell('Ended').textContent).toBe('-');
  expect(row.querySelector('b')?.textContent).toBe('x'.repeat(80));
});

test('no task data, no PR, no error: those cells are empty (cell-empty), never "0/0"', () => {
  const list = show({ active_runs: [run('CMX-8', 'running', { tasks: null })] });
  const row = list.querySelector('tbody tr') as HTMLElement;
  for (const l of ['Tasks', 'PR', 'Error', 'Branch', 'Attempt']) {
    const td = row.querySelector(`td[data-label="${l}"]`) as HTMLElement;
    expect([l, td.textContent, td.className]).toEqual([l, '', 'cell-empty']);
  }
  expect((row.querySelector('td[data-label="Started"]') as HTMLElement).className).toBe('ts');
});

test('the Error cell shows "Closed: <reason>" for a hand-closed run, else its last_error', () => {
  let list = show({ recent_runs: [run('CMX-9', 'closed', { close_reason: 'superseded by cmx-403', last_error: 'tmux window disappeared' })] });
  expect(list.querySelector('td[data-label="Error"]')?.textContent).toBe('Closed: superseded by cmx-403');
  cleanup();
  list = show({ recent_runs: [run('CMX-9', 'failed', { last_error: 'tmux window disappeared' })] });
  expect(list.querySelector('td[data-label="Error"]')?.textContent).toBe('tmux window disappeared');
});

test('a run row × asks, then POSTs the delete and refreshes Work; Cancel restores the row', async () => {
  reply = () => Promise.resolve({ status: 200, body: { ok: true } });
  const list = show({ active_runs: [run('CMX-1', 'running')] });
  const row = () => list.querySelector('tbody tr') as HTMLElement;
  fireEvent.click(screen.getByRole('button', { name: 'Delete this row' }));
  expect(row().className).toBe('row-confirming');
  expect((row().querySelector('.row-confirm-cell') as HTMLTableCellElement).colSpan).toBe(11);
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(row().className).toBe('');
  expect(row().querySelector('.row-confirm-cell')).toBeNull();
  expect(calls).toEqual([]);
  fireEvent.click(screen.getByRole('button', { name: 'Delete this row' }));
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Delete' })));
  expect(calls.map((c) => [c.url, c.init?.method, c.init?.body])).toEqual([['/api/dispatcher/delete', 'POST', JSON.stringify({ kind: 'run', task_id: 'CMX-1' })]]);
  expect(polls).toHaveBeenCalledTimes(1);
});

test('a source-line delete posts file + text; a refusal shows the error and Close restores', async () => {
  reply = () => Promise.resolve({ status: 409, body: { ok: false, error: 'line moved' } });
  const list = show({ open_tasks: [{ id: 'abc', title: 'ship it', file: '/x/TODO.md', line_number: 7 }] });
  fireEvent.click(screen.getByRole('button', { name: 'Delete this row' }));
  expect((list.querySelector('.row-confirm-cell') as HTMLTableCellElement).colSpan).toBe(4);
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Delete' })));
  expect(JSON.parse(String(calls[0]?.init?.body))).toEqual({ kind: 'source-line', file: '/x/TODO.md', text: 'ship it' });
  expect(list.querySelector('.kanban-confirm-msg')?.textContent).toBe('line moved');
  expect(polls).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Close' }));
  expect(list.querySelector('.row-confirm-cell')).toBeNull();
});

test('postWorkDelete: an error string for a failed POST, a non-JSON error, a network error', async () => {
  reply = () => Promise.resolve({ status: 500, body: undefined });
  expect(await postWorkDelete({ kind: 'run', task_id: 'x' })).toBe('HTTP 500');
  reply = () => Promise.resolve({ status: 200, body: { ok: false } });
  expect(await postWorkDelete({ kind: 'run', task_id: 'x' })).toBe('HTTP 200');
  reply = () => Promise.reject(new TypeError('Failed to fetch'));
  expect(await postWorkDelete({ kind: 'run', task_id: 'x' })).toBe('TypeError: Failed to fetch');
  expect(polls).not.toHaveBeenCalled();
  reply = () => Promise.resolve({ status: 200, body: { ok: true } });
  expect(await postWorkDelete({ kind: 'run', task_id: 'x' })).toBeNull();
  expect(polls).toHaveBeenCalledTimes(1);
});

// --- Init a repo ----------------------------------------------------------------------

const INIT = { ok: true, path: '/srv/repo', created: ['WORKFLOW.md', 'TODO.md'], skipped: [], is_git: true };
const result = () => document.querySelector('.init-result') as HTMLElement;
// Each report line as `kind: text`, and whether it drew its icon.
const lines = () => [...result().querySelectorAll<HTMLElement>('.init-line')].map((l) => `${l.className.slice(15)}${l.querySelector('svg') ? '+icon' : ''}: ${l.textContent?.trim()}`);

async function seed(path: string, body: unknown) {
  reply = () => Promise.resolve({ status: 200, body });
  render(<InitRepo />);
  act(() => openInitRepo({ favorites: [{ path: '/a' }, null], recent: [{ path: '/b' }, { path: '/a' }] }));
  fireEvent.change(screen.getByRole('combobox', { name: 'Repo path' }), { target: { value: path } });
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Seed files' })));
}

test('launcherPaths: favorites then recent, each once, no blanks', () => {
  expect(launcherPaths({ favorites: [{ path: '/a' }, null, {}], recent: [{ path: '/b' }, { path: '/a' }] })).toEqual(['/a', '/b']);
  expect(launcherPaths(undefined)).toEqual([]);
});

test('the dialog opens with the launcher dirs as suggestions and focus in the path', async () => {
  render(<InitRepo />);
  expect(screen.queryByRole('dialog')).toBeNull();
  act(() => openInitRepo({ favorites: [{ path: '/a' }], recent: [{ path: '/b' }] }));
  expect(screen.getByRole('dialog', { name: 'Init a repo for the dispatcher' })).not.toBeNull();
  const input = screen.getByRole('combobox', { name: 'Repo path' });
  expect(input.getAttribute('list')).toBe('init-suggestions');
  expect([...document.querySelectorAll('#init-suggestions option')].map((o) => o.getAttribute('value'))).toEqual(['/a', '/b']);
  expect(document.activeElement).toBe(input);
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('an empty path never POSTs', async () => {
  await seed('   ', INIT);
  expect(calls).toEqual([]);
  expect([result().className, result().textContent]).toEqual(['init-result err', 'Enter a repo path.']);
});

test('a seeded git repo reports what was created and refreshes Work; no git warning', async () => {
  await seed(' /srv/repo ', { ...INIT, skipped: ['TODO.md'], created: ['WORKFLOW.md'] });
  expect(calls.map((c) => [c.url, c.init?.method, c.init?.body])).toEqual([['/api/dispatcher/init', 'POST', JSON.stringify({ path: '/srv/repo' })]]);
  expect(result().className).toBe('init-result ok');
  expect(lines()).toEqual(['created+icon: Created WORKFLOW.md', 'skipped+icon: Skipped (already exist): TODO.md',
    'next: Next: add /srv/repo/WORKFLOW.md to CHELA_DISPATCH_WORKFLOWS and restart the daemon, or run:  chela dispatch /srv/repo/WORKFLOW.md']);
  expect(polls).toHaveBeenCalledTimes(1);
});

test('seeding a path that is not a git repo warns that the dispatcher needs one', async () => {
  await seed('/srv/repo', { ...INIT, is_git: false });
  expect(lines().slice(0, 2)).toEqual(['created+icon: Created WORKFLOW.md + TODO.md', 'warn+icon: Not a git repo. The dispatcher needs one to branch & open PRs.']);
});

test('a refused or failed seed shows the error and does not refresh Work', async () => {
  await seed('/srv/repo', { ok: false, error: 'not a directory' });
  expect([result().className, result().textContent]).toEqual(['init-result err', 'not a directory']);
  cleanup();
  await seed('/srv/repo', null);
  expect(result().textContent).toBe('Failed.');
  cleanup();
  reply = () => Promise.reject(new TypeError('down'));
  render(<InitRepo />);
  act(() => openInitRepo(null));
  fireEvent.change(screen.getByRole('combobox', { name: 'Repo path' }), { target: { value: '/x' } });
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Seed files' })));
  expect([result().className, result().textContent]).toEqual(['init-result err', 'Request failed.']);
  expect(polls).not.toHaveBeenCalled();
});
