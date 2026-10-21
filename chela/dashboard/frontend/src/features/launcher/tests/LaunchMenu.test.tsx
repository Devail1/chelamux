// CMX-212 P20: the "+ new" launch menu and the launches behind it (ported from launcher.js and
// nav.js's #new-menu by CMX-219; tests/new_menu_sandbox.test.mjs and mobile_new_fab.test.mjs
// drive it on the real page). Favorites and Recent launch Claude (a plain shell from the shell
// icon); the star pins or unpins, x forgets a recent; + opens the favorites picker; a launch
// into a dir with a live Claude focuses that pane instead of spawning a twin.
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { agents } from '../../../shared/store/agents.ts';
import { LaunchMenu, NewSessionButton, NewSessionFab, closeLaunchMenu, launchProject, launcher, newSandboxedSession, newShellWindow, openLaunchMenu, refreshLauncher, samePath, setLauncherHost } from '../index.ts';

type Call = { url: string; method: string; body: unknown };
let calls: Call[] = [];
let replies: Record<string, unknown> = {};
let fail = '';
const host = { focusPaneByWid: vi.fn(), selectView: vi.fn(), refreshSidebar: vi.fn() };
const alerts: string[] = [];

beforeEach(() => {
  calls = [];
  fail = '';
  alerts.length = 0;
  replies = {
    '/api/launcher': { favorites: [{ path: '/fav/one', label: 'one', exists: true }], recent: [{ path: '/rec/two/', label: 'two', exists: false }] },
    '/api/launcher/suggest': [{ path: '/p/a', label: 'a' }, { path: '/p/b', label: 'b', pinned: true }],
  };
  for (const f of Object.values(host)) f.mockClear();
  setLauncherHost(host);
  agents.set([]);
  launcher.set({ recent: [], favorites: [] });
  vi.stubGlobal('fetch', (url: string, opts?: RequestInit) => {
    calls.push({ url, method: opts?.method ?? 'GET', body: opts?.body ? JSON.parse(String(opts.body)) : undefined });
    if (fail && url.endsWith(fail)) return Promise.reject(new TypeError('Failed to fetch'));
    return Promise.resolve({ json: () => Promise.resolve(replies[url] ?? { ok: true }) });
  });
  vi.stubGlobal('alert', (m: string) => alerts.push(m));
  render(<><NewSessionButton /><NewSessionFab /><LaunchMenu /></>);
});
afterEach(() => {
  act(() => closeLaunchMenu());
  cleanup();
  vi.unstubAllGlobals();
});

const settle = () => act(() => new Promise((r) => setTimeout(r, 0)));
const menu = () => document.querySelector('.popover.launch-menu') as HTMLElement | null;
const posts = () => calls.filter((c) => c.method === 'POST').map((c) => [c.url, c.body]);
async function openFrom(sel: string) {
  fireEvent.click(document.querySelector(sel) as HTMLElement);
  await settle();
}
async function click(el: Element | null | undefined) {
  if (!el) throw new Error('nothing to click');
  fireEvent.click(el);
  await settle();
}

test('New session and the phone "+" each open the launch menu, re-reading the lists', async () => {
  await openFrom('.sidebar-new-btn');
  expect(menu()?.getAttribute('aria-label')).toBe('New');
  expect(calls.map((c) => c.url)).toEqual(['/api/launcher']);
  expect([...(menu()?.querySelectorAll('.lr-label') ?? [])].map((e) => e.textContent)).toEqual(['one', 'two']);
  expect([...(menu()?.querySelectorAll('.launch-group-label') ?? [])].map((e) => e.textContent)).toEqual(['Favorites', 'Recent']);
  expect([...(menu()?.querySelectorAll(':scope > button.popover-item') ?? [])].map((e) => e.textContent)).toEqual(
    ['Init a repo', 'Scheduled task', 'Sandboxed session…', 'Sandboxed session · allow web access…', 'Shell window']);
  act(() => closeLaunchMenu());
  expect(menu()).toBeNull();
  await openFrom('#btn-new-mobile');
  expect(menu()).not.toBeNull();
  expect(document.getElementById('btn-new-mobile')?.getAttribute('aria-label')).toBe('New session');
});

