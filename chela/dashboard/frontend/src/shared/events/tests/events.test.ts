import { afterEach, expect, test, vi } from 'vitest';

class FakeSource {
  static last: FakeSource | null = null;
  listeners: Record<string, ((e: { data: unknown }) => void)[]> = {};
  onerror: unknown = null;
  constructor(public url: string) {
    FakeSource.last = this;
  }
  addEventListener(type: string, cb: (e: { data: unknown }) => void) {
    (this.listeners[type] ??= []).push(cb);
  }
  fire(type: string, data: unknown) {
    for (const cb of this.listeners[type] ?? []) cb({ data });
  }
}

async function load(path = '/') {
  window.history.replaceState({}, '', path);
  vi.resetModules();
  return import('../index.ts');
}
const winWith = (Source: unknown) => ({ EventSource: Source }) as unknown as Window;

afterEach(() => {
  vi.restoreAllMocks();
  FakeSource.last = null;
  window.history.replaceState({}, '', '/');
});

test('the event map is the server\'s seven events', async () => {
  const { EVENT_NAMES } = await load();
  expect([...EVENT_NAMES]).toEqual(['hello', 'windows', 'runs', 'log', 'term-ready', 'orchestrator', 'overrides']);
});

test('one stream on /api/events under the base path, listening for every event', async () => {
  const { openEvents } = await load('/chela/');
  const src = openEvents(winWith(FakeSource)) as unknown as FakeSource;
  expect(src).toBe(FakeSource.last);
  expect(src.url).toBe('/chela/api/events');
  expect(Object.keys(src.listeners).sort()).toEqual(
    ['hello', 'log', 'orchestrator', 'overrides', 'runs', 'term-ready', 'windows'],
  );
  expect(Object.values(src.listeners).map((l) => l.length)).toEqual([1, 1, 1, 1, 1, 1, 1]);
  expect(typeof src.onerror).toBe('function');
});

test('a frame reaches exactly its own event\'s subscribers, parsed; an unparsable one as null', async () => {
  const { onEvent, openEvents } = await load();
  const src = openEvents(winWith(FakeSource)) as unknown as FakeSource;
  const runs = vi.fn();
  const log = vi.fn();
  onEvent('runs', runs);
  onEvent('log', log);
  src.fire('runs', '{"runs": [{"task_id": "CMX-1"}]}');
  src.fire('runs', 'not json');
  expect(runs.mock.calls).toEqual([[{ runs: [{ task_id: 'CMX-1' }] }], [null]]);
  expect(log).not.toHaveBeenCalled();
});

test('a throwing subscriber is logged and never silences the next one', async () => {
  const { dispatch, onEvent } = await load();
  const error = vi.spyOn(console, 'error').mockImplementation(() => {});
  const after = vi.fn();
  onEvent('windows', () => {
    throw new RangeError('boom');
  });
  onEvent('windows', after);
  dispatch('windows', '{}');
  expect(after).toHaveBeenCalledWith({});
  expect(error.mock.calls).toEqual([['SSE windows handler:', new RangeError('boom')]]);
});

test('unsubscribing stops the handler; the others keep receiving', async () => {
  const { dispatch, onEvent } = await load();
  const a = vi.fn();
  const b = vi.fn();
  const offA = onEvent('hello', a);
  onEvent('hello', b);
  offA();
  dispatch('hello', 'null');
  expect([a.mock.calls.length, b.mock.calls.length]).toEqual([0, 1]);
});

test('no EventSource, or one that refuses to open, is polling-only: null, nothing thrown', async () => {
  const { openEvents } = await load();
  expect(openEvents(winWith(undefined))).toBeNull();
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  const err = new TypeError('blocked');
  expect(openEvents(winWith(function Refuse() { throw err; }))).toBeNull();
  expect(warn.mock.calls).toEqual([['SSE unavailable, polling-only:', err]]);
});
