// CMX-278: the sidebar controller's Recent sessions commands and the width, against a stubbed
// /api. The rows, menus and archive are driven through the real page by tests/sidebar*.test.mjs.
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { installBridge } from '../../../shared/bridge/index.ts';
import {
  clearRecentSessions, closeRecentUndo, dismissRecentSession, liveSessionIds, refreshLayout, refreshRecentSessions,
  renderRecentSessions, resumeSession, setNav, setNavBadges, setSidebarWidth, undoDismissRecent,
} from '../controller.ts';
import type { RecentRow } from '../model.ts';

const bridge = installBridge(window);

const ROW: RecentRow = {
  store: 'session-ids', wid: '@5', session_id: 'sid-five', cwd: '/srv/five', label: 'five',
  stamped_epoch: '786-1', dismiss_key: 'sid-five',
};
const SIX: RecentRow = { ...ROW, wid: '@6', session_id: 'sid-six', label: 'six', dismiss_key: 'sid-six' };
const JUDGE: RecentRow = { ...ROW, wid: '@7', session_id: 'sid-judge', label: 'judge-liavacc/cmx-1', dismiss_key: 'sid-judge' };

type Answer = (body: unknown) => unknown;
let answers: Record<string, Answer> = {};
let calls: [string, string, unknown][] = [];

beforeEach(() => {
  calls = [];
  answers = {};
  vi.stubGlobal('fetch', (url: string, opts?: RequestInit) => {
    const method = opts?.method ?? 'GET';
    const body: unknown = opts?.body ? JSON.parse(String(opts.body)) : undefined;
    calls.push([method, url, body]);
    const a = answers[`${method} ${url}`];
    if (!a) return Promise.reject(new Error(`unexpected ${method} ${url}`));
    return Promise.resolve({ json: () => Promise.resolve(a(body)) });
  });
  vi.stubGlobal('alert', vi.fn());
  vi.stubGlobal('confirm', vi.fn(() => true));
  bridge.agents.set([]);
  renderRecentSessions({ rows: [], dispatcher_rows: [] });
  closeRecentUndo();
});

afterEach(() => {
  closeRecentUndo();
  vi.unstubAllGlobals();
});

const flush = () => new Promise(r => setTimeout(r, 0));
const shown = () => bridge.recent.get().rows.map(r => r.label);

// --- the width (first: the layout has not loaded yet) ---------------------------------------

const cssWidth = () => document.documentElement.style.getPropertyValue('--sidebar-w');

test('the width: kept locally before the layout loads, then read from it, clamped, and written to it', async () => {
  setSidebarWidth(300);
  expect(cssWidth()).toBe('300px');
  expect(bridge.sidebarLayout.get().width).toBe(300);
  expect(calls).toEqual([]); // nothing is written before the layout's first answer
  for (const [stored, css] of [[350, '350px'], [100, '264px'], [9000, '480px'], ['wide', '288px'], [null, '288px']] as const) {
    answers['GET /api/sidebar/layout'] = () => ({ layout: { order: [], width: stored } });
    await refreshLayout();
    expect(cssWidth()).toBe(css);
  }
  answers['POST /api/sidebar/layout'] = body => ({ ok: true, layout: { order: [], ...(body as object) } });
  setSidebarWidth(9999);
  expect(calls.at(-1)).toEqual(['POST', '/api/sidebar/layout', { width: 480 }]);
  setSidebarWidth(10);
  expect(calls.at(-1)).toEqual(['POST', '/api/sidebar/layout', { width: 264 }]);
  await flush();
  expect(bridge.sidebarLayout.get()).toMatchObject({ width: 264, loaded: true });
  expect(cssWidth()).toBe('264px');
});

test('a poll never snaps the column back mid-drag, and an unchanged layout is not republished', async () => {
  answers['GET /api/sidebar/layout'] = () => ({ layout: { order: [], width: 300 } });
  await refreshLayout();
  const published = bridge.sidebarLayout.get();
  await refreshLayout();
  expect(bridge.sidebarLayout.get()).toBe(published);
  document.body.classList.add('sidebar-resizing');
  document.documentElement.style.setProperty('--sidebar-w', '400px'); // the drag, live
  answers['GET /api/sidebar/layout'] = () => ({ layout: { order: [], width: 320 } });
  await refreshLayout();
  expect(cssWidth()).toBe('400px');
  document.body.classList.remove('sidebar-resizing');
  answers['GET /api/sidebar/layout'] = () => ({ layout: { order: [], width: 330 } });
  await refreshLayout();
  expect(cssWidth()).toBe('330px');
});

