import { act } from 'react';
import type { Root } from 'react-dom/client';
import { afterEach, expect, test, vi } from 'vitest';
import { boot } from './islands.tsx';

// Every root boot creates, so each test can unmount its islands before the next resets
// document.body (CMX-311). A root left mounted stays subscribed to the stores, and a portal it
// still holds (a Radix overlay whose Presence commits a turn later on a loaded machine) is
// orphaned by the reset: the commit that later removes it throws NotFoundError in another test.
const roots = vi.hoisted(() => [] as Root[]);
vi.mock('react-dom/client', async (importOriginal) => {
  const real = await importOriginal<typeof import('react-dom/client')>();
  return {
    ...real,
    createRoot: (...args: Parameters<typeof real.createRoot>) => {
      const root = real.createRoot(...args);
      roots.push(root);
      return root;
    },
  };
});

const escape = () => act(async () => document.activeElement?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
// Unmounting removes every portal synchronously, with the DOM it was drawn into still in place.
afterEach(() => {
  act(() => roots.splice(0).forEach((r) => r.unmount()));
});

test('boot installs the bridge, the saved theme, the panes\' window.chela contract and the page keys', async () => {
  // No overlays slot: a second Overlays root would stay subscribed to the dialogs' stores after
  // this test's body is reset, and draw (then fail to remove) a later test's portals.
  document.body.innerHTML = '';
  localStorage.setItem('chela_theme', 'nord');
  Reflect.deleteProperty(window, 'chelaBridge');
  await act(async () => boot(window));
  expect(window.chelaBridge?.agents.get()).toEqual([]);
  expect(document.body.dataset.theme).toBe('nord');
  const { settings, toggleSettings } = await import('./features/settings/index.ts');
  const { closePalette, palette } = await import('./features/palette/index.ts');
  const chela = (window as unknown as { chela: { toggleSettings(): void; openPalette(): void } }).chela;
  chela.toggleSettings();
  expect(settings.get().open).toBe(true);
  toggleSettings();
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true, bubbles: true }));
  expect(palette.get().open).toBe(true);
  closePalette();
  chela.openPalette();
  expect(palette.get().open).toBe(true);
  closePalette();
  localStorage.clear();
});

test('boot mounts the keybar and diff modal islands into their slots (CMX-237)', async () => {
  vi.stubGlobal('fetch', () => Promise.resolve({ ok: true, json: () => Promise.resolve({ is_git: true, has_head: true, files: [] }) }));
  document.body.innerHTML = '<div id="keybar-island"></div><div id="diff-modal-island"></div>';
  Reflect.deleteProperty(window, 'chelaBridge');
  await act(async () => boot(window));
  expect(document.querySelector('#keybar-island #term-keybar')).not.toBeNull();
  expect(document.querySelector('#keybar-island #term-bar')).not.toBeNull();
  const { openDiffModal, closeDiffModal } = await import('./features/diffmodal/index.ts');
  await act(async () => openDiffModal('@1'));
  expect(document.querySelector('[role="dialog"]')?.textContent).toContain('No changes');
  await act(async () => closeDiffModal());
  vi.unstubAllGlobals();
});

test('boot mounts the Work view (Board, Runs, Schedules), its toasts and the New-task dialog (CMX-231, CMX-233)', async () => {
  vi.stubGlobal('fetch', () => Promise.resolve({ ok: true, json: () => Promise.resolve([]) }));
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} })); // jsdom has none
  document.body.innerHTML = '<div id="panel-work"></div><div id="work-toasts"></div><div id="newtask-island"></div>';
  Reflect.deleteProperty(window, 'chelaBridge');
  await act(async () => boot(window));
  const { work } = await import('./shared/store/work.ts');
  await act(async () => work.set({ configured: false }));
  expect(document.querySelector('#panel-work #work-seg')?.textContent).toBe('BoardRunsSchedules');
  expect(document.querySelector('#panel-work #work-board .work-empty-title')?.textContent).toBe('A board for work in flight');
  expect(document.querySelector('#panel-work #work-runs .work-empty-title')?.textContent).toBe('Turn a TODO list into pull requests');
  expect(document.querySelector('#panel-work #work-schedules .canvas-toolbar')?.textContent).toBe('+ Add Task');
  const { showToast } = await import('./features/work/index.ts');
  await act(async () => showToast('a note'));
  expect(document.querySelector('#work-toasts .run-toast')?.textContent).toBe('a note');
  const { openNewTask } = await import('./features/newtask/index.ts');
  await act(async () => openNewTask());
  expect(document.querySelector('[role="dialog"] h3')?.textContent).toBe('New task');
  await escape();
  await act(async () => work.set(null));
  vi.unstubAllGlobals();
});

test('boot mounts the chrome into its slots (CMX-219): pills, gear, New session, the "+", detail, overlays', async () => {
  vi.stubGlobal('fetch', () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }));
  document.body.innerHTML = ['sidebar-new-island', 'sessions-count-island', 'resources-island', 'primary-menu-island', 'new-fab-island',
    'status-health-island', 'access-req-island', 'approvals-island', 'term-approvals-island'].map((id) => `<span id="${id}"></span>`).join('')
    + '<div id="agent-detail"></div><div id="overlays-island"></div>';
  Reflect.deleteProperty(window, 'chelaBridge');
  await act(async () => boot(window));
  const inSlot = (slot: string, sel: string) => document.querySelector(`#${slot} ${sel}`) !== null;
  expect([
    inSlot('sidebar-new-island', '.sidebar-new-btn'), inSlot('sessions-count-island', '#hdr-agents'), inSlot('resources-island', '#resources-strip'),
    inSlot('primary-menu-island', '#btn-primary-menu'), inSlot('new-fab-island', '#btn-new-mobile'), inSlot('status-health-island', '#status-health-warn'),
    inSlot('access-req-island', '#btn-access-req'), inSlot('approvals-island', '#btn-approvals'), inSlot('term-approvals-island', '#term-approvals'),
  ]).toEqual(Array(9).fill(true));
  const { detailAgent } = await import('./features/agentdetail/index.ts');
  await act(async () => detailAgent.set('nobody'));
  expect(document.querySelector('#agent-detail .detail-title')?.textContent).toBe('nobody');
  await act(async () => detailAgent.set(null));
  const { openSendMsg } = await import('./features/agentdetail/index.ts');
  await act(async () => openSendMsg('w1'));
  expect(document.querySelector('[role="dialog"] h3')?.textContent).toBe('Send Message to w1');
  // its ×, not Escape: an Escape lands where focus is, and under load focus may not be in yet
  await act(async () => document.querySelector<HTMLElement>('[role="dialog"] .ui-close')?.click());
  vi.unstubAllGlobals();
});

test('boot mounts the Decisions inbox into its slot and the task modal island (CMX-228)', async () => {
  vi.stubGlobal('fetch', () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }));
  document.body.innerHTML = '<span id="decisions-island"></span><div id="task-modal-island"></div>';
  Reflect.deleteProperty(window, 'chelaBridge');
  await act(async () => boot(window));
  expect(document.querySelector('#decisions-island #btn-decisions')).not.toBeNull();
  const { openTaskModal, closeTaskModal } = await import('./features/taskmodal/index.ts');
  await act(async () => openTaskModal({ title: 'a task' }));
  expect(document.querySelector('[role="dialog"].modal-task')?.textContent).toContain('a task');
  await act(async () => closeTaskModal());
  vi.unstubAllGlobals();
});
