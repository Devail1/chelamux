// CMX-380: what an optimistic layout write RESOLVES to, through the real controller against a
// stubbed /api (Pin is the action that hands the result back): the failure in words (the
// server's `error`, else that it refused, else that it did not answer), and "did the write
// hold" (true only when it did; false on a revert and on a failure a newer write covered).
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { installBridge } from '../../../shared/bridge/index.ts';
import { refreshLayout, renderSidebarAgents, rowMenuAction } from '../controller.ts';
import { type AgentRow, buildItems } from '../model.ts';
import { win } from './fixtures.ts';

const bridge = installBridge(window);

let server: Record<string, unknown> = {};
/** What the next layout POSTs answer, in order (the last one repeats); 'down' rejects. */
let answers: unknown[] = [];
const alerts = vi.fn();

const ROWS: AgentRow[] = [win('p', { session_id: 'S-P' }), win('q', { session_id: 'S-Q' })];

beforeEach(async () => {
  document.body.innerHTML = '<div id="sidebar-agents"></div>';
  server = { order: [], pins: [] };
  answers = [];
  alerts.mockReset();
  vi.stubGlobal('alert', alerts);
  vi.stubGlobal('fetch', (url: string, opts?: RequestInit) => {
    const method = opts?.method ?? 'GET';
    if (url.endsWith('/api/sidebar/layout') && method === 'GET') {
      return Promise.resolve({ json: () => Promise.resolve({ layout: server }) });
    }
    if (url.endsWith('/api/sidebar/layout') && method === 'POST') {
      const body = JSON.parse(String(opts?.body)) as Record<string, unknown>;
      const a = answers.length > 1 ? answers.shift() : answers[0];
      if (a === 'down') return Promise.reject(new Error('offline'));
      if (a === 'saved') {
        server = { ...server, ...body };
        return Promise.resolve({ json: () => Promise.resolve({ ok: true, layout: server }) });
      }
      return Promise.resolve({ json: () => Promise.resolve(a) });
    }
    return Promise.reject(new Error(`unexpected ${method} ${url}`));
  });
  await refreshLayout(); // loaded: a write is sent, not only kept
  bridge.agents.set(ROWS as never);
  renderSidebarAgents(ROWS);
});

afterEach(() => vi.unstubAllGlobals());

const keyOf = (n: string) => buildItems(ROWS).find(it => it.agent?.name === n)?.key ?? '';
const pin = (n: string) => rowMenuAction(keyOf(n), 'pin') as Promise<boolean>;
const pins = () => bridge.sidebarLayout.get().pins;

test('a refusal is named by the server\'s error, else as refused; never a blank reason', async () => {
  const cases: [unknown, string][] = [
    // `ok: false` alone is a refusal, with no error to quote
    [{ ok: false }, 'the dashboard refused it'],
    // an `error` alone is one too, and it is quoted
    [{ error: 'disk full' }, 'disk full'],
    [{ ok: false, error: 'disk full' }, 'disk full'],
    // an EMPTY (or non-text) error says nothing: the generic words
    [{ ok: false, error: '' }, 'the dashboard refused it'],
    [{ error: '' }, 'the dashboard refused it'],
    [{ ok: false, error: 7 }, 'the dashboard refused it'],
    ['down', 'the dashboard did not answer'],
  ];
  for (const [answer, why] of cases) {
    alerts.mockReset();
    answers = [answer];
    expect([answer, await pin('p')]).toEqual([answer, false]);
    expect(pins()).toEqual([]);
    expect([answer, alerts.mock.calls]).toEqual([answer, [[`Pinning the session failed: ${why}. It was put back.`]]]);
  }
});

test('a held write resolves true and is shown; an answer with no layout still held', async () => {
  answers = ['saved'];
  expect(await pin('p')).toBe(true);
  expect(pins()).toEqual(['s:S-P']);
  answers = [{ ok: true }];
  expect(await pin('q')).toBe(true);
  expect(pins()).toEqual(['s:S-P', 's:S-Q']);
  expect(alerts).not.toHaveBeenCalled();
});

test('a write that failed under a NEWER one resolves false, puts nothing back, and says so', async () => {
  answers = [{ ok: false, error: 'disk full' }, 'saved'];
  const first = pin('p');
  const second = pin('q');
  expect(await first).toBe(false);
  expect(await second).toBe(true);
  // the newer write keeps the screen: the failed pin is not taken back over it
  expect(pins()).toEqual(['s:S-P', 's:S-Q']);
  expect(alerts.mock.calls).toEqual([
    ['Pinning the session failed: disk full. A later change is shown; the next refresh reads the saved layout.']]);
});