// --- Recent sessions ---------------------------------------------------------------------

test('the /api/restore object and a bare array both render; a non-list part is empty', async () => {
  renderRecentSessions([ROW]);
  expect(bridge.recent.get()).toMatchObject({ rows: [ROW], dispatcher_rows: [] });
  renderRecentSessions({ rows: 'x', dispatcher_rows: [ROW] });
  expect(bridge.recent.get()).toMatchObject({ rows: [], dispatcher_rows: [ROW] });
  answers['GET /api/restore'] = () => ({ rows: [SIX], dispatcher_rows: [] });
  await refreshRecentSessions();
  expect(shown()).toEqual(['six']);
  // a failed read keeps the last answer
  delete answers['GET /api/restore'];
  await refreshRecentSessions();
  expect(shown()).toEqual(['six']);
});

test('the live session ids are the ones /api/agents reports, never a window address', () => {
  bridge.agents.set([{ window_id: '@5', session_id: 'sid-x' }, { window_id: '@9' }, { window_id: '@10', session_id: null }]);
  expect([...liveSessionIds()]).toEqual(['sid-x']);
});

test('Resume POSTs the row identity, then re-reads the list', async () => {
  renderRecentSessions([ROW]);
  answers['POST /api/restore/resume'] = () => ({ ok: true, wid: '@99' });
  answers['GET /api/agents'] = () => [];
  answers['GET /api/agents/context'] = () => [];
  answers['GET /api/sidebar/archive'] = () => ({ hidden: [], sessions: [] });
  answers['GET /api/sidebar/layout'] = () => ({ layout: null });
  answers['GET /api/restore'] = () => ({ rows: [], dispatcher_rows: [] });
  expect(await resumeSession(ROW)).toBe(true);
  expect(calls[0]).toEqual(['POST', '/api/restore/resume',
    { store: 'session-ids', wid: '@5', session_id: 'sid-five', stamped_epoch: '786-1' }]);
  expect(calls.map(c => c[1])).toContain('/api/restore');
  expect(shown()).toEqual([]);
  expect(alert).not.toHaveBeenCalled();
});

test.each([
  ['an error answer', () => ({ ok: false, error: 'this row no longer matches' }), 'this row no longer matches'],
  ['no answer', undefined, 'Could not resume this session. It may have changed since the list was loaded.'],
])('a failed resume (%s) says so, re-reads Recent, and resolves false', async (_what, answer, said) => {
  if (answer) answers['POST /api/restore/resume'] = answer;
  answers['GET /api/restore'] = () => ({ rows: [ROW], dispatcher_rows: [] });
  expect(await resumeSession({ ...ROW, stamped_epoch: '' })).toBe(false);
  expect(calls[0]?.[2]).toMatchObject({ stamped_epoch: null });
  expect(alert).toHaveBeenCalledWith(said);
  await flush();
  expect(calls.at(-1)?.slice(0, 2)).toEqual(['GET', '/api/restore']);
});

test('a dismiss hides the row at once and records ONLY the hide: the transcript is never touched', async () => {
  renderRecentSessions({ rows: [ROW, SIX], dispatcher_rows: [] });
  let release: () => void = () => {};
  answers['POST /api/restore/dismiss'] = () => ({ ok: true });
  const held = new Promise<void>(r => { release = r; });
  const real = globalThis.fetch;
  vi.stubGlobal('fetch', async (url: string, opts?: RequestInit) => { await held; return real(url, opts); });
  const done = dismissRecentSession('sid-five');
  expect(shown()).toEqual(['six']); // before the server answers
  release();
  await done;
  expect(calls).toEqual([['POST', '/api/restore/dismiss', { session_ids: ['sid-five'] }]]);
  expect(bridge.recent.get().undo).toEqual({ sids: ['sid-five'], text: 'Dismissed five' });
});

