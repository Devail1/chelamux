// The Decisions inbox: its button (dot + unread badge), its lanes, and the dialog (owner
// chip, re-register, search, click-through). Ported from tests/decisions.test.mjs,
// decisions_seed and decisions_sse (CMX-228); its look is tests/decisions_modal_css.test.mjs. Every test loads fresh
// modules: the inbox holds its cursor at module scope, as the page does.
import { act, fireEvent, render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, expect, test, vi } from 'vitest';
import { must } from './setup.ts';

type Mods = {
  d: typeof import('../index.ts');
  tm: typeof import('../../taskmodal/index.ts');
  events: typeof import('../../../shared/events/index.ts');
  scheduler: typeof import('../../../shared/scheduler/index.ts');
  orch: typeof import('../../../shared/store/orchestrator.ts');
  agents: typeof import('../../../shared/store/agents.ts');
};
let m: Mods;

type Res = unknown | (() => Promise<unknown>);
let routes: Record<string, Res>;
let calls: { url: string; init?: RequestInit }[];
const LOG = (events: object[], extra: object = {}) => ({ boot_id: 'b1', events, gap: null, first_seq: 1, last_seq: events.length, next_seq: events.length, ...extra });
const NOBODY = { wid: null, name: null, state: 'unregistered', why: '', queued: 0 };
const ev = (seq: number, over: object = {}) => ({ seq, ts: 1000 + seq, type: 'run_review', wid: '@3', summary: `decision ${seq}`, payload: {}, ...over });

// /api/log as event_log.read() answers it: only the events after the cursor.
function serveLog(url: string, r: unknown) {
  const after = Number(new URL(url, 'http://x').searchParams.get('after_seq') ?? 0);
  const b = r as { events: { seq: number }[] };
  return { ...b, events: b.events.filter((e) => e.seq > after) };
}

function route(url: string): Res | undefined {
  const path = url.split('?')[0] ?? url;
  return path in routes ? routes[path] : undefined;
}

beforeEach(async () => {
  localStorage.clear();
  routes = { '/api/log': LOG([]), '/api/orchestrator/status': NOBODY, '/api/dispatcher': { workflows: [] }, '/api/agents': [], '/api/agents/context': {} };
  calls = [];
  vi.stubGlobal('fetch', (url: string, init?: RequestInit) => {
    calls.push(init ? { url, init } : { url });
    const r = route(url);
    if (r === undefined) return Promise.reject(new Error(`unrouted ${url}`));
    const body = typeof r === 'function' ? (r as () => Promise<unknown>)() : Promise.resolve(url.startsWith('/api/log') ? serveLog(url, r) : r);
    return body.then((b) => ({ ok: true, json: () => Promise.resolve(b) }));
  });
  vi.resetModules();
  m = {
    d: await import('../index.ts'),
    tm: await import('../../taskmodal/index.ts'),
    events: await import('../../../shared/events/index.ts'),
    scheduler: await import('../../../shared/scheduler/index.ts'),
    orch: await import('../../../shared/store/orchestrator.ts'),
    agents: await import('../../../shared/store/agents.ts'),
  };
  m.agents.agents.set([]);
});

