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
// CMX-316: why the queue is held is the button's tooltip (the header's subtitle says "dispatch paused")
const hint = () => holdBtn()?.getAttribute('title');
/** Open the header's ⋯ and return the menu. */
function more() {
  const trigger = must(document.getElementById('work-more-btn'), 'the ⋯ button');
  if (trigger.getAttribute('aria-expanded') !== 'true') fireEvent.keyDown(trigger, { key: 'Enter' });
  return must(document.querySelector<HTMLElement>('.work-head-menu'), 'the open ⋯ menu');
}
const closeMore = () => {
  const m = document.querySelector('.work-head-menu');
  if (m) fireEvent.keyDown(m, { key: 'Escape' });
};
/** Pick a ⋯ row whose effect waits for the menu to close (a timer), and let it run. */
async function pickMore(sel: string) {
  const row = must(more().querySelector<HTMLElement>(sel), sel);
  await act(async () => {
    fireEvent.click(row);
    await new Promise((r) => setTimeout(r, 0));
  });
}

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

test('New task is the one primary button: disabled (and says why) without a Linear tracker; with one it opens the New-task dialog', async () => {
  await mount({ configured: true, workflows: [{ path: '/m', tracker_kind: 'markdown' }] });
  const btn = () => must(document.querySelector<HTMLButtonElement>('.work-head #new-task-btn'));
  expect([btn().className, btn().disabled, btn().title, btn().textContent]).toEqual(['btn btn-primary btn-md work-head-new', true, 'New task needs a workflow with a Linear tracker', 'New task']);
  expect(document.querySelectorAll('.work-head .btn-primary')).toHaveLength(1);
  dispatcher = { configured: true, workflows: [{ path: '/m', tracker_kind: 'markdown' }, { path: '/l', tracker_kind: 'linear' }] };
  await act(async () => pollWork());
  expect([btn().disabled, btn().title]).toEqual([false, '']);
  fireEvent.click(btn());
  expect(screen.getByRole('dialog').querySelector('h3')?.textContent).toBe('New task');
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('⋯ Init a repo opens the dialog suggesting the launcher\'s dirs', async () => {
  act(() => launcher.set({ favorites: [{ path: '/fav/repo' }], recent: [{ path: '/recent/repo' }, null] }));
  await mount({ configured: true, workflows: [] });
  const row = must([...more().querySelectorAll<HTMLElement>('[role="menuitem"]')].find((i) => i.textContent === 'Init a repo'));
  await act(async () => {
    fireEvent.click(row);
    await new Promise((r) => setTimeout(r, 0));
  });
  expect([...screen.getByRole('dialog').querySelectorAll('datalist option')].map((o) => o.getAttribute('value'))).toEqual(['/fav/repo', '/recent/repo']);
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
  expect(screen.queryByRole('dialog')).toBeNull();
});

// --- Pause / Resume dispatch (CMX-206) --------------------------------------------------------------

test('no Pause button before the first poll: it never flashes the wrong state', () => {
  render(<WorkView />);
  expect(holdBtn()).toBeNull();
  expect(document.querySelector('.work-head-sub')?.textContent).toBe('Work');
});

test('a held queue shows Resume dispatch (warn look) and why; an unheld one a quiet outline Pause', async () => {
  await mount({ configured: true, workflows: [], dispatch_hold: { summary: 'held for a batch merge' } });
  expect([holdBtn()?.textContent, holdBtn()?.className, hint()]).toEqual(['Resume dispatch', 'btn btn-md work-head-hold btn-warn', 'held for a batch merge']);
  expect(document.querySelector('.work-head-sub')?.textContent).toBe('0 running · 0 queued · dispatch paused');
  dispatcher = { configured: true, workflows: [], dispatch_hold: {} };
  await act(async () => pollWork());
  expect(hint()).toBe('Dispatch is paused.');
  dispatcher = { configured: true, workflows: [], dispatch_hold: null };
  await act(async () => pollWork());
  expect([holdBtn()?.textContent, holdBtn()?.className, hint()]).toEqual(['Pause dispatch', 'btn btn-md work-head-hold', 'No new task is claimed while paused.']);
  expect(document.querySelector('.work-head-sub')?.textContent).toBe('0 running · 0 queued · dispatch on');
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

const PAUSE_ALL = '[data-action="pause-all"]';
const panel = () => document.getElementById('fleet-pause');
const FLEET = {
  since_ago: '3m', by: 'dashboard', expires_in: '27m',
  agents: [{ task: 'CMX-1', wid: '@5', state: 'interrupted' }, { task: 'CMX-2', wid: '@6', state: 'finishing', finishing: 'judge sweep' }],
  judges: [{ task: 'CMX-3', frozen_for: '2m', processes: 4 }],
  services: [{ name: 'game-mirror', state: 'stopped' }, { name: 'game-agent', state: 'not running' }],
};

test('⋯ Pause all agents asks in a dialog first: Cancel posts nothing, confirming POSTs pause with all:true', async () => {
  const ask = vi.spyOn(window, 'confirm');
  await mount({ configured: true, workflows: [] });
  expect(must(more().querySelector(PAUSE_ALL)).textContent).toBe('Pause all agents');
  await pickMore(PAUSE_ALL);
  const dialog = screen.getByRole('dialog', { name: 'Pause all agents?' });
  expect(dialog.textContent).toContain('Pause every dispatched agent? Dispatch is held, each agent is told to start nothing until you resume, and running judges are frozen.');
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(posts()).toEqual([]);
  await pickMore(PAUSE_ALL);
  await act(async () => fireEvent.click(must(document.getElementById('wopt-pause-all-confirm'))));
  expect(posts()).toEqual([{ url: '/api/dispatcher/pause', method: 'POST', body: { all: true } }]);
  expect(ask).not.toHaveBeenCalled();
});

test('a paused fleet: no "Pause all agents", Resume names the agents, and the panel lists agents, judges, since and expiry', async () => {
  await mount({ configured: true, workflows: [], dispatch_hold: { summary: 'held' }, fleet_pause: FLEET });
  expect(more().querySelector(PAUSE_ALL)).toBeNull();
  closeMore();
  expect(holdBtn()?.textContent).toBe('Resume agents and dispatch');
  expect(document.querySelector('.work-head-sub')?.textContent).toBe('0 running · 0 queued · agents paused');
  expect(panel()?.querySelector('.fleet-pause-head')?.textContent).toBe('Agents paused 3m ago by dashboard. The hold expires in 27m; the agents resume then.');
  expect([...must(panel()).querySelectorAll('li')].map((li) => li.textContent)).toEqual([
    'agent CMX-1 in @5: interrupted', 'agent CMX-2 in @6: finishing (judge sweep)', 'judge CMX-3: frozen 2m (4 process(es))',
    'service game-mirror: stopped', 'service game-agent: not running',
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
  expect([holdBtn()?.textContent, hint()]).toEqual(['Resume agents and dispatch', 'Agents are paused.']);
  await act(async () => fireEvent.click(must(holdBtn())));
  expect(ask).not.toHaveBeenCalled();
  expect(posts().map((p) => p.url)).toEqual(['/api/dispatcher/resume']);
  dispatcher = { configured: true, workflows: [], dispatch_hold: { summary: 'held' }, fleet_pause: null };
  await act(async () => pollWork());
  expect(panel()).toBeNull();
  expect([holdBtn()?.textContent, more().querySelector(PAUSE_ALL)?.textContent]).toEqual(['Resume dispatch', 'Pause all agents']);
});

test('before the first poll ⋯ offers no Pause all agents', () => {
  render(<WorkView />);
  expect(more().querySelector(PAUSE_ALL)).toBeNull();
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
