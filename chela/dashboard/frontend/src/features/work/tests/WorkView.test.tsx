// The Work view shell, its one /api/dispatcher poll and its toasts (CMX-233; ported from
// tests/dispatch_hold.test.mjs, the toolbar half of tests/new_task.test.mjs and the run-toast
// half of tests/sse_owners.test.mjs, which drove legacy work.js).
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { installBridge } from '../../../shared/bridge/index.ts';
import { dispatch } from '../../../shared/events/index.ts';
import { refresh, registerLane } from '../../../shared/scheduler/index.ts';
import { launcher } from '../../../shared/store/launcher.ts';
import { view } from '../../../shared/store/view.ts';
import { refreshWork, work, type WorkPayload } from '../../../shared/store/work.ts';
import { NewTask } from '../../newtask/index.ts';
import { SEGMENTS, SEGMENT_KEY, WorkToasts, WorkView, pollWork, toasts } from '../index.ts';
import { must, stubFetch } from './setup.ts';

const bridge = installBridge(window);

let dispatcher: WorkPayload | null = { configured: true, workflows: [] };
let calls: ReturnType<typeof stubFetch> = [];
let reply: (url: string) => { status?: number; body?: unknown } | Promise<never> = () => ({ body: { ok: true } });

beforeEach(() => {
  dispatcher = { configured: true, workflows: [] };
  reply = () => ({ body: { ok: true } });
  calls = stubFetch((url) => (url === '/api/dispatcher' ? { body: dispatcher } : url === '/api/schedules' || url === '/api/cron' ? { body: [] } : reply(url)));
  act(() => view.set('work'));
});
afterEach(() => {
  act(() => {
    work.set(null);
    toasts.set([]);
  });
});

const posts = () => calls.filter((c) => c.method === 'POST');
const reads = (u: string) => calls.filter((c) => c.url === u).length;
const holdBtn = () => document.getElementById('dispatch-hold-btn') as HTMLButtonElement | null;
const hint = () => document.getElementById('dispatch-hold-hint')?.textContent;

async function mount(data: WorkPayload | null) {
  dispatcher = data;
  render(<><WorkView /><NewTask /></>);
  if (data) await act(async () => pollWork());
}

// --- segments ---------------------------------------------------------------------------------

test('Board | Runs | Schedules: Board by default; a pick shows its pane, presses its button and is saved', async () => {
  await mount({ configured: true, workflows: [] });
  const seg = (s: string) => must(document.querySelector<HTMLElement>(`#work-seg [data-seg="${s}"]`));
  const active = () => [...document.querySelectorAll<HTMLElement>('.work-pane.active')].map((p) => p.id);
  expect(SEGMENTS.map((s) => s.label)).toEqual(['Board', 'Runs', 'Schedules']);
  expect(active()).toEqual(['work-board']);
  expect(seg('board').getAttribute('aria-pressed')).toBe('true');
  fireEvent.click(seg('runs'));
  expect(active()).toEqual(['work-runs']);
  expect([seg('board'), seg('runs')].map((b) => [b.getAttribute('aria-pressed'), b.className])).toEqual([['false', 'work-seg-btn'], ['true', 'work-seg-btn active']]);
  expect(SEGMENT_KEY).toBe('chela_work_segment');
  expect(localStorage.getItem('chela_work_segment')).toBe('runs');
});

test('the saved segment is where a reload lands; junk or unreadable storage is Board', async () => {
  localStorage.setItem('chela_work_segment', 'schedules');
  await mount({ configured: true, workflows: [] });
  expect(document.querySelector('.work-pane.active')?.id).toBe('work-schedules');
  act(() => document.body.replaceChildren());
  for (const prime of [() => localStorage.setItem('chela_work_segment', 'kanban'), () => vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied'); })]) {
    prime();
    const r = render(<WorkView />);
    expect(document.querySelector('.work-pane.active')?.id).toBe('work-board');
    r.unmount();
  }
});

test('a pick with storage refusing still switches the pane', async () => {
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('quota');
  });
  await mount({ configured: true, workflows: [] });
  fireEvent.click(screen.getByRole('button', { name: 'Runs' }));
  expect(document.querySelector('.work-pane.active')?.id).toBe('work-runs');
});

test('Schedules reads its own endpoint only while its segment shows on the Work view, and again on each poll', async () => {
  await mount({ configured: true, workflows: [] });
  // The Schedules island refetches on mount once a refresh has ever been asked (an earlier test).
  const base = reads('/api/schedules');
  await act(async () => pollWork());
  expect(reads('/api/schedules')).toBe(base);
  fireEvent.click(screen.getByRole('button', { name: 'Schedules' }));
  await act(async () => {});
  expect(reads('/api/schedules')).toBe(base + 1);
  dispatcher = { configured: true, workflows: [] }; // every poll answers a new payload
  await act(async () => pollWork());
  expect(reads('/api/schedules')).toBe(base + 2);
  act(() => view.set('terminals'));
  await act(async () => pollWork());
  expect(reads('/api/schedules')).toBe(base + 2);
});