const flush = () => act(() => new Promise((r) => setTimeout(r, 0)));
const jump = vi.fn();
function mount(extra?: ReactNode) {
  jump.mockReset();
  render(
    <>
      <m.d.DecisionsButton jump={jump} />
      <m.d.DecisionsDialog />
      <m.tm.TaskModal />
      {extra}
    </>,
  );
}
/** Load the log (one drain) and the owner, as the first refresh() tick does. */
async function load(events: object[], status: object = NOBODY) {
  routes['/api/log'] = LOG(events);
  routes['/api/orchestrator/status'] = status;
  await act(() => m.d.tickDecisions());
}
async function openInbox() {
  await act(async () => {
    m.d.openDecisions();
  });
  await flush();
}
const btn = () => must(document.getElementById('btn-decisions'));
const badge = () => btn().querySelector('.decisions-unread');
const dot = () => must(btn().querySelector('.decisions-dot'));
const inbox = () => screen.queryByRole('dialog', { name: 'Decisions' });
const chip = () => must(document.querySelector('.decisions-chip'));
const rows = () => [...document.querySelectorAll<HTMLElement>('.decisions-list .feed-row')];
const search = () => screen.getByRole('textbox', { name: 'Search decisions' }) as HTMLInputElement;
const logCalls = () => calls.filter((c) => c.url.startsWith('/api/log'));
const reregBtn = () => screen.queryByRole('button', { name: /Re-register/ }) as HTMLButtonElement | null;
const picker = () => screen.queryByRole('combobox', { name: 'Session to re-register as orchestrator' });
const subscribes = () => calls.filter((c) => c.url === '/api/orchestrator/subscribe').map((c) => JSON.parse(String(c.init?.body)) as { wid: string });
async function type(value: string) {
  await act(async () => {
    fireEvent.change(search(), { target: { value } });
  });
}
async function pick(text: string) {
  const trigger = must(picker());
  await act(async () => {
    fireEvent.keyDown(trigger, { key: 'Enter' });
  });
  await act(async () => {
    fireEvent.click(screen.getByRole('option', { name: text }));
  });
}

// --- the button

test('the button: labelled, a dialog opener, and its label says when a click jumps', async () => {
  mount();
  expect(btn().getAttribute('aria-label')).toBe('Decisions inbox');
  expect(btn().getAttribute('aria-haspopup')).toBe('dialog');
  expect(btn().className).toBe('icon-btn');
  await load([], { wid: '@7', name: 'orch', state: 'ok', why: '', queued: 0 });
  expect(btn().getAttribute('aria-label')).toBe('Decisions inbox: jump to the orchestrator');
});

test.each([
  ['ok', 'dot-ok', 'live'],
  ['unregistered', 'dot-none', 'nobody (logged here)'],
  ['dangling', 'dot-bad', 'dangling'],
  ['gone', 'dot-bad', 'gone'],
  ['unstamped', 'dot-warn', 'unverified'],
  ['something-new', 'dot-none', 'nobody (logged here)'],
])('the dot mirrors the owner: %s', async (state, cls, word) => {
  mount();
  await load([], { ...NOBODY, state });
  expect(dot().className).toBe(`decisions-dot ${cls}`);
  expect(dot().getAttribute('title')).toBe(word);
});

test('no badge before the first batch lands, nor on a fresh browser: it seeds to the newest seq', async () => {
  mount();
  expect(badge()).toBeNull();
  await load([ev(1), ev(2), ev(3)]);
  expect(badge()).toBeNull();
  expect(localStorage.getItem('chela.decisions.lastSeen')).toBe('3');
  // and the seeded cursor holds: the next decision badges
  routes['/api/log'] = LOG([ev(4, { type: 'finished' })], { next_seq: 4, last_seq: 4 });
  await act(() => m.d.refreshLog());
  expect(badge()?.textContent).toBe('1');
});

test('a stored lastSeen survives a reload: unseen decisions badge, coloured by urgency', async () => {
  localStorage.setItem('chela.decisions.lastSeen', '1');
  vi.resetModules();
  m.d = await import('../index.ts');
  m.tm = await import('../../taskmodal/index.ts');
  m.orch = await import('../../../shared/store/orchestrator.ts');
  mount();
  await load([ev(1, { type: 'run_failed' }), ev(2, { type: 'finished' }), ev(3, { type: 'run_review' })]);
  expect(badge()?.textContent).toBe('2');
  expect(badge()?.className).toBe('decisions-unread decisions-unread-warn');
  routes['/api/log'] = LOG([ev(4, { type: 'died' })], { next_seq: 4, last_seq: 4 });
  await act(() => m.d.refreshLog());
  expect(badge()?.className).toBe('decisions-unread decisions-unread-bad');
});

test('neutral unseen decisions badge in the neutral colour', async () => {
  localStorage.setItem('chela.decisions.lastSeen', '0');
  vi.resetModules();
  m.d = await import('../index.ts');
  m.tm = await import('../../taskmodal/index.ts');
  mount();
  await load([ev(1, { type: 'completed_gone' })]);
  expect(badge()?.className).toBe('decisions-unread');
});

