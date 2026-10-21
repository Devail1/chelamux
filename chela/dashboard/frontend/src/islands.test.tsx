import { act } from 'react';
import { expect, test, vi } from 'vitest';
import { boot } from './islands.tsx';

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

test('boot mounts the Work islands: Runs, Schedules and the New-task dialog (CMX-231)', async () => {
  vi.stubGlobal('fetch', () => Promise.resolve({ ok: true, json: () => Promise.resolve([]) }));
  document.body.innerHTML = '<div id="work-runs"></div><div id="work-schedules"></div><div id="newtask-island"></div>';
  Reflect.deleteProperty(window, 'chelaBridge');
  await act(async () => boot(window));
  const { work } = await import('./shared/store/work.ts');
  await act(async () => work.set({ configured: false }));
  expect(document.querySelector('#work-runs .work-empty-title')?.textContent).toBe('Turn a TODO list into pull requests');
  expect(document.querySelector('#work-schedules .canvas-toolbar')?.textContent).toBe('+ Add Task');
  const { openNewTask } = await import('./features/newtask/index.ts');
  await act(async () => openNewTask());
  expect(document.querySelector('[role="dialog"] h3')?.textContent).toBe('New task');
  await act(async () => work.set(null));
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
