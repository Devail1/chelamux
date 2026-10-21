import { act } from 'react';
import { afterEach, expect, test, vi } from 'vitest';
import { boot } from './islands.tsx';

// Each test resets document.body. A dialog left open keeps its portal there; the reset
// orphans it and a later commit that removes it throws (NotFoundError). So none stays open.
const escape = () => act(async () => document.activeElement?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
// A close commits through Radix's Presence, which can land a turn later on a loaded machine
// (the full pytest run): wait for it, so the next test's reset never orphans a live portal.
afterEach(async () => {
  await vi.waitFor(() => expect(document.querySelector('[role="dialog"]')).toBeNull());
});

test('boot installs the bridge and mounts the version island into #version-island', async () => {
  vi.stubGlobal('fetch', () => Promise.resolve({ ok: true, json: () => Promise.resolve({ version: 'v9' }) }));
  document.body.innerHTML = '<span id="version-island"></span>';
  Reflect.deleteProperty(window, 'chelaBridge');
  await act(async () => boot(window));
  expect(window.chelaBridge?.agents.get()).toEqual([]);
  expect(document.getElementById('version-island')?.textContent).toBe('build v9');
  vi.unstubAllGlobals();
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
  await escape();
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