test('an unreadable stored lastSeen, or storage that throws, seeds as a fresh browser', async () => {
  localStorage.setItem('chela.decisions.lastSeen', 'not-a-number');
  vi.resetModules();
  m.d = await import('../index.ts');
  m.tm = await import('../../taskmodal/index.ts');
  mount();
  await load([ev(5)]);
  expect(badge()).toBeNull();
  expect(localStorage.getItem('chela.decisions.lastSeen')).toBe('5');
  const get = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    throw new Error('blocked');
  });
  const set = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('blocked');
  });
  vi.resetModules();
  const fresh = await import('../index.ts');
  expect(fresh.inbox.get().lastSeen).toBeNull();
  routes['/api/log'] = LOG([ev(9)]);
  await act(() => fresh.refreshLog());
  expect(fresh.inbox.get().lastSeen).toBe(9);
  get.mockRestore();
  set.mockRestore();
});

test('a click jumps to the orchestrator, opens the log, marks every held decision seen and ticks', async () => {
  localStorage.setItem('chela.decisions.lastSeen', '0');
  vi.resetModules();
  m.d = await import('../index.ts');
  m.tm = await import('../../taskmodal/index.ts');
  mount();
  await load([ev(1), ev(2)]);
  expect(badge()?.textContent).toBe('2');
  calls = [];
  await act(async () => {
    fireEvent.click(btn());
  });
  await flush();
  expect(jump).toHaveBeenCalledTimes(1);
  expect(inbox()).toBeTruthy();
  expect(badge()).toBeNull();
  expect(localStorage.getItem('chela.decisions.lastSeen')).toBe('2');
  expect(calls.map((c) => c.url.split('?')[0]).sort()).toEqual(['/api/log', '/api/orchestrator/status']);
  expect(document.activeElement).toBe(search());
});

// --- lanes (the boot's seed, the SSE delta)

test('the first refresh() tick seeds the log, scoped to decision kinds, with no dialog open', async () => {
  mount();
  routes['/api/log'] = LOG([ev(1)]);
  m.scheduler.registerLane('pane-list', (t) => t.paneListKnown());
  await act(() => m.scheduler.refresh());
  const url = new URL(must(logCalls()[0]).url, 'http://x');
  expect(url.searchParams.get('limit')).toBe('300');
  expect(url.searchParams.get('after_seq')).toBeNull();
  expect(url.searchParams.getAll('type')).toEqual(m.d.DECISION_TYPES);
  expect(m.d.inbox.get().events.map((e) => e.seq)).toEqual([1]);
  expect(m.d.inbox.get().loaded).toBe(true);
});

test('the decision kinds: every outcome chela/inbox.py queues, never the hook firehose', () => {
  const types = new Set(m.d.DECISION_TYPES);
  for (const t of ['run_review', 'run_needs_human', 'run_changes_requested', 'run_failed', 'run_judge_clean', 'run_judge_cannot_verify', 'run_judge_blocked_race', 'run_unjudged_merged', 'run_judge_not_started', 'run_reopened', 'run_was_closed', 'run_ci_red', 'run_ci_flake', 'run_pr_conflicting', 'run_stuck', 'run_marker_unread', 'run_selfcheck_long', 'dispatch_hold_expired', 'dispatch_hold_released', 'finished', 'blocked', 'died', 'gone_unknown', 'completed_gone', 'watch_epoch_lost', 'inbox_undeliverable', 'inbox_self_healed'])
    expect(types.has(t)).toBe(true);
  expect(m.d.DECISION_TYPES).toHaveLength(27);
  for (const t of ['hook.pre_tool_use', 'hook.post_tool_use', 'hook.user_prompt_submit']) expect(types.has(t)).toBe(false);
});

test('an SSE `log` frame reads on from the cursor, whatever view is on screen', async () => {
  mount();
  await load([ev(1), ev(2)]);
  const view = await import('../../../shared/store/view.ts');
  for (const v of ['terminals', 'work']) {
    view.view.set(v);
    calls = [];
    routes['/api/log'] = LOG([], { boot_id: 'b1', next_seq: 2, last_seq: 2 });
    m.events.dispatch('log', null);
    await flush();
    const url = new URL(must(logCalls()[0]).url, 'http://x');
    expect(url.searchParams.get('after_seq')).toBe('2');
    expect(url.searchParams.get('after_boot')).toBe('b1');
  }
});

