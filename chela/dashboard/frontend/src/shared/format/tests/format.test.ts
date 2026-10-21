// The dashboard's time and schedule labels (moved from util.js, CMX-231).
import { expect, test } from 'vitest';
import { humanSchedule, relativeTime, shortTime } from '../index.ts';

test('shortTime: HH:MM Mon D in 24h; "-" with no time; the input back when unparsable', () => {
  const iso = new Date(2026, 8, 9, 7, 5).toISOString();
  expect(shortTime(iso)).toBe('07:05 Sep 9');
  expect(shortTime(new Date(2026, 0, 1, 23, 59).toISOString())).toBe('23:59 Jan 1');
  expect([shortTime(null), shortTime(''), shortTime(undefined)]).toEqual(['-', '-', '-']);
  expect(shortTime('not a date')).toBe('not a date');
});

test('relativeTime: s / m / h / d ahead, overdue once past, rounding at each boundary', () => {
  const now = Date.UTC(2026, 0, 1);
  const at = (s: number) => new Date(now + s * 1000).toISOString();
  expect([at(0), at(59), at(60), at(3599), at(3600), at(86399), at(86400), at(3 * 86400)].map((t) => relativeTime(t, now)))
    .toEqual(['0s', '59s', '1m', '60m', '1h', '24h', '1d', '3d']);
  expect(relativeTime(at(-1), now)).toBe('overdue');
  expect([relativeTime(null), relativeTime('')]).toEqual(['-', '-']);
  expect(relativeTime('nope', now)).toBe('nope');
});

test('humanSchedule: interval, once, unknown types and malformed cron', () => {
  expect(humanSchedule('interval', '5m')).toBe('every 5m');
  expect(humanSchedule('once', '2026-01-15T09:00')).toBe('once');
  expect(humanSchedule('weekly', 'mon')).toBe('weekly mon');
  expect(humanSchedule('cron', '0 9 * *')).toBe('cron 0 9 * *');
});

test.each([
  ['*/15 * * * *', 'every 15m'],
  ['5 * * * *', 'every 1h at :05'],
  ['30 */8 * * *', 'every 8h at :30'],
  ['0 9 * * *', 'daily at 09:00'],
  ['7 9 * * 1', 'daily at 09:07'],
  ['0 16,0,8 * * *', 'every 8h at :00'],
  ['15 9,13,20 * * *', '3x daily at 09:15, 13:15, 20:15'],
  ['15 9,13 * * 1', 'cron 15 9,13 * * 1'],
  ['0,20,40 * * * *', 'every 20m'],
  ['5,7,30 * * * *', 'at :05,:07,:30'],
  ['0 9 1 * *', 'daily at 09:00'],
  ['a b c d e', 'cron a b c d e'],
])('humanSchedule cron %s → %s', (value, want) => {
  expect(humanSchedule('cron', value)).toBe(want);
});