test('Escape closes it', async () => {
  await openFrom('.sidebar-new-btn');
  fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' });
  await settle();
  expect(menu()).toBeNull();
});

test('a missing dir is drawn gone but still launches; a pinned row has no forget', async () => {
  await openFrom('.sidebar-new-btn');
  const [fav, rec] = [...(menu()?.querySelectorAll('.launch-row') ?? [])] as HTMLElement[];
  expect([fav?.className, fav?.title, rec?.className, rec?.title]).toEqual(['launch-row', '/fav/one', 'launch-row lr-gone', '/rec/two/ (missing)']);
  expect(fav?.querySelector('.lr-forget')).toBeNull();
  expect(fav?.querySelector('.lr-star')?.getAttribute('aria-label')).toBe('Unpin from Favorites');
  expect(rec?.querySelector('.lr-star')?.getAttribute('aria-label')).toBe('Pin to Favorites');
});

test('a row launches Claude there; its shell icon launches a plain shell; either closes the menu and lands on the Wall', async () => {
  await openFrom('.sidebar-new-btn');
  await click(menu()?.querySelector('.launch-row .lr-main'));
  expect(menu()).toBeNull();
  await openFrom('.sidebar-new-btn');
  await click(menu()?.querySelectorAll('.launch-row .lr-shell')[1]);
  expect(posts()).toEqual([['/api/agents/spawn', { cwd: '/fav/one', command: 'claude' }], ['/api/agents/spawn', { cwd: '/rec/two/' }]]);
  expect(host.selectView.mock.calls).toEqual([['terminals'], ['terminals']]);
  expect(host.refreshSidebar).toHaveBeenCalledTimes(2);
});

test('the star pins and unpins, x forgets, and the menu redraws from the answer', async () => {
  await openFrom('.sidebar-new-btn');
  replies['/api/launcher/unpin'] = { ok: true, favorites: [], recent: [{ path: '/fav/one', label: 'one', exists: true }] };
  await click(menu()?.querySelector('.lr-star.pinned'));
  expect(menu()?.querySelector('.side-empty')?.textContent).toBe('No projects yet. Add one with +');
  replies['/api/launcher/pin'] = { ok: true, favorites: [{ path: '/fav/one', label: 'one', exists: true }], recent: [{ path: '/r', label: 'r', exists: true }] };
  await click(menu()?.querySelector('.lr-star:not(.pinned)'));
  expect(menu()?.querySelector('.side-empty')).toBeNull();
  replies['/api/launcher/forget'] = { ok: true, favorites: [{ path: '/fav/one', label: 'one', exists: true }], recent: [] };
  await click(menu()?.querySelector('.lr-forget'));
  expect(posts()).toEqual([['/api/launcher/unpin', { path: '/fav/one' }], ['/api/launcher/pin', { path: '/fav/one' }], ['/api/launcher/forget', { path: '/r' }]]);
  expect(menu()?.querySelectorAll('.launch-row')).toHaveLength(1);
  // the edits leave the menu open
  expect(menu()).not.toBeNull();
});

test('a refused or failed edit keeps the lists', async () => {
  launcher.set({ favorites: [{ path: '/x', label: 'x' }], recent: [] });
  replies['/api/launcher/pin'] = { ok: false };
  const { pinFav, forgetRecent } = await import('../index.ts');
  await act(() => pinFav('/y'));
  fail = '/api/launcher/forget';
  await act(() => forgetRecent('/x'));
  expect(launcher.get().favorites.map((f) => f.path)).toEqual(['/x']);
});

test('+ opens the favorites picker: the unpinned repos; a pick pins it and closes', async () => {
  await openFrom('.sidebar-new-btn');
  await click(screen.getByRole('button', { name: 'Add a project to Favorites' }));
  expect(menu()).toBeNull();
  const picker = document.querySelector('[aria-label="Add a project to Favorites"].popover') as HTMLElement;
  expect([...picker.querySelectorAll('button')].map((b) => [b.textContent, b.title])).toEqual([['a', '/p/a']]);
  await click(picker.querySelector('button'));
  expect(posts()).toEqual([['/api/launcher/pin', { path: '/p/a' }]]);
  expect(document.querySelector('.popover')).toBeNull();
});