test('one read at a time: a frame during a drain is dropped, the next one reads', async () => {
  let release = () => {};
  routes['/api/log'] = () =>
    new Promise((r) => {
      release = () => r(LOG([ev(1)]));
    });
  const first = m.d.refreshLog();
  await m.d.refreshLog();
  expect(logCalls()).toHaveLength(1);
  release();
  await first;
  routes['/api/log'] = LOG([]);
  await m.d.refreshLog();
  expect(logCalls()).toHaveLength(2);
});

test('the browser keeps the newest 500 decisions', async () => {
  routes['/api/log'] = LOG(Array.from({ length: 300 }, (_, i) => ev(i + 1)), { next_seq: 300, last_seq: 300 });
  await m.d.refreshLog();
  routes['/api/log'] = LOG(Array.from({ length: 300 }, (_, i) => ev(i + 301)), { next_seq: 600, last_seq: 600 });
  await m.d.refreshLog();
  const held = m.d.inbox.get().events.map((e) => e.seq);
  expect(held).toHaveLength(500);
  expect([held[0], held.at(-1)]).toEqual([101, 600]);
});

// --- the dialog

test('closed at rest; rows render newest first with NO owner registered (never blank on "nobody")', async () => {
  mount();
  expect(inbox()).toBeNull();
  await load([ev(1, { summary: 'cmx-9 awaiting review' }), ev(2, { wid: null, summary: '' })]);
  await openInbox();
  expect(must(inbox()).className).toBe('modal modal-decisions');
  expect(rows().map((r) => r.dataset.seq)).toEqual(['2', '1']);
  const [newest, older] = rows();
  expect(newest?.querySelector('.feed-wid')?.textContent).toBe('chela');
  expect(newest?.querySelector('.feed-summary')?.textContent).toBe('run_review');
  expect(older?.querySelector('.feed-summary')?.textContent).toBe('cmx-9 awaiting review');
  expect(older?.querySelector('.feed-glyph')?.textContent).toBe('● run');
  expect(older?.className).toBe('feed-row feed-cls-run');
  expect(chip().className).toBe('decisions-chip decisions-chip-none');
  expect(chip().textContent).toContain('nobody (this panel is home)');
});

test('an empty log says so', async () => {
  mount();
  await load([]);
  await openInbox();
  expect(must(document.querySelector('.decisions-list')).textContent).toBe('No decisions logged yet');
});

test('an owner is reflected in the chip: who, its window, and what is queued', async () => {
  mount();
  await load([], { wid: '@7', name: 'orchestrator', state: 'ok', why: '', queued: 2 });
  await openInbox();
  expect(chip().className).toBe('decisions-chip decisions-chip-ok');
  expect(chip().querySelector('.decisions-chip-who')?.textContent).toBe('orchestrator (@7)');
  expect(chip().querySelector('.decisions-chip-queued')?.textContent).toBe('2 queued');
  expect(chip().querySelector('.decisions-chip-glyph')?.textContent).toBe('● live');
  expect(chip().querySelector('.decisions-chip-why')).toBeNull();
});

test('a dangling/gone owner is a visibly BAD chip with a non-colour glyph, and says why', async () => {
  mount();
  await load([], { wid: '@7', name: '', state: 'gone', why: 'no claude running', queued: 0 });
  await openInbox();
  expect(chip().className).toBe('decisions-chip decisions-chip-bad');
  expect(chip().querySelector('.decisions-chip-glyph')?.textContent).toBe('× gone');
  expect(chip().querySelector('.decisions-chip-why')?.textContent).toBe('no claude running');
  expect(chip().querySelector('.decisions-chip-who')?.textContent).toBe('@7 (@7)');
  expect(chip().querySelector('.decisions-chip-queued')).toBeNull();
});

