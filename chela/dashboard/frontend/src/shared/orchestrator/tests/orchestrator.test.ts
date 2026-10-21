// Who holds the decisions inbox (static/js/orchestrator.js until CMX-228; ported from
// tests/orchestrator.test.mjs). The Wall's pane toggle and the boot seed are driven on the
// real page by tests/orchestrator_ui.test.mjs and orchestrator_seed.test.mjs.
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { dispatch } from '../../events/index.ts';
import { refresh, registerLane } from '../../scheduler/index.ts';
import { orchestrator } from '../../store/orchestrator.ts';
import { onOrchestratorChange, orchestratorRelease, orchestratorState, orchestratorSubscribe, refreshOrchestratorStatus } from '../index.ts';

let responses: Record<string, unknown>;
let calls: { url: string; init?: RequestInit }[];
const OWNER = { ok: true, wid: '@3', name: 'agent-a', state: 'ok', why: '', queued: 0 };
const NOBODY = { wid: null, name: null, state: 'unregistered', why: '', queued: 0 };

beforeEach(() => {
  orchestrator.set(NOBODY);
  responses = {};
  calls = [];
  vi.stubGlobal('fetch', (url: string, init?: RequestInit) => {
    calls.push(init ? { url, init } : { url });
    if (!(url in responses)) return Promise.reject(new Error(`network down: ${url}`));
    return Promise.resolve({ ok: true, json: () => Promise.resolve(responses[url]) });
  });
});
afterEach(() => vi.unstubAllGlobals());

const flush = () => new Promise((r) => setTimeout(r, 0));

test('the state starts as "nobody"', () => {
  expect(orchestratorState()).toEqual(NOBODY);
});

test('subscribing POSTs the wid and applies the server response as the new shared state', async () => {
  responses['/api/orchestrator/subscribe'] = OWNER;
  const result = await orchestratorSubscribe('@3');
  expect(result).toEqual(OWNER);
  expect(orchestratorState()).toEqual(OWNER);
  expect(calls).toEqual([{ url: '/api/orchestrator/subscribe', init: { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{"wid":"@3"}' } }]);
});

test('a REFUSED subscribe (dead window) never overwrites the last-known-good state', async () => {
  responses['/api/orchestrator/subscribe'] = OWNER;
  await orchestratorSubscribe('@3');
  responses['/api/orchestrator/subscribe'] = { ok: false, error: 'no such window: @999' };
  expect(await orchestratorSubscribe('@999')).toEqual({ ok: false, error: 'no such window: @999' });
  expect(orchestratorState().wid).toBe('@3');
});

test('releasing POSTs to release (null wid included); a refusal leaves the state alone', async () => {
  responses['/api/orchestrator/subscribe'] = OWNER;
  await orchestratorSubscribe('@3');
  responses['/api/orchestrator/release'] = { ok: false, wid: '@4', orchestrator: '@3' };
  expect((await orchestratorRelease('@4')).ok).toBe(false);
  expect(orchestratorState().wid).toBe('@3');
  responses['/api/orchestrator/release'] = { ok: true, ...NOBODY };
  expect((await orchestratorRelease(null)).ok).toBe(true);
  expect(calls.at(-1)?.init?.body).toBe('{"wid":null}');
  expect(orchestratorState().wid).toBeNull();
});

test('a failed or empty write answers ok:false with why, and changes nothing', async () => {
  responses['/api/orchestrator/subscribe'] = OWNER;
  await orchestratorSubscribe('@3');
  delete responses['/api/orchestrator/subscribe'];
  expect(await orchestratorSubscribe('@4')).toEqual({ ok: false, error: 'Error: network down: /api/orchestrator/subscribe' });
  responses['/api/orchestrator/release'] = null;
  expect(await orchestratorRelease('@3')).toEqual({ ok: false, error: 'empty response' });
  expect(orchestratorState().wid).toBe('@3');
});

test('a status read applies an object; a failed read, an array or a non-object keeps the state', async () => {
  responses['/api/orchestrator/status'] = OWNER;
  expect(await refreshOrchestratorStatus()).toEqual(OWNER);
  for (const bad of [[OWNER], 'ok', null]) {
    responses['/api/orchestrator/status'] = bad;
    expect(await refreshOrchestratorStatus()).toEqual(OWNER);
  }
  delete responses['/api/orchestrator/status'];
  expect(await refreshOrchestratorStatus()).toEqual(OWNER);
});

test('every listener hears a change, one bad listener does not silence the rest, and off() stops it', async () => {
  const heard: (string | null)[] = [];
  const err = vi.spyOn(console, 'error').mockImplementation(() => {});
  const off1 = onOrchestratorChange(() => {
    throw new Error('boom');
  });
  const off2 = onOrchestratorChange((s) => heard.push(s.wid));
  responses['/api/orchestrator/subscribe'] = { ...OWNER, wid: '@9' };
  await orchestratorSubscribe('@9');
  expect(heard).toEqual(['@9']);
  expect(err).toHaveBeenCalledWith('orchestrator listener', expect.any(Error));
  off1();
  off2();
  responses['/api/orchestrator/subscribe'] = { ...OWNER, wid: '@10' };
  await orchestratorSubscribe('@10');
  expect(heard).toEqual(['@9']);
});

test.each(['orchestrator', 'hello'] as const)('an SSE `%s` frame refetches the status', async (name) => {
  responses['/api/orchestrator/status'] = { ...OWNER, wid: '@5' };
  dispatch(name, null);
  await flush();
  expect(calls.map((c) => c.url)).toEqual(['/api/orchestrator/status']);
  expect(orchestratorState().wid).toBe('@5');
});

test('every refresh() tick reads the status: its own lane, whatever view is on screen', async () => {
  responses['/api/orchestrator/status'] = { ...OWNER, wid: '@6' };
  responses['/api/agents/context'] = {};
  registerLane('pane-list', (t) => t.paneListKnown()); // the tick waits for the pane list
  await refresh();
  expect(calls.some((c) => c.url === '/api/orchestrator/status')).toBe(true);
  expect(orchestratorState().wid).toBe('@6');
});
