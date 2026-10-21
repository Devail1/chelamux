// CMX-240 (CMX-212 P25): a LIVE share's pure rules and the page's share state (ported from
// tests/share_dialog.test.mjs's time-left table and tests/share_mode_sheet.test.mjs's state
// invariants): the countdown, the pill's words, the upgrade body, and how the store follows
// the agents poll and /api/term/shared.
import { expect, test } from 'vitest';
import { applyShared, asMode, fmtShareEnd, markShared, noteShare, pillView, seedFromAgents, shareOffer, shares, shareTimeLeft, unsafeBannerText, upgradeBody, type ShareEntry } from '../index.ts';

const NOW = 1_700_000_000_000;
const at = (secs: number) => NOW / 1000 + secs;

test.each([
  [12 * 86400 + 4 * 3600 + 30, '12d 4h left'],
  [86400, '1d 0h left'],
  [3 * 3600 + 5 * 60 + 10, '3h 5m left'],
  [3600, '1h 0m left'],
  [25 * 60 + 10, '25m left'],
  [60, '1m left'],
  [20, '<1m left'],
  [-600, '<1m left'],
])('an expiry %i s away reads "%s"', (secs, want) => {
  expect(shareTimeLeft(at(secs), NOW)).toBe(want);
});

test('no expiry reads as nothing, and the banner then says no time', () => {
  expect(shareTimeLeft(null, NOW)).toBe('');
  expect(shareTimeLeft(undefined, NOW)).toBe('');
  expect(unsafeBannerText(null, NOW)).toBe('UNSANDBOXED: guest can type');
  expect(unsafeBannerText(at(25 * 60 + 10), NOW)).toBe('UNSANDBOXED: guest can type · 25m left');
});