test('a gap is rendered, never swallowed, and stays on screen after the next read', async () => {
  mount();
  routes['/api/log'] = LOG([ev(9)], { gap: { reason: 'the log rotated past your cursor' } });
  await act(() => m.d.refreshLog());
  await openInbox();
  expect(must(document.querySelector('.feed-gap')).textContent?.trim()).toBe('the log rotated past your cursor');
  routes['/api/log'] = LOG([], { next_seq: 9, last_seq: 9 });
  await act(() => m.d.refreshLog());
  expect(document.querySelector('.feed-gap')).toBeTruthy();
});

test('a gap with no reason still says there is one', async () => {
  mount();
  routes['/api/log'] = LOG([], { gap: {} });
  await act(() => m.d.refreshLog());
  await openInbox();
  expect(must(document.querySelector('.feed-gap')).textContent?.trim()).toBe('a gap in the decisions log');
});

test('the search narrows the rendered list with no re-fetch, says what it searched, and clears back', async () => {
  mount();
  await load([ev(1, { payload: { branch_name: 'cmx-1' } }), ev(2, { payload: { branch_name: 'cmx-2' } }), ev(3, { payload: { branch_name: 'cmx-12' } })]);
  await openInbox();
  const before = logCalls().length;
  await type('cmx-1');
  expect(rows().map((r) => r.dataset.seq)).toEqual(['3', '1']);
  expect(must(document.querySelector('.decisions-scope')).textContent).toBe('2 of 3 loaded');
  await type('nope');
  expect(rows()).toHaveLength(0);
  expect(must(document.querySelector('.decisions-list')).textContent).toBe('No decisions match "nope"');
  await type('  ');
  expect(rows()).toHaveLength(3);
  expect(document.querySelector('.decisions-scope')).toBeNull();
  expect(logCalls()).toHaveLength(before);
  expect(m.d.inbox.get().events).toHaveLength(3);
});

test('the seen cursor ignores the filter (it is over what is HELD); the query survives a close and reopen', async () => {
  mount();
  await load([ev(1), ev(2)]);
  await openInbox();
  await type('decision 1');
  routes['/api/log'] = LOG([ev(3)], { next_seq: 3, last_seq: 3 });
  await act(() => m.d.refreshLog());
  expect(rows().map((r) => r.dataset.seq)).toEqual(['1']);
  expect(localStorage.getItem('chela.decisions.lastSeen')).toBe('3');
  act(() => m.d.closeDecisions());
  await openInbox();
  expect(search().value).toBe('decision 1');
});

test('Escape and the close button close the dialog', async () => {
  mount();
  await openInbox();
  fireEvent.keyDown(must(inbox()), { key: 'Escape' });
  expect(inbox()).toBeNull();
  await openInbox();
  fireEvent.click(screen.getByRole('button', { name: 'Close' }));
  expect(inbox()).toBeNull();
  expect(m.d.inboxOpen.get()).toBe(false);
});

// --- the re-register control (dangling/gone only)

const LIVE = (wid: string, name: string, claude = true) => ({ window_id: wid, name, claude_running: claude });
const DANGLING = { wid: '@1', name: 'liavedunix', state: 'dangling', why: 'tmux server restarted', queued: 1 };

test('a dangling chip with a live session offers a picker and a button, not a dismiss', async () => {
  m.agents.agents.set([LIVE('@5', 'liavedunix')]);
  mount();
  await load([], DANGLING);
  await openInbox();
  expect(must(picker()).textContent).toContain('liavedunix (@5)');
  expect(must(reregBtn()).disabled).toBe(false);
  expect(reregBtn()?.className).toBe('btn btn-danger btn-sm');
  expect(chip().textContent).toContain('dangling');
});

test.each(['ok', 'unregistered', 'unstamped'])('an %s chip never shows the re-register control, nor reads /api/agents', async (state) => {
  mount();
  await load([], { ...DANGLING, state });
  await openInbox();
  await flush();
  expect(picker()).toBeNull();
  expect(reregBtn()).toBeNull();
  expect(calls.some((c) => c.url === '/api/agents')).toBe(false);
});