// --- the toolbar --------------------------------------------------------------------------

test('"+ New task" shows only when a workflow\'s tracker is Linear, and opens the New-task dialog', async () => {
  await mount({ configured: true, workflows: [{ path: '/m', tracker_kind: 'markdown' }] });
  expect(document.getElementById('new-task-btn')).toBeNull();
  dispatcher = { configured: true, workflows: [{ path: '/m', tracker_kind: 'markdown' }, { path: '/l', tracker_kind: 'linear' }] };
  await act(async () => pollWork());
  fireEvent.click(must(document.getElementById('new-task-btn')));
  expect(screen.getByRole('dialog').querySelector('h3')?.textContent).toBe('New task');
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('"+ Init a repo" opens the dialog suggesting the launcher\'s dirs', async () => {
  act(() => launcher.set({ favorites: [{ path: '/fav/repo' }], recent: [{ path: '/recent/repo' }, null] }));
  await mount({ configured: true, workflows: [] });
  fireEvent.click(screen.getByRole('button', { name: '+ Init a repo' }));
  expect([...screen.getByRole('dialog').querySelectorAll('datalist option')].map((o) => o.getAttribute('value'))).toEqual(['/fav/repo', '/recent/repo']);
});

// --- Pause / Resume dispatch (CMX-206) --------------------------------------------------------------

test('no Pause button before the first poll: it never flashes the wrong state', () => {
  render(<WorkView />);
  expect(holdBtn()).toBeNull();
});

test('a held queue shows Resume dispatch (warn look) and why; an unheld one Pause with no hint', async () => {
  await mount({ configured: true, workflows: [], dispatch_hold: { summary: 'held for a batch merge' } });
  expect([holdBtn()?.textContent, holdBtn()?.className, hint()]).toEqual(['Resume dispatch', 'btn btn-md btn-warn', 'held for a batch merge']);
  dispatcher = { configured: true, workflows: [], dispatch_hold: {} };
  await act(async () => pollWork());
  expect(hint()).toBe('Dispatch is paused.');
  dispatcher = { configured: true, workflows: [], dispatch_hold: null };
  await act(async () => pollWork());
  expect([holdBtn()?.textContent, holdBtn()?.className, hint()]).toEqual(['Pause dispatch', 'btn btn-primary btn-md', '']);
});

test('declining the confirm aborts a pause before any POST', async () => {
  const ask = vi.spyOn(window, 'confirm').mockReturnValue(false);
  await mount({ configured: true, workflows: [] });
  await act(async () => fireEvent.click(must(holdBtn())));
  expect(ask).toHaveBeenCalledWith('Pause dispatch? No new task will be claimed until you resume.');
  expect(posts()).toEqual([]);
});

test('confirming pauses: POSTs /api/dispatcher/pause, disabled while in flight, then redraws from a fresh poll', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  const alert = vi.spyOn(window, 'alert').mockImplementation(() => {});
  let resolve: (v: { body: unknown }) => void = () => {};
  reply = () => new Promise((r) => (resolve = r)) as never;
  await mount({ configured: true, workflows: [] });
  const before = reads('/api/dispatcher');
  fireEvent.click(must(holdBtn()));
  expect(holdBtn()?.disabled).toBe(true);
  dispatcher = { configured: true, workflows: [], dispatch_hold: { summary: 'paused' } };
  await act(async () => resolve({ body: { ok: true } }));
  expect(posts()).toEqual([{ url: '/api/dispatcher/pause', method: 'POST', body: undefined }]);
  expect(reads('/api/dispatcher')).toBe(before + 1);
  expect([holdBtn()?.textContent, holdBtn()?.disabled]).toEqual(['Resume dispatch', false]);
  expect(alert).not.toHaveBeenCalled();
});

test('resuming does not ask and POSTs /api/dispatcher/resume', async () => {
  const ask = vi.spyOn(window, 'confirm');
  await mount({ configured: true, workflows: [], dispatch_hold: { summary: 'x' } });
  await act(async () => fireEvent.click(must(holdBtn())));
  expect(ask).not.toHaveBeenCalled();
  expect(posts().map((p) => p.url)).toEqual(['/api/dispatcher/resume']);
});

test('a refusal alerts its reason (or a fallback); a throw alerts too; the button re-enables every time', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  const alert = vi.spyOn(window, 'alert').mockImplementation(() => {});
  await mount({ configured: true, workflows: [] });
  for (const [r, want] of [
    [() => ({ body: { ok: false, error: '"x" is not a duration' } }), '"x" is not a duration'],
    [() => ({ body: { ok: false } }), 'That failed. Dispatch state is unchanged.'],
    [() => ({ body: null }), 'That failed. Dispatch state is unchanged.'],
    [() => Promise.reject(new TypeError('offline')), 'Request failed. Dispatch state is unchanged.'],
  ] as const) {
    reply = r;
    alert.mockClear();
    await act(async () => fireEvent.click(must(holdBtn())));
    expect(alert).toHaveBeenCalledWith(want);
    expect(holdBtn()?.disabled).toBe(false);
  }
});