test('a dismiss the server refused puts the row back and says so; no Undo', async () => {
  renderRecentSessions([ROW]);
  answers['POST /api/restore/dismiss'] = () => ({ ok: false });
  await dismissRecentSession('sid-five');
  expect(shown()).toEqual(['five']);
  expect(bridge.recent.get().undo).toBeNull();
  expect(alert).toHaveBeenCalledWith('Could not dismiss. Nothing was changed.');
});

test('a dispatcher row is dismissed by its address key, and only it leaves', async () => {
  const d1 = { ...ROW, session_id: undefined, label: 'judge-cmx-206', dismiss_key: 'row:a' };
  const d2 = { ...d1, label: 'judge-cmx-207', dismiss_key: 'row:b' };
  renderRecentSessions({ rows: [ROW], dispatcher_rows: [d1, d2] });
  answers['POST /api/restore/dismiss'] = () => ({ ok: true });
  await dismissRecentSession('row:a');
  expect(calls).toEqual([['POST', '/api/restore/dismiss', { session_ids: ['row:a'] }]]);
  expect(bridge.recent.get().dispatcher_rows.map(r => r.label)).toEqual(['judge-cmx-207']);
  expect(shown()).toEqual(['five']);
  expect(bridge.recent.get().undo?.text).toBe('Dismissed judge-cmx-206');
});

test('Undo un-dismisses the same keys, closes the toast and re-reads; a failed Undo says so', async () => {
  renderRecentSessions([SIX]);
  answers['POST /api/restore/dismiss'] = () => ({ ok: true });
  await dismissRecentSession('sid-six');
  answers['POST /api/restore/undismiss'] = () => ({ ok: true });
  answers['GET /api/restore'] = () => ({ rows: [SIX], dispatcher_rows: [] });
  await undoDismissRecent(['sid-six']);
  expect(calls.slice(1)).toEqual([['POST', '/api/restore/undismiss', { session_ids: ['sid-six'] }], ['GET', '/api/restore', undefined]]);
  expect(bridge.recent.get().undo).toBeNull();
  expect(shown()).toEqual(['six']);
  expect(alert).not.toHaveBeenCalled();
  answers['POST /api/restore/undismiss'] = () => ({ ok: false });
  await undoDismissRecent(['sid-six']);
  expect(alert).toHaveBeenCalledWith('Could not undo the dismiss.');
});

test('the Undo toast closes by itself after 6 s', async () => {
  vi.useFakeTimers();
  try {
    renderRecentSessions([ROW]);
    answers['POST /api/restore/dismiss'] = () => ({ ok: true });
    await dismissRecentSession('sid-five');
    vi.advanceTimersByTime(5999);
    expect(bridge.recent.get().undo).not.toBeNull();
    vi.advanceTimersByTime(1);
    expect(bridge.recent.get().undo).toBeNull();
  } finally {
    vi.useRealTimers();
  }
});

test('Clear all dismisses what the section SHOWS: not a live session, not a judge; a declined confirm changes nothing', async () => {
  bridge.agents.set([{ window_id: '@15', session_id: 'sid-six' }]);
  renderRecentSessions({ rows: [ROW, SIX, JUDGE, { ...ROW, wid: '@8', session_id: 'sid-8', label: 'eight', dismiss_key: 'sid-8' }], dispatcher_rows: [] });
  answers['POST /api/restore/dismiss'] = () => ({ ok: true });
  vi.mocked(confirm).mockReturnValueOnce(false);
  await clearRecentSessions();
  expect(calls).toEqual([]);
  await clearRecentSessions();
  expect(confirm).toHaveBeenLastCalledWith('Dismiss all 2 recent sessions? Transcripts are kept; Undo is offered briefly.');
  expect(calls).toEqual([['POST', '/api/restore/dismiss', { session_ids: ['sid-five', 'sid-8'] }]]);
  expect(bridge.recent.get().undo?.text).toBe('Dismissed 2 sessions');
});

// --- the nav rows' state -----------------------------------------------------------------

test('the nav store: items and the active view, and badges merged by id', () => {
  setNav({ items: [{ id: 'work', label: 'Work' }], active: 'work' });
  setNavBadges({ 'side-runs-count': '3' });
  setNavBadges({ 'side-pr-count': '1 PR' });
  expect(bridge.nav.get()).toEqual({
    items: [{ id: 'work', label: 'Work' }], active: 'work', badges: { 'side-runs-count': '3', 'side-pr-count': '1 PR' },
  });
});