test('the candidates: live Claude sessions only, sorted by name then window; the first is picked', async () => {
  m.agents.agents.set([LIVE('@9', 'zeta'), LIVE('@3', 'alpha'), LIVE('@2', 'alpha'), LIVE('@4', 'shell', false), { name: 'no-wid', claude_running: true }]);
  routes['/api/orchestrator/subscribe'] = { ok: true, wid: '@2', name: 'alpha', state: 'ok', why: '', queued: 0 };
  mount();
  await load([], DANGLING);
  await openInbox();
  await act(async () => {
    fireEvent.keyDown(must(picker()), { key: 'Enter' });
  });
  expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual(['alpha (@2)', 'alpha (@3)', 'zeta (@9)']);
  await act(async () => {
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' });
  });
  await act(async () => {
    fireEvent.click(must(reregBtn()));
  });
  expect(subscribes()).toEqual([{ wid: '@2' }]);
  // success repaints the chip: the control is gone
  expect(chip().className).toBe('decisions-chip decisions-chip-ok');
  expect(reregBtn()).toBeNull();
});

test('the operator\'s pick survives re-renders and is what gets subscribed', async () => {
  m.agents.agents.set([LIVE('@1', 'a'), LIVE('@2', 'b')]);
  mount();
  await load([], DANGLING);
  await openInbox();
  await pick('b (@2)');
  // a log frame and an owner re-read land while it is still dangling
  routes['/api/log'] = LOG([ev(1)]);
  await act(() => m.d.tickDecisions());
  expect(must(picker()).textContent).toContain('b (@2)');
  routes['/api/orchestrator/subscribe'] = { ok: false, error: 'nope' };
  await act(async () => {
    fireEvent.click(must(reregBtn()));
  });
  expect(subscribes()).toEqual([{ wid: '@2' }]);
});

test('a picked session that vanished falls back to the first, never a stale wid', async () => {
  m.agents.agents.set([LIVE('@1', 'a'), LIVE('@2', 'b')]);
  routes['/api/orchestrator/subscribe'] = { ok: false, error: 'nope' };
  mount();
  await load([], DANGLING);
  await openInbox();
  await pick('b (@2)');
  act(() => m.agents.agents.set([LIVE('@1', 'a')]));
  expect(must(picker()).textContent).toContain('a (@1)');
  await act(async () => {
    fireEvent.click(must(reregBtn()));
  });
  expect(subscribes()).toEqual([{ wid: '@1' }]);
});

test('a refused re-register leaves the chip visibly dangling and logs why', async () => {
  const err = vi.spyOn(console, 'error').mockImplementation(() => {});
  m.agents.agents.set([LIVE('@5', 'x')]);
  routes['/api/orchestrator/subscribe'] = { ok: false, error: 'no such window: @5' };
  mount();
  await load([], DANGLING);
  await openInbox();
  await act(async () => {
    fireEvent.click(must(reregBtn()));
  });
  expect(chip().className).toBe('decisions-chip decisions-chip-bad');
  expect(must(reregBtn()).disabled).toBe(false);
  expect(err).toHaveBeenCalledWith('re-register failed', 'no such window: @5');
  err.mockRestore();
});

test('the button is DISABLED while the take-over is in flight, across a re-render and a reopen', async () => {
  m.agents.agents.set([LIVE('@5', 'x')]);
  let release: (v: unknown) => void = () => {};
  routes['/api/orchestrator/subscribe'] = () => new Promise((r) => (release = r));
  mount();
  await load([], DANGLING);
  await openInbox();
  await act(async () => {
    fireEvent.click(must(reregBtn()));
  });
  expect(must(reregBtn()).disabled).toBe(true);
  routes['/api/log'] = LOG([ev(1)]);
  await act(() => m.d.tickDecisions());
  act(() => m.d.closeDecisions());
  await openInbox();
  expect(must(reregBtn()).disabled).toBe(true);
  expect(subscribes()).toHaveLength(1);
  await act(async () => {
    release({ ok: false, error: 'x' });
  });
  await flush();
  expect(must(reregBtn()).disabled).toBe(false);
});