test('the picker says Scanning while it reads, and says how to get projects when it finds none', async () => {
  let release: (v: unknown) => void = () => {};
  vi.stubGlobal('fetch', (url: string) => (url.endsWith('/suggest')
    ? new Promise((r) => (release = (v) => r({ json: () => Promise.resolve(v) })))
    : Promise.resolve({ json: () => Promise.resolve({}) })));
  await openFrom('.sidebar-new-btn');
  await click(screen.getByRole('button', { name: 'Add a project to Favorites' }));
  expect(document.querySelector('.popover .popover-note')?.textContent).toBe('Scanning…');
  release({ error: 'not a list' });
  await settle();
  expect(document.querySelector('.popover .popover-note')?.textContent).toBe('No projects to add (set CHELA_PROJECTS_DIR)');
});

test('Init a repo (suggesting the launcher dirs) and Scheduled task open their dialogs', async () => {
  const { InitRepo } = await import('../../runs/index.ts');
  const { Schedules } = await import('../../schedules/index.ts');
  render(<><InitRepo /><Schedules /></>);
  await openFrom('.sidebar-new-btn');
  await click(screen.getByRole('button', { name: 'Init a repo' }));
  expect(menu()).toBeNull();
  expect(screen.getByRole('dialog', { name: 'Init a repo for the dispatcher' })).toBeTruthy();
  expect([...document.querySelectorAll('#init-suggestions option')].map((o) => o.getAttribute('value'))).toEqual(['/fav/one', '/rec/two/']);
  fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' });
  await settle();
  await openFrom('.sidebar-new-btn');
  await click(screen.getByRole('button', { name: 'Scheduled task' }));
  expect(screen.getByRole('dialog').querySelector('h3')?.textContent).toMatch(/schedule/i);
});

test('a Claude launch into a dir with a live Claude focuses that pane instead (trailing slash ignored)', async () => {
  agents.set([{ window_id: '@7', name: 'x', claude_running: true, cwd: '/rec/two' }]);
  await act(() => launchProject('/rec/two/'));
  expect(host.focusPaneByWid.mock.calls).toEqual([['@7']]);
  expect(posts()).toEqual([]);
  // a shell, or a fresh session (a group's "+"), always spawns
  const live = [{ window_id: '@7', name: 'x', claude_running: true, cwd: '/rec/two' }];
  await act(() => launchProject('/rec/two', { shell: true }));
  agents.set(live); // a launch empties the cache: the twin is still live for the next one
  await act(() => launchProject('/rec/two', { fresh: true }));
  expect(host.focusPaneByWid).toHaveBeenCalledTimes(1);
  expect(posts()).toEqual([['/api/agents/spawn', { cwd: '/rec/two' }], ['/api/agents/spawn', { cwd: '/rec/two', command: 'claude' }]]);
  // a stopped Claude, or another dir, is no twin
  agents.set([{ window_id: '@8', claude_running: false, cwd: '/a' }, { window_id: '@9', claude_running: true, cwd: '/ab' }]);
  await act(() => launchProject('/a'));
  expect(host.focusPaneByWid).toHaveBeenCalledTimes(1);
});

test('a launch empties the agents cache (the new window shows on the next read) and re-reads the lists', async () => {
  agents.set([{ window_id: '@1', claude_running: false, cwd: '/q' }]);
  await act(() => launchProject('/z'));
  expect(agents.get()).toEqual([]);
  expect(calls.filter((c) => c.url === '/api/launcher')).toHaveLength(1);
});

test('a refused or failed launch says why and lands nowhere', async () => {
  replies['/api/agents/spawn'] = { ok: false, error: 'no such dir' };
  await act(() => launchProject('/gone'));
  replies['/api/agents/spawn'] = { ok: false };
  await act(() => launchProject('/gone'));
  fail = '/api/agents/spawn';
  await act(() => launchProject('/gone'));
  expect(alerts).toEqual(['Launch failed: no such dir', 'Launch failed: unknown error', 'Launch failed: TypeError: Failed to fetch']);
  expect(host.selectView).not.toHaveBeenCalled();
});

