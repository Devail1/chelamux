// CMX-240 (CMX-212 P25): owner presence (static/collab/presence-owner.js) as the share code
// drives it: loaded once from its versioned static URL and wired once, started with the code
// a mint or an adopt already holds, stopped with the share, and following the agents poll only
// once loaded. The real module against a real relay socket: tests/presence_owner_rotation.
import { act } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';

const owner = vi.hoisted(() => ({
  initOwnerPresence: vi.fn(),
  startOwnerPresence: vi.fn(),
  stopOwnerPresence: vi.fn(),
  syncOwnerPresence: vi.fn(),
  loads: 0,
}));
vi.mock('/static/collab/presence-owner.js', () => {
  owner.loads++;
  return owner;
});

import { configureShareHost, followAgents, shares, shareSession, startShares, stopOwnerPresence, stopShare } from '../index.ts';
import { BOOT } from '../../../shared/api/index.ts';
import { INFO, resetShares, serve, settle } from './server.ts';
import { stubFetch } from './setup.ts';

beforeEach(() => {
  resetShares();
  configureShareHost({ paneTitle: (w) => w, paneFrames: () => [] });
  for (const f of [owner.initOwnerPresence, owner.startOwnerPresence, owner.stopOwnerPresence, owner.syncOwnerPresence]) f.mockReset();
});

test('⭐ the agents poll reaches presence only once it is loaded; then every row is synced', async () => {
  followAgents([{ window_id: '@1', shared: true, share_epoch: 2 }]);
  await settle();
  expect(owner.syncOwnerPresence).not.toHaveBeenCalled();
  expect(owner.loads).toBe(0);

  serve({ shared: {} });
  startShares();
  await settle();
  expect(owner.loads).toBe(1);
  expect(owner.initOwnerPresence).toHaveBeenCalledTimes(1);

  followAgents([{ window_id: '@1', shared: true, share_epoch: 2 }, { window_id: '@2', shared: 0 }, { shared: true }]);
  await settle();
  expect(owner.syncOwnerPresence.mock.calls).toEqual([
    ['@1', true, 2],
    ['@2', false, undefined],
  ]);
});

test('adopting a live share starts presence with the code in hand', async () => {
  serve({ info: { ...INFO, share_epoch: 5 } });
  await act(() => shareSession('@1'));
  await settle();
  expect(owner.startOwnerPresence.mock.calls).toEqual([['@1', INFO.join_url, INFO.pairing_code, 5]]);
});

test('a stop, or a pane going away, stops presence; a refused stop does not', async () => {
  serve({ shared: { '@1': { mode: 'view' } } });
  await stopShare('@1');
  stopOwnerPresence('@3');
  await settle();
  expect(owner.stopOwnerPresence.mock.calls).toEqual([['@1'], ['@3']]);
  serve({ stop: 'refuse' });
  await stopShare('@1');
  await settle();
  expect(owner.stopOwnerPresence).toHaveBeenCalledTimes(2);
});

test('the module is loaded and wired once per page', async () => {
  serve({ shared: {} });
  startShares();
  await settle();
  expect([owner.loads, owner.initOwnerPresence.mock.calls.length]).toEqual([1, 0]);
});

test('with terminals off, page load reads nothing and loads nothing', async () => {
  const calls = stubFetch(() => ({ '@1': { mode: 'view' } }));
  const loads = owner.loads;
  BOOT.terminalsEnabled = false;
  try {
    startShares();
    await settle();
  } finally {
    BOOT.terminalsEnabled = true;
  }
  expect(calls).toEqual([]);
  expect(shares.get().size).toBe(0);
  expect(owner.loads).toBe(loads);
});