test('no live Claude session: says so and offers nothing to click', async () => {
  m.agents.agents.set([LIVE('@4', 'shell', false)]);
  mount();
  await load([], { ...DANGLING, state: 'gone' });
  await openInbox();
  expect(must(document.querySelector('.decisions-chip-rereg-empty')).textContent).toBe('no live Claude session to re-register. Open one, then retry');
  expect(reregBtn()).toBeNull();
  expect(calls.some((c) => c.url === '/api/agents')).toBe(false);
});

test.each(['dangling', 'gone'])('%s with an EMPTY cache reads /api/agents before claiming nobody is live', async (state) => {
  routes['/api/agents'] = [LIVE('@6', 'late')];
  mount();
  await load([], { ...DANGLING, state });
  await openInbox();
  await flush();
  expect(calls.filter((c) => c.url === '/api/agents')).toHaveLength(1);
  expect(must(picker()).textContent).toContain('late (@6)');
});

test('a failing /api/agents (or a non-list) keeps the panel rendering the empty state', async () => {
  const err = vi.spyOn(console, 'error').mockImplementation(() => {});
  delete routes['/api/agents'];
  mount();
  await load([ev(1)], DANGLING);
  await openInbox();
  await flush();
  expect(document.querySelector('.decisions-chip-rereg-empty')).toBeTruthy();
  expect(rows()).toHaveLength(1);
  act(() => m.d.closeDecisions());
  routes['/api/agents'] = { not: 'a list' };
  await openInbox();
  await flush();
  expect(m.agents.agents.get()).toEqual([]);
  expect(document.querySelector('.decisions-chip-rereg-empty')).toBeTruthy();
  err.mockRestore();
});

// --- click-through to the task modal

const TASK = (seq: number, task_id = 't9') => ev(seq, { payload: { task_id, title: 'cmx-9 task', run_status: 'awaiting_review', branch_name: 'cmx-9' } });

test('a row naming a task_id is a button; a bare plumbing event is not', async () => {
  mount();
  await load([TASK(1), ev(2, { type: 'inbox_undeliverable', payload: { wid: '@0' } })]);
  await openInbox();
  const [bare, clickable] = rows();
  expect(clickable?.getAttribute('role')).toBe('button');
  expect(clickable?.className).toBe('feed-row feed-cls-run feed-row-clickable');
  expect(clickable?.getAttribute('title')).toBe('Open this task');
  expect(bare?.getAttribute('role')).toBeNull();
  expect(bare?.className).toBe('feed-row feed-cls-gate');
  expect(bare?.getAttribute('tabindex')).toBeNull();
  calls = [];
  fireEvent.click(must(bare));
  await flush();
  expect(calls).toEqual([]);
  expect(inbox()).toBeTruthy();
});

test('a click resolves the task against /api/dispatcher and opens THAT run, closing the inbox', async () => {
  routes['/api/dispatcher'] = { workflows: [{ awaiting_review_runs: [{ task_id: 't9', title: 'the real run', brief: 'THE AUTHORITATIVE BRIEF', status: 'awaiting_review' }] }] };
  mount();
  await load([TASK(1)]);
  await openInbox();
  await act(async () => {
    fireEvent.click(must(rows()[0]));
  });
  await flush();
  expect(inbox()).toBeNull();
  const ticket = screen.getByRole('dialog', { name: 'the real run' });
  expect(ticket.textContent).toContain('THE AUTHORITATIVE BRIEF');
});

test.each([
  ['no dispatcher match', () => ({ workflows: [] })],
  ['an unreachable dispatcher', () => Promise.reject(new Error('down'))],
])('%s: the payload ticket opens, marked partial', async (_why, answer) => {
  routes['/api/dispatcher'] = answer;
  mount();
  await load([TASK(1)]);
  await openInbox();
  await act(async () => {
    fireEvent.keyDown(must(rows()[0]), { key: 'Enter' });
  });
  await flush();
  const ticket = screen.getByRole('dialog', { name: 'cmx-9 task' });
  expect(ticket.textContent).toContain('aged out');
  expect(ticket.textContent).toContain('cmx-9');
  expect(inbox()).toBeNull();
});

