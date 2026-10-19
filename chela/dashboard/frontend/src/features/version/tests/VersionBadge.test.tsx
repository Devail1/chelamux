import { act, cleanup, render } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { VersionBadge } from '../index.ts';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function answer(res: Partial<Response> | Error) {
  const fetch = vi.fn(() => (res instanceof Error ? Promise.reject(res) : Promise.resolve(res)));
  vi.stubGlobal('fetch', fetch);
  return fetch;
}

async function shown(base?: string) {
  let html = '';
  await act(async () => {
    html = render(<VersionBadge base={base} />).container.innerHTML;
  });
  return html;
}

test('renders the version /api/version answers, fetched under the base path', async () => {
  const fetch = answer({ ok: true, json: () => Promise.resolve({ version: 'abc123def456' }) });
  let el!: HTMLElement;
  await act(async () => {
    el = render(<VersionBadge base="/prefix" />).container;
  });
  expect(fetch.mock.calls).toEqual([['/prefix/api/version']]);
  expect(el.innerHTML).toBe('<span class="version-badge" title="Dashboard build">build abc123def456</span>');
});

test('the default base is the page path without its trailing slash', async () => {
  window.history.replaceState(null, '', '/chela/');
  const fetch = answer({ ok: true, json: () => Promise.resolve({ version: 'v1' }) });
  await shown();
  expect(fetch.mock.calls).toEqual([['/chela/api/version']]);
  window.history.replaceState(null, '', '/');
});

test.each([
  ['a non-OK answer', { ok: false, json: () => Promise.resolve({ version: 'v1' }) }],
  ['no version in the body', { ok: true, json: () => Promise.resolve({}) }],
  ['a non-string version', { ok: true, json: () => Promise.resolve({ version: 7 }) }],
  ['a failed request', new TypeError('offline')],
])('renders nothing on %s', async (_, res) => {
  answer(res);
  expect(await shown('')).toBe('');
});
