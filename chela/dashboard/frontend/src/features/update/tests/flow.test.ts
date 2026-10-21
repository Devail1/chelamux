// CMX-95: what follows an /api/update/apply, on its exact boundaries (ported from
// tests/settings_update.test.mjs's state-machine half and update_banner_stale's staleOffer
// cases by CMX-219). A click always ends in an outcome; unknown is never success.
import { afterEach, expect, test, vi } from 'vitest';
import { FOLLOW_TIMEOUT_MS, POLL_MS, followUpdate, staleOffer, updateOutcome, type UpdateStatus } from '../flow.ts';

const BOOT = 'boot-1';
const status = (extra: UpdateStatus = {}): UpdateStatus => ({ ok: true, behind: 0, stale_ok: true, stale_services: [], boot: BOOT, applying: false, last_result: null, ...extra });
const CTX = { run: 4, boot: BOOT, label: 'Restarting chela-daemon', startedAt: 1000 };
const done = { run: 4, ok: true, step: 'done', error: '', restarted: ['chela-daemon'], behind_before: 0 };

afterEach(() => {
  vi.unstubAllGlobals();
});

test('the documented constants', () => {
  expect([POLL_MS, FOLLOW_TIMEOUT_MS]).toEqual([2000, 180000]);
});

test('timeout: 179.9 s still polls, 180 s gives up, and never as success', () => {
  const st = status({ applying: true });
  expect(updateOutcome(st, CTX, 1000 + 179900)).toEqual({ state: 'polling', text: 'Restarting chela-daemon…' });
  expect(updateOutcome(st, CTX, 1000 + 180000)).toEqual({ state: 'timeout', text: "Couldn't confirm the restart. Check `pm2 ls`." });
  expect(updateOutcome(null, CTX, 1000 + 180000).state).toBe('timeout');
});

test("done needs applying === false AND this run's result", () => {
  expect(updateOutcome(status({ last_result: done }), CTX, 1000)).toEqual({ state: 'done', text: 'Restarted chela-daemon' });
  for (const applying of [true, undefined]) expect(updateOutcome(status({ applying, last_result: done }), CTX, 1000).state).toBe('polling');
  expect(updateOutcome(status({ last_result: { ...done, run: 3 } }), CTX, 1000).state).toBe('polling');
  expect(updateOutcome(status({ last_result: done }), CTX, 1000 + 180000).state).toBe('done');
  expect(updateOutcome(status({ last_result: { ...done, restarted: [] } }), CTX, 1000).text).toBe('Done. No running service needed a restart');
  expect(updateOutcome(status({ last_result: { ...done, restarted: ['b', 'a'], behind_before: 2 } }), CTX, 1000).text).toBe('Pulled 2 commit(s). Restarted a, b');
});

test('an unknown result (ok missing / not true) is failed, never success', () => {
  for (const ok of [undefined, null, 'true', 1]) {
    const step = updateOutcome(status({ last_result: { run: 4, ok: ok as unknown as boolean, step: 'pm2-restart' } }), CTX, 1000);
    expect(step, String(ok)).toEqual({ state: 'failed', text: 'Update failed at pm2-restart: no detail' });
  }
  expect(updateOutcome(status({ last_result: { run: 4, ok: false, step: 'git-pull', error: 'diverged' } }), CTX, 1000).text).toBe('Update failed at git-pull: diverged');
});

test('reload only on a CHANGED boot: a missing boot on either side is not a restart', () => {
  expect(updateOutcome(status({ boot: 'boot-2', applying: true }), CTX, 1000)).toEqual({ state: 'reload', text: 'The dashboard restarted, reloading…' });
  expect(updateOutcome(status({ boot: BOOT, applying: true }), CTX, 1000).state).toBe('polling');
  expect(updateOutcome(status({ boot: undefined, applying: true }), CTX, 1000).state).toBe('polling');
  expect(updateOutcome(status({ boot: 'boot-2', applying: true }), { ...CTX, boot: undefined }, 1000).state).toBe('polling');
});

test('followUpdate polls every 2 s from the click, says each step, and stops on the outcome', async () => {
  let t = 0;
  const sleeps: number[] = [];
  const steps: string[] = [];
  const seq: (UpdateStatus | Error)[] = [status({ applying: true }), new Error('down mid-restart'), status({ last_result: done })];
  const step = await followUpdate({ run: 4, boot: BOOT, detail: 'restarting chela-daemon' }, (s) => steps.push(`${s.state}:${s.text}`), {
    fetchStatus: async () => {
      const next = seq.shift();
      if (next instanceof Error) throw next;
      return next;
    },
    now: () => t,
    sleep: async (ms) => {
      sleeps.push(ms);
      t += ms;
    },
    reload: () => expect.fail('reloaded'),
  });
  expect(sleeps).toEqual([2000, 2000, 2000]);
  expect(steps).toEqual(['polling:Restarting chela-daemon…', 'polling:Restarting chela-daemon…', 'done:Restarted chela-daemon']);
  expect(step.state).toBe('done');
});

test('followUpdate reloads the page when THIS dashboard restarted, and labels a missing detail', async () => {
  const reload = vi.fn();
  const steps: string[] = [];
  const step = await followUpdate({ run: 4, boot: BOOT }, (s) => steps.push(s.text), { fetchStatus: async () => status({ boot: 'boot-2' }), sleep: async () => {}, reload });
  expect(step.state).toBe('reload');
  expect(reload).toHaveBeenCalledTimes(1);
  const label: string[] = [];
  await followUpdate({ run: 9, boot: BOOT }, (s) => label.push(s.text), { fetchStatus: async () => status({ applying: true }), sleep: async () => {}, now: (() => { let n = 0; return () => (n += 100000); })() });
  expect(label[0]).toBe('Restarting…');
});

test('followUpdate fetches /api/update/status by default', async () => {
  const urls: string[] = [];
  vi.stubGlobal('fetch', (url: string) => {
    urls.push(url);
    return Promise.resolve({ json: () => Promise.resolve(status({ last_result: done })) });
  });
  const step = await followUpdate({ run: 4, boot: BOOT }, () => {}, { sleep: async () => {} });
  expect(step.state).toBe('done');
  expect(urls).toEqual(['/api/update/status']);
});

const STALE = status({ stale_services: ['chela-collab', 'chela-telegram'] });

test('staleOffer: an affirmative, readable stale list offers; anything unknown offers nothing', () => {
  expect(staleOffer(STALE)).toEqual({ names: ['chela-collab', 'chela-telegram'], key: 'chela-collab,chela-telegram', text: '2 services run old code', label: 'Restart' });
  expect(staleOffer({ ...STALE, stale_services: ['chela-collab'] })?.text).toBe('1 service runs old code');
  expect(staleOffer({ ...STALE, behind: 1 })?.label).toBe('Update & restart');
  for (const st of [{ ...STALE, stale_services: [] }, null, { ...STALE, ok: false }, { ...STALE, ok: undefined }, { ...STALE, stale_ok: false },
    { ...STALE, stale_ok: undefined }, { ...STALE, stale_services: undefined }, { ...STALE, stale_services: 'chela-x' }]) {
    expect(staleOffer(st), JSON.stringify(st)).toBeNull();
  }
});