test('Space opens a row too; other keys do nothing', async () => {
  mount();
  await load([TASK(1)]);
  await openInbox();
  fireEvent.keyDown(must(rows()[0]), { key: 'a' });
  await flush();
  expect(inbox()).toBeTruthy();
  await act(async () => {
    fireEvent.keyDown(must(rows()[0]), { key: ' ' });
  });
  await flush();
  expect(inbox()).toBeNull();
  expect(screen.getByRole('dialog', { name: 'cmx-9 task' })).toBeTruthy();
});

test('openDecisionTicket on an event with no task_id opens nothing and reads nothing', async () => {
  mount();
  await m.d.openDecisionTicket(ev(1, { payload: {} }) as never);
  expect(calls).toEqual([]);
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('mountDecisions draws into #decisions-island, and the jump is nav.js\'s, looked up at click time', async () => {
  const slot = document.createElement('span');
  slot.id = 'decisions-island';
  document.body.append(slot);
  expect(m.d.mountDecisions({ document: { getElementById: () => null } } as unknown as Window)).toBeNull();
  const root = must(m.d.mountDecisions(window));
  await flush();
  expect(slot.querySelector('#btn-decisions')).toBeTruthy();
  const jumpTo = vi.fn();
  (window as unknown as { chela: object }).chela = { jumpToOrchestrator: jumpTo };
  await act(async () => {
    fireEvent.click(must(slot.querySelector('#btn-decisions')));
  });
  expect(jumpTo).toHaveBeenCalledTimes(1);
  delete (window as unknown as { chela?: object }).chela;
  expect(() => m.d.legacyJump(window)()).not.toThrow();
  act(() => root.unmount());
  slot.remove();
});

// --- focus and the seen cursor (CMX-228 review)

test('opening focuses the search box; closing returns focus to the inbox button', async () => {
  mount();
  act(() => btn().focus());
  await act(async () => {
    fireEvent.click(btn());
  });
  await flush();
  expect(document.activeElement).toBe(search());
  fireEvent.keyDown(must(inbox()), { key: 'Escape' });
  await flush();
  expect(document.activeElement).toBe(btn());
});

test('a row handing over to its task modal leaves focus in the task modal', async () => {
  mount();
  await load([TASK(1)]);
  act(() => btn().focus());
  await openInbox();
  await act(async () => {
    fireEvent.click(must(rows()[0]));
  });
  await flush();
  const ticket = screen.getByRole('dialog', { name: 'cmx-9 task' });
  expect(ticket.contains(document.activeElement)).toBe(true);
});

test('opening before the first batch lands marks nothing: the first batch still seeds', async () => {
  mount();
  routes['/api/log'] = () => new Promise(() => {}); // the boot's drain is still in flight
  await openInbox();
  expect(localStorage.getItem('chela.decisions.lastSeen')).toBeNull();
  expect(m.d.inbox.get().lastSeen).toBeNull();
});

test('decisions that land while the dialog is open are seen; once closed, new ones badge', async () => {
  localStorage.setItem('chela.decisions.lastSeen', '0');
  vi.resetModules();
  m.d = await import('../index.ts');
  m.tm = await import('../../taskmodal/index.ts');
  mount();
  await load([ev(1)]);
  await openInbox();
  routes['/api/log'] = LOG([ev(1), ev(2)], { next_seq: 2, last_seq: 2 });
  await act(() => m.d.refreshLog());
  expect(badge()).toBeNull();
  expect(localStorage.getItem('chela.decisions.lastSeen')).toBe('2');
  act(() => m.d.closeDecisions());
  routes['/api/log'] = LOG([ev(3)], { next_seq: 3, last_seq: 3 });
  await act(() => m.d.refreshLog());
  expect(badge()?.textContent).toBe('1');
});

test('a jump that throws still opens the log', async () => {
  const err = vi.spyOn(console, 'error').mockImplementation(() => {});
  mount();
  jump.mockImplementation(() => {
    throw new Error('stale cache');
  });
  await act(async () => {
    fireEvent.click(btn());
  });
  expect(m.d.inboxOpen.get()).toBe(true);
  expect(err).toHaveBeenCalledWith('jump to the orchestrator', expect.any(Error));
  err.mockRestore();
});
