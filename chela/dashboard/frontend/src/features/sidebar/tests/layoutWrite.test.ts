// CMX-380: what a layout write RESOLVES to, against a stubbed /api: writeLayout's failure in
// words (the server's `error`, else that it refused, else that it did not answer), and
// commitLayout's "did the write hold" (true only when it did; false on a revert and on a
// failure a newer write has already covered).
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { installBridge } from '../../../shared/bridge/index.ts';
import { commitLayout, refreshLayout, writeLayout } from '../lib/layout.ts';

const bridge = installBridge(window);

/** What the next layout POSTs answer, in order (the last one repeats); 'down' rejects. */
let answers: unknown[] = [];
const alerts = vi.fn();

beforeEach(async () => {
  answers = [];
  alerts.mockReset();
  vi.stubGlobal('alert', alerts);
  vi.stubGlobal('fetch', (url: string, opts?: RequestInit) => {
    const method = opts?.method ?? 'GET';
    if (url.endsWith('/api/sidebar/layout') && method === 'GET') {
      return Promise.resolve({ json: () => Promise.resolve({ layout: { order: ['/srv/a', '/srv/b'] } }) });
    }
    if (url.endsWith('/api/sidebar/layout') && method === 'POST') {
      const a = answers.length > 1 ? answers.shift() : answers[0];
      if (a === 'down') return Promise.reject(new Error('offline'));
      return Promise.resolve({ json: () => Promise.resolve(a) });
    }
    return Promise.reject(new Error(`unexpected ${method} ${url}`));
  });
  await refreshLayout(); // loaded: a write is sent, not only kept
});

afterEach(() => vi.unstubAllGlobals());

test('writeLayout: a refusal is named by the server\'s error, else as refused; a held write is null', async () => {
  const cases: [unknown, string | null][] = [
    // `ok: false` alone is a refusal, with no error to quote
    [{ ok: false }, 'the dashboard refused it'],
    // an `error` alone is one too, and it is quoted
    [{ error: 'disk full' }, 'disk full'],
    [{ ok: false, error: 'disk full' }, 'disk full'],
    // an EMPTY error says nothing: the generic words, never a blank reason
    [{ ok: false, error: '' }, 'the dashboard refused it'],
    [{ error: '' }, 'the dashboard refused it'],
    [{ ok: false, error: 7 }, 'the dashboard refused it'],
    ['down', 'the dashboard did not answer'],
    [{ ok: true, layout: { order: ['/srv/b', '/srv/a'] } }, null],
    [{ ok: true }, null],
  ];
  for (const [answer, want] of cases) {
    answers = [answer];
    expect([answer, await writeLayout({ order: ['/srv/b', '/srv/a'] })]).toEqual([answer, want]);
  }
});

test('commitLayout resolves true when the write held, and shows it', async () => {
  answers = [{ ok: true, layout: { order: ['/srv/b', '/srv/a'] } }];
  expect(await commitLayout({ order: ['/srv/b', '/srv/a'] }, 'Moving the group')).toBe(true);
  expect(bridge.sidebarLayout.get().order).toEqual(['/srv/b', '/srv/a']);
  expect(alerts).not.toHaveBeenCalled();
});

test('commitLayout resolves false when the write failed and was put back', async () => {
  answers = [{ ok: true, layout: { order: ['/srv/a', '/srv/b'] } }];
  await commitLayout({ order: ['/srv/a', '/srv/b'] }, 'Moving the group');
  answers = [{ ok: false, error: 'disk full' }];
  const onRevert = vi.fn();
  expect(await commitLayout({ order: ['/srv/b', '/srv/a'] }, 'Moving the group', onRevert)).toBe(false);
  expect(bridge.sidebarLayout.get().order).toEqual(['/srv/a', '/srv/b']);
  expect(onRevert).toHaveBeenCalledTimes(1);
  expect(alerts.mock.calls).toEqual([['Moving the group failed: disk full. It was put back.']]);
});

test('commitLayout resolves false when it failed under a NEWER write (nothing put back, still not held)', async () => {
  answers = [{ ok: false, error: 'disk full' }, { ok: true, layout: { order: ['/srv/a', '/srv/b'] } }];
  const onRevert = vi.fn();
  const first = commitLayout({ order: ['/srv/b', '/srv/a'] }, 'Moving the group', onRevert);
  const second = commitLayout({ order: ['/srv/a', '/srv/b'] }, 'Moving the group');
  expect(await first).toBe(false);
  expect(await second).toBe(true);
  expect(onRevert).not.toHaveBeenCalled();
  expect(alerts.mock.calls).toEqual([
    ['Moving the group failed: disk full. A later change is shown; the next refresh reads the saved layout.']]);
});