// --- Pause all agents / the paused fleet (CMX-252) ----------------------------------------------

const fleetBtn = () => document.getElementById('fleet-pause-btn') as HTMLButtonElement | null;
const panel = () => document.getElementById('fleet-pause');
const FLEET = {
  since_ago: '3m', by: 'dashboard', expires_in: '27m',
  agents: [{ task: 'CMX-1', wid: '@5', state: 'interrupted' }, { task: 'CMX-2', wid: '@6', state: 'finishing', finishing: 'judge sweep' }],
  judges: [{ task: 'CMX-3', frozen_for: '2m', processes: 4 }],
};

test('confirming "Pause all agents" POSTs pause with all:true; declining POSTs nothing', async () => {
  const ask = vi.spyOn(window, 'confirm').mockReturnValue(false);
  await mount({ configured: true, workflows: [] });
  await act(async () => fireEvent.click(must(fleetBtn())));
  expect(ask).toHaveBeenCalledWith('Pause every dispatched agent? Dispatch is held, each agent is told to start nothing until you resume, and running judges are frozen.');
  expect(posts()).toEqual([]);
  ask.mockReturnValue(true);
  await act(async () => fireEvent.click(must(fleetBtn())));
  expect(posts()).toEqual([{ url: '/api/dispatcher/pause', method: 'POST', body: { all: true } }]);
});

test('a paused fleet: no "Pause all agents", Resume names the agents, and the panel lists agents, judges, since and expiry', async () => {
  await mount({ configured: true, workflows: [], dispatch_hold: { summary: 'held' }, fleet_pause: FLEET });
  expect(fleetBtn()).toBeNull();
  expect(holdBtn()?.textContent).toBe('Resume agents and dispatch');
  expect(panel()?.querySelector('.fleet-pause-head')?.textContent).toBe('Agents paused 3m ago by dashboard. The hold expires in 27m; the agents resume then.');
  expect([...must(panel()).querySelectorAll('li')].map((li) => li.textContent)).toEqual([
    'agent CMX-1 in @5: interrupted', 'agent CMX-2 in @6: finishing (judge sweep)', 'judge CMX-3: frozen 2m (4 process(es))',
  ]);
  await act(async () => fireEvent.click(must(holdBtn())));
  expect(posts().map((p) => p.url)).toEqual(['/api/dispatcher/resume']);
});

test('a fleet pause with no hold left and nothing running says so; no fleet pause draws no panel', async () => {
  await mount({ configured: true, workflows: [], dispatch_hold: null, fleet_pause: { by: '', agents: [], judges: [] } });
  expect(panel()?.querySelector('.fleet-pause-head')?.textContent).toBe('Agents paused by ?. The hold is gone; the next dispatch tick resumes the agents.');
  expect(panel()?.querySelector('.fleet-pause-empty')?.textContent).toBe('No agent was running.');
  // the hold is gone but the agents are still paused: Resume, without a confirm
  const ask = vi.spyOn(window, 'confirm');
  expect([holdBtn()?.textContent, fleetBtn()]).toEqual(['Resume agents and dispatch', null]);
  await act(async () => fireEvent.click(must(holdBtn())));
  expect(ask).not.toHaveBeenCalled();
  expect(posts().map((p) => p.url)).toEqual(['/api/dispatcher/resume']);
  dispatcher = { configured: true, workflows: [], dispatch_hold: { summary: 'held' }, fleet_pause: null };
  await act(async () => pollWork());
  expect(panel()).toBeNull();
  expect([holdBtn()?.textContent, fleetBtn()?.textContent]).toEqual(['Resume dispatch', 'Pause all agents']);
});

// --- the poll -----------------------------------------------------------------------------------

test('the poll publishes the payload and fills the sidebar badges; a failed read keeps the last one', async () => {
  dispatcher = { configured: true, workflows: [{ path: '/a', active_runs: [{}, {}], awaiting_review_runs: [{}] }, { path: '/b', active_runs: [{}] }] };
  await pollWork();
  expect(work.get()).toBe(dispatcher);
  expect(bridge.nav.get()?.badges).toMatchObject({ 'side-runs-count': '3', 'side-pr-count': '1 PR' });
  const err = vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.stubGlobal('fetch', () => Promise.reject(new Error('down')));
  await pollWork();
  expect(work.get()).toBe(dispatcher);
  expect(err).toHaveBeenCalledWith('pollWork', expect.any(Error));
  expect(bridge.nav.get()?.badges['side-runs-count']).toBe('3');
});

