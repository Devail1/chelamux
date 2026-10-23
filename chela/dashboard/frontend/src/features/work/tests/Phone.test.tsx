// CMX-315: the phone Work view (Liav's approved mockups, 10-23): ONE title bar (☰, the project
// as the title, +, ⋯), the project switcher sheet, the Work options sheet (layout, Init a repo,
// Pause dispatch, Pause all agents with its confirm), Rows by default, and the compact card
// with everything destructive in its ⋮. The desktop toolbar is WorkView.test.tsx's.
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { installBridge } from '../../../shared/bridge/index.ts';
import { launcher } from '../../../shared/store/launcher.ts';
import { view } from '../../../shared/store/view.ts';
import { work, type Run, type WorkPayload, type Workflow } from '../../../shared/store/work.ts';
import { NewTask } from '../../newtask/index.ts';
import { TaskModal, closeTaskModal } from '../../taskmodal/index.ts';
import { WorkView, pollWork, toasts } from '../index.ts';
import { must, stubFetch, viewport } from './setup.ts';

installBridge(window);

let dispatcher: WorkPayload | null = null;
let calls: ReturnType<typeof stubFetch> = [];
let reply: (url: string) => { status?: number; body?: unknown } = () => ({ body: { ok: true } });

beforeEach(() => {
  viewport.phone = true;
  reply = () => ({ body: { ok: true } });
  calls = stubFetch((url) => (url === '/api/dispatcher' ? { body: dispatcher } : url === '/api/schedules' || url === '/api/cron' ? { body: [] } : reply(url)));
  act(() => view.set('work'));
});
afterEach(() => {
  act(() => {
    closeTaskModal();
    work.set(null);
    toasts.set([]);
  });
  document.body.className = '';
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
  render(<><WorkView /><NewTask /><TaskModal /></>);
  if (data) await act(async () => pollWork());
}

const bar = () => must(document.querySelector<HTMLElement>('header.work-bar'), 'the work bar');
const title = () => [bar().querySelector('.work-bar-project')?.textContent, bar().querySelector('.work-bar-sub')?.textContent];
const posts = () => calls.filter((c) => c.method === 'POST');
const dialog = (name: string) => screen.getByRole('dialog', { name });

// --- the one title bar ------------------------------------------------------------------------

test('one top bar: ☰, the project with a chevron over "Work · N running · M queued", + and ⋯; no toolbar, no pills, no coloured buttons', async () => {
  await mount(TWO);
  expect(document.querySelectorAll('header.work-bar').length).toBe(1);
  const buttons = [...bar().querySelectorAll('button')].map((b) => b.getAttribute('aria-label'));
  expect(buttons).toEqual(['Open menu', 'Switch project, current All projects', 'New task', 'Work options']);
  expect(title()).toEqual(['All projects', 'Work · 2 running · 3 queued']);
  expect(bar().querySelector('.work-bar-name svg')).not.toBeNull();
  for (const sel of ['.work-toolbar', '#new-task-btn', '#dispatch-hold-btn', '#fleet-pause-btn', '.kanban-filter-chip', '.kanban-layout-btn', '.kanban-mobile-controls']) {
    expect([sel, document.querySelectorAll(sel).length]).toEqual([sel, 0]);
  }
  expect(document.querySelectorAll('#panel-work .btn, .work-bar .btn, .work-phone-seg .btn').length).toBe(0);
  // Board | Runs | Schedules sits right under it.
  expect([...document.querySelectorAll('.work-phone-seg .work-seg-btn')].map((b) => b.textContent)).toEqual(['Board', 'Runs', 'Schedules']);
});

test('before the first poll the subtitle is just "Work"; a held queue or a paused fleet says so', async () => {
  render(<WorkView />);
  expect(title()).toEqual(['All projects', 'Work']);
  dispatcher = { ...TWO, dispatch_hold: { summary: 'held' } };
  await act(async () => pollWork());
  expect(title()[1]).toBe('Work · 2 running · 3 queued · dispatch paused');
  dispatcher = { ...TWO, dispatch_hold: { summary: 'held' }, fleet_pause: { by: 'liav' } };
  await act(async () => pollWork());
  expect(title()[1]).toBe('Work · 2 running · 3 queued · agents paused');
});

test('the ☰ opens the sidebar drawer', async () => {
  const sb = document.createElement('aside');
  sb.className = 'sidebar';
  document.body.append(sb);
  await mount(TWO);
  fireEvent.click(screen.getByRole('button', { name: 'Open menu' }));
  expect([sb.classList.contains('open'), document.body.classList.contains('sidebar-open')]).toEqual([true, true]);
  expect(screen.getByRole('button', { name: 'Open menu' }).getAttribute('aria-expanded')).toBe('true');
  sb.remove();
});

test('+ opens New task for a Linear workflow; without one it is disabled and says why', async () => {
  await mount(TWO);
  fireEvent.click(screen.getByRole('button', { name: 'New task' }));
  expect(screen.getByRole('dialog').querySelector('h3')?.textContent).toBe('New task');
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
  act(() => document.body.replaceChildren());
  await mount({ configured: true, workflows: [wf('/m/WORKFLOW.md', { tracker_kind: 'markdown' })] });
  const plus = screen.getByRole('button', { name: 'New task (needs a Linear tracker)' }) as HTMLButtonElement;
  expect(plus.disabled).toBe(true);
});

// --- the project switcher --------------------------------------------------------------------

const radios = () => within(dialog('Project')).getAllByRole('radio').map((r) => [r.closest('label')?.querySelector('.wproj-name')?.textContent, r.getAttribute('aria-checked')]);
const counts = () => [...dialog('Project').querySelectorAll('.wproj-row')].map((r) => [r.querySelector('.wproj-counts')?.textContent, r.querySelector('.wproj-needs')?.textContent ?? null]);

test('the title opens the switcher: All projects first, one row per project with its counts and an orange "N needs you"', async () => {
  await mount(TWO);
  fireEvent.click(bar().querySelector('.work-bar-title') as HTMLElement);
  expect(radios()).toEqual([['All projects', 'true'], ['chelamux', 'false'], ['lean-alpha', 'false']]);
  expect(counts()).toEqual([['2 running · 3 queued', null], ['2 running · 1 queued', null], ['0 running · 2 queued', '1 needs you']]);
  expect(within(dialog('Project')).queryByRole('searchbox')).toBeNull();
});

test('picking a project: single-select, it becomes the title, the board and the counts follow, and it moves to the top next time', async () => {
  await mount(TWO);
  fireEvent.click(bar().querySelector('.work-bar-title') as HTMLElement);
  fireEvent.click(within(dialog('Project')).getAllByRole('radio')[2] as HTMLElement);
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(title()).toEqual(['lean-alpha', 'Work · 0 running · 2 queued']);
  expect(JSON.parse(localStorage.getItem('chela_kanban_projects') ?? '')).toEqual([LEAN]);
  expect(JSON.parse(localStorage.getItem('chela_work_recent_projects') ?? '')).toEqual([LEAN]);
  expect([...document.querySelectorAll('.wrow')].map((r) => r.getAttribute('data-task-id'))).toEqual(['LAP-9', 'LAP-1', 'LAP-2']);
  fireEvent.click(bar().querySelector('.work-bar-title') as HTMLElement);
  expect(radios()).toEqual([['All projects', 'false'], ['lean-alpha', 'true'], ['chelamux', 'false']]);
  fireEvent.click(within(dialog('Project')).getAllByRole('radio')[0] as HTMLElement);
  expect(title()[0]).toBe('All projects');
  expect(localStorage.getItem('chela_kanban_projects')).toBe('[]');
  expect(JSON.parse(localStorage.getItem('chela_work_recent_projects') ?? '')).toEqual([LEAN]);
});

test('a tap on the row already picked closes the switcher; any close clears the search', async () => {
  const many: WorkPayload = { configured: true, workflows: Array.from({ length: 7 }, (_, i) => wf(`/p/proj-${i}/WORKFLOW.md`)) };
  await mount(many);
  fireEvent.click(bar().querySelector('.work-bar-title') as HTMLElement);
  fireEvent.click(within(dialog('Project')).getAllByRole('radio')[0] as HTMLElement);
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(localStorage.getItem('chela_kanban_projects')).toBeNull();
  fireEvent.click(bar().querySelector('.work-bar-title') as HTMLElement);
  fireEvent.change(within(dialog('Project')).getByRole('searchbox'), { target: { value: 'proj-3' } });
  expect(radios()).toHaveLength(2);
  fireEvent.click(must(dialog('Project').querySelector('.wproj-search')));
  expect(screen.queryByRole('dialog')).not.toBeNull();
  fireEvent.keyDown(dialog('Project'), { key: 'Escape' });
  fireEvent.click(bar().querySelector('.work-bar-title') as HTMLElement);
  expect((within(dialog('Project')).getByRole('searchbox') as HTMLInputElement).value).toBe('');
  expect(radios()).toHaveLength(8);
});

test('a desktop multi-select shows as "N projects" with no row checked; a vanished project drops out of the title', async () => {
  localStorage.setItem('chela_kanban_projects', JSON.stringify([CHELA, LEAN]));
  await mount(TWO);
  expect(title()[0]).toBe('2 projects');
  fireEvent.click(bar().querySelector('.work-bar-title') as HTMLElement);
  expect(radios().map((r) => r[1])).toEqual(['false', 'false', 'false']);
  fireEvent.keyDown(dialog('Project'), { key: 'Escape' });
  act(() => document.body.replaceChildren());
  localStorage.setItem('chela_kanban_projects', JSON.stringify(['/gone/WORKFLOW.md', CHELA]));
  await mount(TWO);
  expect(title()).toEqual(['chelamux', 'Work · 2 running · 1 queued']);
});

test('more than 6 projects: a search field filters them by name (All projects stays); 6 have none', async () => {
  const seven = (n: number): WorkPayload => ({ configured: true, workflows: Array.from({ length: n }, (_, i) => wf(`/p/proj-${i}${i === 3 ? '-alpha' : ''}/WORKFLOW.md`)) });
  await mount(seven(6));
  fireEvent.click(bar().querySelector('.work-bar-title') as HTMLElement);
  expect(within(dialog('Project')).queryByRole('searchbox')).toBeNull();
  fireEvent.keyDown(dialog('Project'), { key: 'Escape' });
  act(() => document.body.replaceChildren());
  await mount(seven(7));
  fireEvent.click(bar().querySelector('.work-bar-title') as HTMLElement);
  const search = within(dialog('Project')).getByRole('searchbox', { name: 'Search projects' });
  expect(radios()).toHaveLength(8);
  fireEvent.change(search, { target: { value: 'ALPHA' } });
  expect(radios().map((r) => r[0])).toEqual(['All projects', 'proj-3-alpha']);
  fireEvent.change(search, { target: { value: 'zzz' } });
  expect(radios().map((r) => r[0])).toEqual(['All projects']);
  expect(dialog('Project').querySelector('.wproj-none')?.textContent).toBe('No project matches.');
});

// --- the ⋯ sheet ---------------------------------------------------------------------------------

function options() {
  fireEvent.click(screen.getByRole('button', { name: 'Work options' }));
  return dialog('Work options');
}

test('Rows is the phone default; ⋯ holds the layout and the three actions, and Board persists', async () => {
  await mount(TWO);
  expect(must(document.getElementById('work-board')).dataset.layout).toBe('rows');
  const sheet = options();
  expect([...sheet.querySelectorAll('.wopt-layout-btn')].map((b) => [b.textContent, b.getAttribute('aria-pressed')])).toEqual([['Rows', 'true'], ['Board', 'false']]);
  expect([...sheet.querySelectorAll('.wopt-item .wopt-label')].map((l) => l.textContent)).toEqual(['Init a repo', 'Pause dispatch', 'Pause all agents']);
  expect([...sheet.querySelectorAll('.wopt-hint')].map((l) => l.textContent)).toEqual(['no new tasks', 'asks to confirm']);
  expect(must(sheet.querySelector('#wopt-pause-all')).className).toBe('popover-item wopt-item wopt-warn');
  expect(must(sheet.querySelector('#wopt-pause-all svg')).getAttribute('class')).toContain('lucide-triangle-alert');
  fireEvent.click(within(sheet).getByRole('button', { name: 'Board' }));
  expect(must(document.getElementById('work-board')).dataset.layout).toBe('board');
  expect(localStorage.getItem('chela_work_phone_layout')).toBe('board');
  expect(document.querySelectorAll('.wcard').length).toBeGreaterThan(0);
  fireEvent.click(within(sheet).getByRole('button', { name: 'Rows' }));
  expect(localStorage.getItem('chela_work_phone_layout')).toBe('rows');
});

test('a saved Board layout is where a reload lands; junk is Rows', async () => {
  localStorage.setItem('chela_work_phone_layout', 'board');
  await mount(TWO);
  expect(must(document.getElementById('work-board')).dataset.layout).toBe('board');
  act(() => document.body.replaceChildren());
  localStorage.setItem('chela_work_phone_layout', 'swipe');
  await mount(TWO);
  expect(must(document.getElementById('work-board')).dataset.layout).toBe('rows');
});

test('⋯ Init a repo closes the sheet and opens the dialog with the launcher\'s dirs', async () => {
  act(() => launcher.set({ favorites: [{ path: '/fav/repo' }], recent: [] }));
  await mount(TWO);
  fireEvent.click(within(options()).getByRole('button', { name: 'Init a repo' }));
  expect(screen.queryByRole('dialog', { name: 'Work options' })).toBeNull();
  expect([...screen.getByRole('dialog').querySelectorAll('datalist option')].map((o) => o.getAttribute('value'))).toEqual(['/fav/repo']);
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('⋯ Pause dispatch pauses without a prompt; held, it reads Resume dispatch with the reason and resumes', async () => {
  const ask = vi.spyOn(window, 'confirm');
  await mount(TWO);
  fireEvent.click(within(options()).getByRole('button', { name: /Pause dispatch/ }));
  await act(async () => {});
  expect(posts().map((p) => [p.url, p.body])).toEqual([['/api/dispatcher/pause', undefined]]);
  expect(ask).not.toHaveBeenCalled();
  dispatcher = { ...TWO, dispatch_hold: { summary: 'held for a merge' } };
  await act(async () => pollWork());
  const sheet = options();
  expect(must(sheet.querySelector('#wopt-hold')).textContent).toBe(' Resume dispatchheld for a merge');
  fireEvent.click(must(sheet.querySelector('#wopt-hold')));
  await act(async () => {});
  expect(posts().map((p) => p.url)).toEqual(['/api/dispatcher/pause', '/api/dispatcher/resume']);
});

test('⋯ with the fleet paused: one Resume agents and dispatch, no Pause all agents; before a poll neither shows', async () => {
  render(<WorkView />);
  let sheet = options();
  expect([...sheet.querySelectorAll('.wopt-label')].map((l) => l.textContent)).toEqual(['Init a repo']);
  fireEvent.keyDown(sheet, { key: 'Escape' });
  dispatcher = { ...TWO, fleet_pause: { by: 'liav' } };
  await act(async () => pollWork());
  sheet = options();
  expect([...sheet.querySelectorAll('.wopt-label')].map((l) => l.textContent)).toEqual(['Init a repo', 'Resume agents and dispatch']);
  expect(must(sheet.querySelector('#wopt-hold .wopt-hint')).textContent).toBe('agents are paused');
});

test('⋯ Pause all agents asks to confirm: Cancel posts nothing, confirming posts --pause --all', async () => {
  const ask = vi.spyOn(window, 'confirm');
  await mount(TWO);
  fireEvent.click(within(options()).getByRole('button', { name: /Pause all agents/ }));
  const confirm = dialog('Pause all agents?');
  expect(confirm.textContent).toContain('Pause every dispatched agent?');
  fireEvent.click(within(confirm).getByRole('button', { name: 'Cancel' }));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(posts()).toEqual([]);
  fireEvent.click(within(options()).getByRole('button', { name: /Pause all agents/ }));
  fireEvent.click(within(dialog('Pause all agents?')).getByRole('button', { name: 'Pause all agents' }));
  await act(async () => {});
  expect(posts().map((p) => [p.url, p.body])).toEqual([['/api/dispatcher/pause', { all: true }]]);
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(ask).not.toHaveBeenCalled();
});

test('a pause in flight disables the sheet\'s other pause until it lands', async () => {
  let release: (v: unknown) => void = () => {};
  await mount(TWO);
  vi.stubGlobal('fetch', (url: string) => (url === '/api/dispatcher' ? Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(TWO) }) : new Promise((r) => (release = r))));
  fireEvent.click(within(options()).getByRole('button', { name: /Pause all agents/ }));
  fireEvent.click(within(dialog('Pause all agents?')).getByRole('button', { name: 'Pause all agents' }));
  const hold = must(options().querySelector<HTMLButtonElement>('#wopt-hold'));
  expect(hold.disabled).toBe(true);
  await act(async () => release({ ok: true, status: 200, json: () => Promise.resolve({ ok: true }) }));
  expect(must(dialog('Work options').querySelector<HTMLButtonElement>('#wopt-hold')).disabled).toBe(false);
});

test('a failed pause says so and leaves the state to the next poll', async () => {
  const alert = vi.spyOn(window, 'alert').mockImplementation(() => {});
  reply = () => ({ body: { ok: false, error: 'already held' } });
  await mount(TWO);
  fireEvent.click(within(options()).getByRole('button', { name: /Pause dispatch/ }));
  await act(async () => {});
  expect(alert).toHaveBeenCalledWith('already held');
});

// --- the negative control -------------------------------------------------------------------------

test('wider than a phone: no title bar, the toolbar and the project pills as before', async () => {
  viewport.phone = false;
  await mount(TWO);
  expect(document.querySelector('.work-bar')).toBeNull();
  expect(document.querySelector('.work-toolbar #dispatch-hold-btn')).not.toBeNull();
  expect([...document.querySelectorAll('.kanban-filter-chip')].map((c) => c.textContent)).toEqual(['All (2)', 'chelamux', 'lean-alpha']);
  expect(document.querySelectorAll('.kanban-card').length).toBeGreaterThan(0);
  expect(document.querySelectorAll('.wcard, .wrow').length).toBe(0);
});
