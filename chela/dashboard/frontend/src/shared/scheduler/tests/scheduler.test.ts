import { afterEach, beforeEach, expect, test, vi } from 'vitest';

type Mod = typeof import('../index.ts');
let fetches: string[];
let S: Mod;
let view: (typeof import('../../store/view.ts'))['view'];

// A fresh scheduler (its lanes and boot flag are module state) on a page whose boot block says
// whether terminals are on.
async function load(terminalsEnabled = true) {
  document.body.innerHTML =
    `<script type="application/json" id="chela-boot">${JSON.stringify({ terminalsEnabled })}</script>`;
  vi.resetModules();
  S = await import('../index.ts');
  ({ view } = await import('../../store/view.ts'));
}
const flush = () => new Promise((r) => setTimeout(r, 0));
function deferred() {
  let resolve!: (v?: unknown) => void;
  const promise = new Promise((r) => (resolve = r));
  return { promise, resolve };
}

beforeEach(async () => {
  fetches = [];
  vi.stubGlobal('fetch', (url: string) => {
    fetches.push(url);
    return Promise.resolve({ json: () => Promise.resolve({ '@1': { used_pct: 10 } }) });
  });
  await load();
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

// The sidebar's lane: it reads the pane list, then says so.
const paneLane = () => S.registerLane('sidebar', (t) => t.paneListKnown());

test('lanes are independent: one that throws (sync or async) is logged, the others still run', async () => {
  const error = vi.spyOn(console, 'error').mockImplementation(() => {});
  const ran: string[] = [];
  S.registerLane('sync', () => {
    throw new RangeError('sync');
  });
  S.registerLane('async', () => Promise.reject(new TypeError('Failed to fetch')));
  S.registerLane('ok', () => ran.push('ok'));
  paneLane();
  await S.refresh();
  expect(ran).toEqual(['ok']);
  expect(error.mock.calls).toEqual([
    ['Refresh error:', new RangeError('sync')],
    ['Refresh error:', new TypeError('Failed to fetch')],
  ]);
});

test('ONE /api/agents/context read per tick, the same promise for every lane and the view tick', async () => {
  const seen: unknown[] = [];
  S.registerLane('a', (t) => seen.push(t.context));
  S.registerLane('b', (t) => seen.push(t.context));
  S.registerLane('after', (t) => seen.push(t.context), 'settled');
  S.setViewTicks(() => (ctx) => seen.push(ctx));
  paneLane();
  await S.refresh();
  await flush();
  expect(fetches).toEqual(['/api/agents/context']);
  expect(seen).toHaveLength(4);
  expect(new Set(seen).size).toBe(1);
  await expect(seen[0]).resolves.toEqual({ '@1': { used_pct: 10 } });
});

test('with terminals off there is no context read: every lane gets null', async () => {
  await load(false);
  const seen: unknown[] = [];
  S.registerLane('a', (t) => seen.push(t.context));
  paneLane();
  await S.refresh();
  expect(fetches).toEqual([]);
  expect(seen).toEqual([null]);
});

test('the view tick waits for the pane list, not for the other lanes, and is the ACTIVE view\'s', async () => {
  const pane = deferred();
  const slow = deferred();
  const ticked: string[] = [];
  S.setViewTicks((id) => (id === 'terminals' ? () => ticked.push('wall') : id === 'work' ? () => ticked.push('work') : null));
  S.registerLane('sidebar', (t) => pane.promise.then(t.paneListKnown));
  S.registerLane('slow', () => slow.promise);
  view.set('terminals');
  const done = S.refresh();
  await flush();
  expect(ticked).toEqual([]);
  pane.resolve();
  await flush();
  expect(ticked).toEqual(['wall']);
  slow.resolve();
  await done;
  view.set('agent-detail');   // a view with no tick: nothing runs
  await S.refresh();
  expect(ticked).toEqual(['wall']);
});

test('boot is true on the first tick only', async () => {
  const boots: boolean[] = [];
  S.registerLane('a', (t) => boots.push(t.boot));
  paneLane();
  await S.refresh();
  await S.refresh();
  expect(boots).toEqual([true, false]);
});

test('settled lanes run once every tick lane has settled, rejected ones included; their throw is logged', async () => {
  const error = vi.spyOn(console, 'error').mockImplementation(() => {});
  const order: string[] = [];
  const slow = deferred();
  S.registerLane('slow', () => slow.promise.then(() => order.push('slow')));
  S.registerLane('fails', () => Promise.reject(new Error('x')));
  S.registerLane('context', () => {
    order.push('context');
    throw new SyntaxError('ctx');
  }, 'settled');
  paneLane();
  const done = S.refresh();
  await flush();
  expect(order).toEqual([]);
  slow.resolve();
  await done;
  expect(order).toEqual(['slow', 'context']);
  await flush();
  expect(error.mock.calls).toEqual([['Refresh error:', new Error('x')], ['Refresh error:', new SyntaxError('ctx')]]);
});

test('a detached lane is not awaited and its failure is swallowed', async () => {
  const error = vi.spyOn(console, 'error').mockImplementation(() => {});
  const never = deferred();
  let started = 0;
  S.registerLane('update', () => {
    started++;
    return never.promise;
  }, 'detached');
  S.registerLane('broken', () => Promise.reject(new Error('down')), 'detached');
  paneLane();
  await S.refresh();
  await flush();
  expect(started).toBe(1);
  expect(error).not.toHaveBeenCalled();
});

test('viewPainted settles once the view tick has, even when it rejects', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  const tick = deferred();
  let painted = false;
  S.setViewTicks(() => () => tick.promise.then(() => Promise.reject(new Error('wall'))));
  S.registerLane('update', (t) => t.viewPainted.then(() => (painted = true)), 'detached');
  paneLane();
  const done = S.refresh();
  await flush();
  expect(painted).toBe(false);
  tick.resolve();
  await done;
  await flush();
  expect(painted).toBe(true);
});

test('registering a lane name again replaces it', async () => {
  const ran: string[] = [];
  S.registerLane('summary', () => ran.push('old'));
  S.registerLane('summary', () => ran.push('new'));
  paneLane();
  await S.refresh();
  expect(ran).toEqual(['new']);
});

test('startScheduler ticks every interval, not before', async () => {
  vi.useFakeTimers();
  let ticks = 0;
  S.registerLane('a', () => ticks++);
  S.startScheduler(30000);
  await vi.advanceTimersByTimeAsync(29999);
  expect(ticks).toBe(0);
  await vi.advanceTimersByTimeAsync(1);
  expect(ticks).toBe(1);
  await vi.advanceTimersByTimeAsync(30000);
  expect(ticks).toBe(2);
});