test('it is a scheduler lane, and refreshWork runs it', async () => {
  registerLane('test-pane-list', (t) => t.paneListKnown()); // the sidebar's job on the page
  // Detached: a hung /api/dispatcher read never holds the tick (its settled lanes) up.
  let release: (v: { body: unknown }) => void = () => {};
  const hung = stubFetch((url) => (url === '/api/dispatcher' ? (new Promise((r) => (release = r)) as never) : { body: {} }));
  await refresh();
  expect(hung.filter((c) => c.url === '/api/dispatcher')).toHaveLength(1);
  await act(async () => release({ body: { configured: true, workflows: [] } }));
  calls = stubFetch((url) => (url === '/api/dispatcher' ? { body: dispatcher } : {}));
  await refresh();
  await act(async () => {});
  expect(reads('/api/dispatcher')).toBe(1);
  await refreshWork();
  expect(reads('/api/dispatcher')).toBe(2);
  expect(work.get()).toEqual({ configured: true, workflows: [] });
});

// --- run-state toasts (SSE) ---------------------------------------------------------------------

test('a run moving INTO review toasts once, with its label and PR link; each runs frame refetches once', async () => {
  render(<WorkToasts />);
  const run = { task_id: 'CMX-901', label: 'CMX-901 · port', status: 'awaiting_review', pr_url: 'https://example.test/pull/9' };
  await act(async () => {
    dispatch('runs', JSON.stringify({ runs: [{ ...run, status: 'running' }] }));
    dispatch('runs', JSON.stringify({ runs: [run, null, { status: 'done' }] }));
    dispatch('runs', JSON.stringify({ runs: [{ ...run, pr_state: 'MERGEABLE' }] }));
  });
  expect(reads('/api/dispatcher')).toBe(3);
  const shown = [...document.querySelectorAll('.run-toast-stack .run-toast')];
  expect(shown.map((t) => t.textContent)).toEqual(['CMX-901 · port → awaiting reviewPR']);
  expect(shown[0]?.querySelector('a')?.getAttribute('href')).toBe('https://example.test/pull/9');
  // The PR link opens without dismissing the toast; a click elsewhere dismisses it.
  await act(async () => fireEvent.click(must(shown[0]?.querySelector('a'))));
  expect(document.querySelectorAll('.run-toast')).toHaveLength(1);
  await act(async () => fireEvent.click(must(shown[0])));
  expect(document.querySelectorAll('.run-toast')).toHaveLength(0);
  expect(toasts.get()).toEqual([]);
});

test('the hello baseline primes: a run already in review never toasts, a later transition does (by task id, no link without a PR)', async () => {
  await act(async () => {
    dispatch('hello', JSON.stringify({ runs: [{ task_id: 'CMX-902', status: 'awaiting_review' }, null] }));
    dispatch('runs', JSON.stringify({ runs: [{ task_id: 'CMX-902', status: 'awaiting_review' }] }));
  });
  expect(toasts.get()).toEqual([]);
  await act(async () => dispatch('runs', JSON.stringify({ runs: [{ task_id: 'CMX-902', status: 'done' }] })));
  expect(toasts.get()).toEqual([{ id: expect.any(Number), title: 'CMX-902 → done' }]);
});

test('muted run toasts stay silent; storage that throws is not muted', async () => {
  localStorage.setItem('chela_mute_run_toasts', '1');
  await act(async () => {
    dispatch('runs', JSON.stringify({ runs: [{ task_id: 'CMX-903', status: 'running' }] }));
    dispatch('runs', JSON.stringify({ runs: [{ task_id: 'CMX-903', status: 'failed' }] }));
  });
  expect(toasts.get()).toEqual([]);
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    throw new Error('denied');
  });
  await act(async () => dispatch('runs', JSON.stringify({ runs: [{ task_id: 'CMX-903', status: 'needs_human' }] })));
  expect(toasts.get().map((t) => t.title)).toEqual(['CMX-903 → NEEDS A HUMAN: rework cap reached']);
});

test('a toast leaves on its own after 15 s', async () => {
  vi.useFakeTimers();
  try {
    render(<WorkToasts />);
    act(() => toasts.set([{ id: 1, title: 'a note' }]));
    expect(document.querySelector('.run-toast')?.textContent).toBe('a note');
    await act(async () => vi.advanceTimersByTime(14000));
    expect(toasts.get()).toHaveLength(1);
    await act(async () => vi.advanceTimersByTime(1100));
    expect(toasts.get()).toEqual([]);
  } finally {
    vi.useRealTimers();
  }
});
