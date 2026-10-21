// CMX-7: the operator's access-request pill and sheet (ported from tests/access_requests.test.mjs
// by CMX-219). The pill shows only while a request is PENDING; a refused request says why and
// offers only Deny; Approve defaults to the server's duration, read-only, and "allow write" is
// offered only when the guest asked for it, starting off; with Guest typing off Approve is
// disabled; an approved request shows its time left and a Revoke.
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { AccessRequestsPill, AccessRequestsSheet, accessRequests, closeAccessRequests, openAccessRequests, tickAccessRequests } from '../index.ts';
import { DURATIONS, durLabel, durationChoices, pendingCount, timeLeft, who, type AccessRequest } from '../model.ts';

let listing: unknown;
let posts: { path: string; body: { minutes?: number; rw?: boolean } }[] = [];
let postReply: unknown;
let fail = false;

beforeEach(() => {
  listing = { requests: [], share_typing: true, default_minutes: 60 };
  posts = [];
  postReply = { ok: true };
  fail = false;
  vi.stubGlobal('fetch', (url: string, opts?: RequestInit) => {
    if (fail) return Promise.reject(new Error('network down'));
    let body = listing;
    if (opts?.method === 'POST') {
      posts.push({ path: url, body: JSON.parse(String(opts.body || '{}')) });
      body = postReply;
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
  });
  accessRequests.set({ rows: [], shareTyping: false, defaultMinutes: 60 });
  render(<><AccessRequestsPill /><AccessRequestsSheet /></>);
});
afterEach(() => {
  act(() => closeAccessRequests());
  cleanup();
  vi.unstubAllGlobals();
});

const pending = (over: Partial<AccessRequest> = {}): AccessRequest => ({ id: 'abcdefabcdef-0', sid: 'abcdefabcdef', kind: 'mount', access: 'ro', status: 'pending',
  window: 'sandbox-1', description: 'mount /srv/data (read-only)', reason: 'need the dataset', refusal: null, ...over });
const pill = () => document.getElementById('btn-access-req') as HTMLButtonElement;
const rows = () => [...document.querySelectorAll('.access-req-sheet .ar-row')] as HTMLElement[];
const tick = () => act(() => tickAccessRequests());
const open = () => act(() => openAccessRequests());
async function click(el: Element | null | undefined) {
  if (!el) throw new Error('nothing to click');
  await act(async () => {
    fireEvent.click(el);
    await new Promise((r) => setTimeout(r, 0));
  });
}
function pickDuration(row: HTMLElement, label: string) {
  fireEvent.keyDown(within(row).getByRole('combobox', { name: 'How long the approval lasts' }), { key: 'Enter' });
  fireEvent.click(screen.getByRole('option', { name: label }));
}

test('the pill shows only while a request is PENDING, with the count in its text and its name', async () => {
  await tick();
  expect(pill().hidden).toBe(true);
  listing = { requests: [pending(), pending({ id: 'x-1' }), pending({ id: 'x-2', status: 'denied' })], share_typing: true };
  await tick();
  expect(pill().hidden).toBe(false);
  expect(pill().textContent).toBe(' 2 access requests');
  expect(pill().getAttribute('aria-label')).toBe('2 sandboxed-session access requests waiting, tap to decide');
  listing = { requests: [pending()], share_typing: true };
  await tick();
  expect(pill().textContent).toBe(' 1 access request');
  expect(pill().getAttribute('aria-label')).toBe('1 sandboxed-session access request waiting, tap to decide');
  await click(pill());
  expect(rows()).toHaveLength(1);
});

test('a failed read keeps the last list', async () => {
  listing = { requests: [pending()], share_typing: true };
  await tick();
  fail = true;
  await tick();
  expect(pill().hidden).toBe(false);
});

test('a refused request says why and offers no Approve, only Deny', async () => {
  listing = { requests: [pending({ refusal: '/home/u/.ssh holds secrets or system state, it can never be mounted' })], share_typing: true };
  await open();
  const [row] = rows();
  expect(row?.querySelector('.ar-approve')).toBeNull();
  expect(row?.querySelector('.ar-refused')?.textContent).toBe(" Can't be approved: /home/u/.ssh holds secrets or system state, it can never be mounted");
  await click(row?.querySelector('.ar-deny'));
  expect(posts).toEqual([{ path: '/api/share-requests/abcdefabcdef-0/deny', body: {} }]);
});

test('approve defaults to the server duration, read-only; write offered only when asked, starting off', async () => {
  listing = { requests: [pending(), pending({ id: 'rw-1', access: 'rw' })], share_typing: true, default_minutes: 60 };
  await open();
  const [ro, rw] = rows();
  expect(within(ro as HTMLElement).getByRole('combobox').textContent).toContain('1 h');
  expect(ro?.querySelector('[role="switch"]')).toBeNull();
  expect(rw?.querySelector('[role="switch"]')?.getAttribute('aria-checked')).toBe('false');
  await click(ro?.querySelector('.ar-approve'));
  expect(posts).toEqual([{ path: '/api/share-requests/abcdefabcdef-0/approve', body: { minutes: 60, rw: false } }]);
});

test('a server default outside the list joins it, selected', async () => {
  listing = { requests: [pending()], share_typing: true, default_minutes: 90 };
  await open();
  await click(rows()[0]?.querySelector('.ar-approve'));
  expect(posts[0]?.body.minutes).toBe(90);
});

test('a write request posts rw:true only when "allow write" is ON, not merely offered', async () => {
  listing = { requests: [pending({ id: 'rw-1', access: 'rw' }), pending({ id: 'rw-2', access: 'rw' })], share_typing: true };
  await open();
  const [offered, on] = rows();
  await click(offered?.querySelector('.ar-approve'));
  await click(on?.querySelector('[role="switch"]'));
  await click(on?.querySelector('.ar-approve'));
  expect(posts.map((p) => [p.path, p.body.rw])).toEqual([['/api/share-requests/rw-1/approve', false], ['/api/share-requests/rw-2/approve', true]]);
});

test('Approve posts the duration the operator PICKED, not the default', async () => {
  listing = { requests: [pending(), pending({ id: 'x-1' })], share_typing: true, default_minutes: 60 };
  await open();
  const [a, b] = rows() as [HTMLElement, HTMLElement];
  pickDuration(a, '4 h');
  await click(a.querySelector('.ar-approve'));
  pickDuration(b, '15 min');
  await click(b.querySelector('.ar-approve'));
  expect(posts.map((p) => p.body.minutes)).toEqual([240, 15]);
});

test('with Guest typing off, Approve is disabled and the row says why', async () => {
  listing = { requests: [pending()], share_typing: false };
  await open();
  expect(document.querySelector<HTMLButtonElement>('.ar-approve')?.disabled).toBe(true);
  expect(rows()[0]?.textContent).toContain('Guest typing is off. Turn it on in Settings to approve.');
});

test('an approved request shows its time left and access, and a Revoke', async () => {
  listing = { requests: [pending({ status: 'approved', seconds_left: 1800, rw: false })], share_typing: true };
  await open();
  expect(rows()[0]?.querySelector('.ar-meta')?.textContent).toBe('30 min left · read-only');
  await click(rows()[0]?.querySelector('.ar-revoke'));
  expect(posts[0]?.path).toBe('/api/share-requests/abcdefabcdef-0/revoke');
  listing = { requests: [pending({ status: 'approved', seconds_left: 5400, rw: true }), pending({ id: 'd-1', kind: 'domain', status: 'approved', seconds_left: 60 })], share_typing: true };
  await tick();
  expect(rows().map((r) => r.querySelector('.ar-meta')?.textContent)).toEqual(['1h 30m left · read-write', '1 min left']);
});

test('a decided request is shown, done, with no controls', async () => {
  listing = { requests: [pending({ status: 'denied' })], share_typing: true };
  await open();
  expect(rows()[0]?.classList.contains('ar-done')).toBe(true);
  expect(rows()[0]?.querySelector('button')).toBeNull();
});

test('a refused decision shows the server reason on its row, and keeps the row usable', async () => {
  listing = { requests: [pending()], share_typing: true };
  await open();
  postReply = { ok: false, error: 'the session is gone' };
  await click(rows()[0]?.querySelector('.ar-deny'));
  expect(rows()[0]?.querySelector('[role="alert"]')?.textContent).toBe('the session is gone');
  expect(rows()[0]?.querySelector<HTMLButtonElement>('.ar-deny')?.disabled).toBe(false);
  postReply = { ok: false };
  await click(rows()[0]?.querySelector('.ar-approve'));
  expect(rows()[0]?.querySelector('[role="alert"]')?.textContent).toBe('Could not approve the request');
  fail = true;
  await click(rows()[0]?.querySelector('.ar-deny'));
  expect(rows()[0]?.querySelector('[role="alert"]')?.textContent).toBe('Could not deny the request');
});

test('a successful decision refetches the list', async () => {
  listing = { requests: [pending()], share_typing: true };
  await open();
  listing = { requests: [], share_typing: true };
  await click(rows()[0]?.querySelector('.ar-deny'));
  expect(rows()).toHaveLength(0);
  expect(document.querySelector('.access-req-sheet .ss-empty')?.textContent).toBe('No access requests.');
});

test('the pure half: labels, choices, who, time left, pending', () => {
  expect(DURATIONS).toEqual([15, 60, 240, 1440]);
  expect([15, 60, 90, 240, 1440, 2880].map(durLabel)).toEqual(['15 min', '1 h', '90 min', '4 h', '1 day', '2 days']);
  expect(durationChoices(60)).toEqual([15, 60, 240, 1440]);
  expect(durationChoices(30)).toEqual([15, 30, 60, 240, 1440]);
  expect(who(pending())).toBe('sandbox-1');
  expect(who(pending({ window: null, wid: '@4' }))).toBe('@4');
  expect(who(pending({ window: null, wid: null }))).toBe('sandbox abcdefabcdef');
  expect([null, 1, 60, 61, 3540, 3541, 3600, 3661].map(timeLeft)).toEqual(['', '1 min left', '1 min left', '2 min left', '59 min left', '1h 0m left', '1h 0m left', '1h 2m left']);
  expect(pendingCount([pending(), pending({ status: 'approved' })])).toBe(1);
  expect(pendingCount(null)).toBe(0);
});

test.each([[{ error: 'restarting' }], [null], [{ requests: 'x' }]])('an error reply %j keeps the last list, never "no requests"', async (bad) => {
  listing = { requests: [pending()], share_typing: true };
  await tick();
  listing = bad;
  await tick();
  expect(pill().hidden).toBe(false);
});
