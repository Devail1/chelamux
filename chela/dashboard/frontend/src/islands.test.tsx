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
