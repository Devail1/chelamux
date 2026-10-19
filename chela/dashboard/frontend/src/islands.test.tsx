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
