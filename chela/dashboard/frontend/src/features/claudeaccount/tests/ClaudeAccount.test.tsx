// CMX-395: the header's Claude account line. Every state the server answers renders as
// itself; a missing, malformed or failed read renders "account: unknown", never a blank.
// Placeholders only (public repo): *.invalid addresses.
import { act, cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import {
  ACCOUNT_POLL_MS, ClaudeAccount, accountLabel, accountLane, accountView, claudeAccount, left, resetClaudeAccount,
  switchBadge, tickClaudeAccount, utc,
} from '../index.ts';

const A = 'person-a@example.invalid';
const B = 'person-b@example.invalid';

let reply: () => Promise<unknown>;
let urls: string[];
beforeEach(() => {
  resetClaudeAccount();
  urls = [];
  reply = () => Promise.resolve({ state: 'ok', email: A });
  vi.stubGlobal('fetch', (url: string) => {
    urls.push(url);
    return reply().then((body) => ({ json: () => Promise.resolve(body) }));
  });
  render(<ClaudeAccount />);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const line = () => document.getElementById('claude-account') as HTMLElement;
const words = () => line().querySelector('.ca-text')?.textContent;
const warn = () => line().querySelector('.ca-warn')?.textContent ?? null;
async function answer(body: unknown) {
  reply = () => Promise.resolve(body);
  await act(() => tickClaudeAccount());
}

const OK = {
  state: 'ok', email: A, org_name: 'Example Org', plan: 'team', auth_method: 'claude.ai',
  expires_at: '2027-01-15T12:00:00+00:00', expires_in_s: 58 * 86400 + 5, warn_days: 3, change: null,
};

test('before the first answer it says it is checking, not a blank', () => {
  expect(line().dataset.tone).toBe('pending');
  expect(words()).toBe('account: checking');
});

test('logged in: the account and the org, hover for plan, method and expiry', async () => {
  await answer(OK);
  expect(line().dataset.tone).toBe('ok');
  expect(line().className).toBe('claude-account ca-ok');
  expect(words()).toBe('person-a@… · Example Org');
  expect(warn()).toBeNull();
  expect(line().querySelector('svg.lucide-circle-user')).toBeTruthy();
  expect(line().title).toBe([
    `Claude account: ${A}`, 'Org: Example Org', 'Plan: team', 'Login method: claude.ai',
    'Login expires: 2027-01-15 12:00 UTC (in 58d)',
  ].join('\n'));
});

test('logged in without an org: the whole email, unknown fields say unknown', async () => {
  await answer({ state: 'ok', email: A });
  expect(words()).toBe(A);
  expect(line().title).toBe([`Claude account: ${A}`, 'Plan: unknown', 'Login method: unknown', 'Login expiry: unknown'].join('\n'));
});

test('logged out: a clear "Claude not logged in"', async () => {
  await answer({ state: 'logged_out' });
  expect(line().dataset.tone).toBe('out');
  expect(words()).toBe('Claude not logged in');
  expect(line().querySelector('svg.lucide-triangle-alert')).toBeTruthy();
  expect(line().title).toBe('claude auth status: not logged in. Agents fail until you run claude auth login.');
});

test('expiring: the account stays, with the warning beside it', async () => {
  await answer({ ...OK, state: 'expiring', expires_in_s: 2 * 86400 + 100 });
  expect(line().dataset.tone).toBe('warn');
  expect(words()).toBe('person-a@… · Example Org');
  expect(warn()).toBe('login expires in 2d');
  expect(line().querySelector('svg.lucide-triangle-alert')).toBeTruthy();
  expect(line().title).toContain('Login expires: 2027-01-15 12:00 UTC (in 2d)');
  expect(line().title).toContain('Run claude auth login before it lapses');
  await answer({ ...OK, state: 'expiring', expires_in_s: 5 * 3600 });
  expect(warn()).toBe('login expires in 5h');
  await answer({ ...OK, state: 'expiring', expires_in_s: -10 });
  expect(warn()).toBe('login expired');
  expect(line().title).toContain('Login expires: 2027-01-15 12:00 UTC (expired)');
  await answer({ ...OK, state: 'expiring', expires_in_s: 0 });
  expect(warn()).toBe('login expired');
  await answer({ ...OK, state: 'expiring', expires_in_s: null });
  expect(warn()).toBe('login expiring');
});

test('an "ok" answer with time left is never a warning', async () => {
  await answer({ ...OK, expires_in_s: 60 });
  expect(warn()).toBeNull();
  expect(line().dataset.tone).toBe('ok');
});

test('the CLI missing or failing reads "account: unknown" with its reason', async () => {
  await answer({ state: 'unknown', reason: 'claude CLI not found' });
  expect(line().dataset.tone).toBe('unknown');
  expect(words()).toBe('account: unknown');
  expect(line().querySelector('svg.lucide-triangle-alert')).toBeTruthy();
  expect(line().title).toBe('Claude account unknown: claude CLI not found. Run claude auth status to check.');
  await answer({ state: 'unknown' });
  expect(line().title).toBe('Claude account unknown: no reason given. Run claude auth status to check.');
});

test.each([
  [null, 'no answer'], [[], 'no answer'], ['ok', 'no answer'], [{}, 'an answer this page does not know'],
  [{ state: 'fine', email: A }, 'an answer this page does not know'],
  [{ state: 'ok' }, 'no account email'], [{ state: 'ok', email: '  ' }, 'no account email'],
  [{ state: 'expiring', email: 7 }, 'no account email'],
])('a malformed answer %j reads unknown, never fine', async (body, why) => {
  await answer(body);
  expect(words()).toBe('account: unknown');
  expect(line().title).toBe(`Claude account unknown: ${why}. Run claude auth status to check.`);
});

test('the dashboard not answering, before any answer: unknown, and logged', async () => {
  const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
  reply = () => Promise.reject(new Error('down'));
  await act(() => tickClaudeAccount());
  expect(words()).toBe('account: unknown');
  expect(line().title).toBe('Claude account unknown: the dashboard did not answer. Run claude auth status to check.');
  expect(errors).toHaveBeenCalledWith('tickClaudeAccount', expect.any(Error));
});

test('the dashboard not answering after an answer keeps the last answer', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  await answer(OK);
  reply = () => Promise.reject(new Error('down'));
  await act(() => tickClaudeAccount());
  expect(words()).toBe('person-a@… · Example Org');
});

test('an observed switch is in the hover', async () => {
  await answer({ ...OK, email: B, change: { from: A, to: B, at: '2026-11-17T10:00:00+00:00' } });
  expect(line().title).toContain(`Account switched from ${A} to ${B} at 2026-11-17 10:00 UTC`);
});

// --- the poll: on load (fresh), then every ACCOUNT_POLL_MS, never per tick -------------

test('the boot tick reads fresh; later ticks only after the poll period', async () => {
  expect(ACCOUNT_POLL_MS).toBe(300_000);
  await act(() => accountLane({ boot: true }, 1_000_000));
  expect(urls).toEqual(['/api/claude-account?fresh=1']);
  expect(accountLane({ boot: false }, 1_000_000 + 30_000)).toBeUndefined();
  expect(accountLane({ boot: false }, 1_000_000 + 299_999)).toBeUndefined();
  expect(urls).toHaveLength(1);
  await act(() => accountLane({ boot: false }, 1_000_000 + 300_000));
  expect(urls).toEqual(['/api/claude-account?fresh=1', '/api/claude-account']);
  expect(accountLane({ boot: false }, 1_000_000 + 300_001)).toBeUndefined();
});

test('a failed read is retried on the next tick, not after the poll period', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  reply = () => Promise.reject(new Error('down'));
  expect(await act(() => accountLane({ boot: true }, 1_000_000))).toBe(false);
  reply = () => Promise.resolve(OK);
  expect(await act(() => accountLane({ boot: false }, 1_000_000 + 30_000))).toBe(true);
  expect(urls).toEqual(['/api/claude-account?fresh=1', '/api/claude-account']);
  expect(words()).toBe('person-a@… · Example Org');
  expect(accountLane({ boot: false }, 1_000_000 + 60_000)).toBeUndefined();
});

test('a page that missed the boot still reads on its first tick', async () => {
  await act(() => accountLane({ boot: false }, 5));
  expect(urls).toEqual(['/api/claude-account']);
});

// --- the pure helpers --------------------------------------------------------------------

test('accountLabel: name@… · Org with an org, the email without', () => {
  expect(accountLabel(A, 'Org')).toBe('person-a@… · Org');
  expect(accountLabel(A, '')).toBe(A);
  expect(accountLabel('no-at-sign', 'Org')).toBe('no-at-sign · Org');
  expect(accountLabel('@lead', 'Org')).toBe('@lead · Org');
});

test('left and utc', () => {
  expect(left(86400)).toBe('1d');
  expect(left(86399)).toBe('24h');
  expect(left(3601)).toBe('2h');
  expect(left(10)).toBe('1h');
  expect(utc('2027-01-15T12:34:56Z')).toBe('2027-01-15 12:34 UTC');
  expect(utc('nope')).toBe('');
  expect(utc(5)).toBe('');
});

test('accountView of undefined is pending, of null unknown', () => {
  expect(accountView(undefined).tone).toBe('pending');
  expect(accountView(null).tone).toBe('unknown');
});

test('switchBadge: may still use the old account, never "uses"', () => {
  expect(switchBadge({ from: A, to: B, at: '2026-11-17T10:00:00+00:00', started_unknown: false })).toEqual({
    text: A,
    title: `May still use ${A}: it started before the switch to ${B} at 2026-11-17 10:00 UTC. Restart it to be sure.`,
  });
  expect(switchBadge({ from: A, to: B, at: '2026-11-17T10:00:00+00:00', started_unknown: true })?.title).toBe(
    `May still use ${A}: its start time is unknown, and the account switched to ${B} at 2026-11-17 10:00 UTC. Restart it to be sure.`);
  expect(switchBadge({ from: A, to: B, at: '' })?.title).toContain('at an unknown time');
  for (const v of [null, undefined, 'x', {}, { from: A }, { to: B }, { from: '', to: B }]) expect(switchBadge(v)).toBeNull();
});

test('the store is the one the line reads', async () => {
  await act(async () => { claudeAccount.set({ state: 'logged_out' }); });
  expect(words()).toBe('Claude not logged in');
});
