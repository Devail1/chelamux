// CMX-240: owner presence is a nicety, never a gate. A module that fails to load is retried on
// the next use (not cached as failed), and a router that throws while wiring still leaves the
// module usable.
import { act } from '@testing-library/react';
import { expect, test, vi } from 'vitest';

const owner = vi.hoisted(() => ({
  attempt: 0,
  initOwnerPresence: vi.fn(() => {
    throw new Error('router');
  }),
  startOwnerPresence: vi.fn(),
  stopOwnerPresence: vi.fn(),
  syncOwnerPresence: vi.fn(),
}));
vi.mock('/static/collab/presence-owner.js', () => {
  if (++owner.attempt === 1) throw new Error('offline');
  return owner;
});

import { shareSession, stopOwnerPresence } from '../index.ts';
import { INFO, serve, settle } from './server.ts';

test('a failed load is retried; a throwing router still starts presence', async () => {
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  stopOwnerPresence('@1');
  await settle();
  expect(warn).toHaveBeenCalledTimes(1);
  expect(owner.stopOwnerPresence).not.toHaveBeenCalled();
  serve({ info: { ...INFO, share_epoch: 1 } });
  await act(() => shareSession('@1'));
  await settle();
  expect(owner.attempt).toBe(2);
  expect(owner.initOwnerPresence).toHaveBeenCalledTimes(1);
  expect(owner.startOwnerPresence.mock.calls).toEqual([['@1', INFO.join_url, INFO.pairing_code, 1]]);
});