test('Shell window spawns a plain window, staying on the view; a refusal says the server reason', async () => {
  await act(() => newShellWindow());
  expect(posts()).toEqual([['/api/agents/spawn', undefined]]);
  expect(host.refreshSidebar).toHaveBeenCalledTimes(1);
  expect(host.selectView).not.toHaveBeenCalled();
  replies['/api/agents/spawn'] = { ok: false, error: 'terminals are off' };
  await act(() => newShellWindow());
  replies['/api/agents/spawn'] = { ok: false };
  await act(() => newShellWindow());
  fail = '/api/agents/spawn';
  await act(() => newShellWindow());
  const fallback = 'Could not spawn a window (enable terminals, or use tmux directly).';
  expect(alerts).toEqual(['terminals are off', fallback, fallback]);
});

test('a sandboxed session asks for the dir, pre-filled with the first favorite, and POSTs it to the sandboxed route', async () => {
  launcher.set({ favorites: [{ path: '/f', label: 'f' }], recent: [{ path: '/r', label: 'r' }] });
  const asked: [string, string][] = [];
  vi.stubGlobal('prompt', (q: string, d: string) => {
    asked.push([q, d]);
    return '  /work/proj  ';
  });
  await act(() => newSandboxedSession());
  expect(asked).toEqual([['Sandboxed session: project directory (the guest sees ONLY this):', '/f']]);
  expect(posts()).toEqual([['/api/agents/spawn-sandboxed', { cwd: '/work/proj' }]]);
  expect(host.selectView.mock.calls).toEqual([['terminals']]);
});

test('web access is confirmed first and sends web:true; a decline, or no dir, launches nothing', async () => {
  vi.stubGlobal('prompt', () => '/w');
  let answer = false;
  const questions: string[] = [];
  vi.stubGlobal('confirm', (q: string) => {
    questions.push(q);
    return answer;
  });
  await act(() => newSandboxedSession(true));
  expect(posts()).toEqual([]);
  expect(questions[0]).toMatch(/^Allow web access for this session\?\n\nIt can fetch any PUBLIC web page/);
  expect(questions[0]).toMatch(/keep only the guest's own material in \/w\.$/);
  answer = true;
  await act(() => newSandboxedSession(true));
  expect(posts()).toEqual([['/api/agents/spawn-sandboxed', { cwd: '/w', web: true }]]);
  vi.stubGlobal('prompt', () => null);
  await act(() => newSandboxedSession());
  expect(posts()).toHaveLength(1);
});

test('a refused or failed sandboxed session says why', async () => {
  vi.stubGlobal('prompt', () => '/w');
  replies['/api/agents/spawn-sandboxed'] = { ok: false, error: 'no docker' };
  await act(() => newSandboxedSession());
  replies['/api/agents/spawn-sandboxed'] = { ok: false };
  await act(() => newSandboxedSession());
  fail = '/api/agents/spawn-sandboxed';
  await act(() => newSandboxedSession());
  expect(alerts).toEqual(['Sandboxed session refused: no docker', 'Sandboxed session refused: unknown error', 'Sandboxed session failed: TypeError: Failed to fetch']);
});

test('refreshLauncher keeps the last lists on a failed read', async () => {
  await act(() => refreshLauncher());
  expect(launcher.get().favorites).toHaveLength(1);
  fail = '/api/launcher';
  await act(() => refreshLauncher());
  expect(launcher.get().favorites).toHaveLength(1);
  replies['/api/launcher'] = {};
  fail = '';
  await act(() => refreshLauncher());
  expect(launcher.get()).toEqual({ recent: [], favorites: [] });
});

test('samePath ignores trailing slashes and never matches an empty side', () => {
  expect(samePath('/a/', '/a')).toBe(true);
  expect(samePath('/a//', '/a')).toBe(true);
  expect(samePath('/a', '/ab')).toBe(false);
  expect(samePath('', '')).toBe(false);
  expect(samePath(null, '/a')).toBe(false);
});

test('openLaunchMenu opens it at any element (the gear menu passes its gear)', async () => {
  const gear = document.createElement('button');
  document.body.appendChild(gear);
  act(() => openLaunchMenu(gear));
  await settle();
  expect(menu()).not.toBeNull();
  act(() => closeLaunchMenu());
  expect(menu()).toBeNull();
  gear.remove();
});