test('an end today names the time; another day names the day too', () => {
  const t = (e: number) => new Date(e * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const noonish = new Date(2026, 9, 21, 12, 0).getTime();
  const today = noonish / 1000 + 3600;
  expect(fmtShareEnd(today, noonish)).toBe(`at ${t(today)}`);
  const later = noonish / 1000 + 3 * 86400;
  expect(fmtShareEnd(later, noonish)).toBe(`${new Date(later * 1000).toLocaleDateString([], { month: 'short', day: 'numeric' })}, ${t(later)}`);
});

const e = (mode: ShareEntry['mode'], expiresAt: number | null = null): ShareEntry => ({ mode, expiresAt });

test.each([
  [[], { count: 0, mode: 'view' }],
  [[e('view')], { count: 1, mode: 'view', text: '1 sharing', label: '1 active share (view only), tap to manage or stop' }],
  [[e('view'), e('typing')], { count: 2, mode: 'typing', text: '2 sharing', label: '2 active shares (typing allowed), tap to manage or stop' }],
  [
    [e('typing'), e('unsandboxed', at(3 * 3600 + 5 * 60 + 10))],
    { count: 2, mode: 'unsandboxed', text: 'UNSANDBOXED: guest can type · 3h 5m left', label: 'UNSANDBOXED: a guest can type into a real shell. 2 active shares (typing allowed), tap to manage or stop' },
  ],
])('the pill for %j: the most permissive mode wins', (entries, want) => {
  expect(pillView(entries, NOW)).toMatchObject(want);
});

const OFFER = shareOffer({ share_typing: true, sandboxed: false, unsandboxed_offered: true, window_name: 'w', unsandboxed_long_minutes: 240 });

test('the upgrade body: the typed name arms it, twice past 4 h, and it never carries `on`', () => {
  expect(upgradeBody(OFFER, { minutes: 30, confirm: 'x', confirmLong: '' })).toBeNull();
  expect(upgradeBody(OFFER, { minutes: 30, confirm: ' w ', confirmLong: '' })).toEqual({ mode: 'unsandboxed', minutes: 30, confirm: 'w' });
  expect(upgradeBody(OFFER, { minutes: 241, confirm: 'w', confirmLong: '' })).toBeNull();
  expect(upgradeBody(OFFER, { minutes: 241, confirm: 'w', confirmLong: 'w' })).toEqual({ mode: 'unsandboxed', minutes: 241, confirm: 'w', confirm_long: 'w' });
  expect(upgradeBody(shareOffer({ share_typing: true, sandboxed: true, window_name: 'w' }), { minutes: 30, confirm: 'w', confirmLong: '' })).toBeNull();
});

test('a missing or unknown mode reads as View only', () => {
  expect([asMode('typing'), asMode('unsandboxed'), asMode('view'), asMode(undefined), asMode('root'), asMode(1)]).toEqual(['typing', 'unsandboxed', 'view', 'view', 'view', 'view']);
});

const snapshot = () => Object.fromEntries(shares.get());

test('the agents poll: a shared row sets its mode, an unshared one drops it, other windows stay', () => {
  shares.set(new Map([['@9', e('typing')]]));
  seedFromAgents([{ window_id: '@1', shared: true, share_mode: 'typing' }, { window_id: '@2', shared: false }, { shared: true }]);
  expect(snapshot()).toEqual({ '@9': e('typing'), '@1': e('typing') });
  seedFromAgents([{ window_id: '@1', shared: true }]);
  expect(snapshot()['@1']).toEqual(e('view'));
  seedFromAgents([{ window_id: '@1', shared: false }]);
  expect(snapshot()).toEqual({ '@9': e('typing') });
});

test('the agents poll keeps a known expiry only while the share stays UNSANDBOXED', () => {
  shares.set(new Map([['@1', e('unsandboxed', 123)]]));
  seedFromAgents([{ window_id: '@1', shared: true, share_mode: 'unsandboxed' }]);
  expect(snapshot()['@1']).toEqual(e('unsandboxed', 123));
  seedFromAgents([{ window_id: '@1', shared: true, share_mode: 'view' }]);
  expect(snapshot()['@1']).toEqual(e('view'));
});

test('/api/term/shared: replace makes it the whole set, merge only adds', () => {
  shares.set(new Map([['@9', e('view')]]));
  applyShared({ '@1': { mode: 'unsandboxed', expires_at: 5 }, '@2': null }, false);
  expect(snapshot()).toEqual({ '@9': e('view'), '@1': e('unsandboxed', 5), '@2': e('view') });
  applyShared({ '@1': { mode: 'typing' } }, true);
  expect(snapshot()).toEqual({ '@1': e('typing') });
});

test('adopting keeps what is known; noting replaces it', () => {
  shares.set(new Map([['@1', e('typing')]]));
  markShared('@1');
  markShared('@2');
  expect(snapshot()).toEqual({ '@1': e('typing'), '@2': e('view') });
  noteShare('@1', 'unsandboxed', 7);
  noteShare('@2', 'view', null);
  expect(snapshot()).toEqual({ '@1': e('unsandboxed', 7), '@2': e('view') });
});

test('an update that changes nothing notifies nobody (the agents poll runs every 4 s)', () => {
  shares.set(new Map([['@1', e('typing')]]));
  let n = 0;
  const off = shares.subscribe(() => n++);
  seedFromAgents([{ window_id: '@1', shared: true, share_mode: 'typing' }]);
  applyShared({ '@1': { mode: 'typing' } }, true);
  markShared('@1');
  expect(n).toBe(0);
  noteShare('@1', 'typing', 9);
  seedFromAgents([{ window_id: '@2', shared: true }]);
  expect(n).toBe(2);
  off();
});

test('with several overrides the pill counts down to the one that lasts longest', () => {
  const short = e('unsandboxed', at(10 * 60 + 10));
  const long = e('unsandboxed', at(6 * 3600 + 10));
  expect(pillView([short, long], NOW).text).toBe('UNSANDBOXED: guest can type · 6h 0m left');
  expect(pillView([long, short], NOW).text).toBe('UNSANDBOXED: guest can type · 6h 0m left');
  expect(pillView([short, e('unsandboxed'), long], NOW).text).toBe('UNSANDBOXED: guest can type');
});
