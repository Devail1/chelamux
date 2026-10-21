// CMX-103: the Pending-approvals pill and sheet (ported from tests/approvals.test.mjs and
// tests/approvals_categories.test.mjs by CMX-219). The pill shows only while a request waits;
// a blocked one says why and offers only Deny; Approve needs a second, confirming click; the
// browser notification is opt-in and fires once per NEW request; every request-supplied field
// lands as TEXT; a failed fetch keeps what was known and never reads as "nothing is waiting".
import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { ARM_MS, ApprovalsPill, ApprovalsSheet, openApprovals, resetApprovals, tickApprovals } from '../index.ts';
import { NOTIFY_KEY, durStr, notifyEnabled, safeHref, setApprovalNotify, whatText, type Request } from '../model.ts';

let listing: unknown = { requests: [] };
let posts: { path: string; body: unknown }[] = [];
let postReply: unknown = null;
let fail = false;
let notes: { title: string; opts: { body: string; tag: string } }[] = [];

class FakeNotification {
  readonly shown = true;
  static permission = 'granted';
  static requestPermission = () => Promise.resolve('granted');
  constructor(title: string, opts: { body: string; tag: string }) {
    notes.push({ title, opts });
  }
}

beforeEach(() => {
  listing = { requests: [] };
  posts = [];
  postReply = null;
  fail = false;
  notes = [];
  FakeNotification.permission = 'granted';
  FakeNotification.requestPermission = () => Promise.resolve('granted');
  localStorage.removeItem(NOTIFY_KEY);
  resetApprovals();
  vi.stubGlobal('Notification', FakeNotification);
  vi.stubGlobal('fetch', (url: string, opts?: RequestInit) => {
    if (fail) return Promise.reject(new Error('network down'));
    let body: unknown = listing;
    if (opts?.method === 'POST') {
      posts.push({ path: url, body: JSON.parse(String(opts.body || '{}')) });
      body = postReply ?? { ok: true, state: 'denied' };
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
  });
  render(<><ApprovalsPill id="btn-approvals" /><ApprovalsSheet /></>);
});
afterEach(async () => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const ID = `override-${'a'.repeat(24)}`;
const req = (over: Partial<Request> = {}): Request => ({ id: ID, kind: 'override', task_id: 't1', label: 'cmx-1', head_sha: 'b'.repeat(40),
  judge_state: 'blocked', base: 'dev', reason: 'judge flaked', actor: 'orchestrator', age_s: 120, expires_in_s: 3600, blocked: null,
  pr_url: 'https://github.com/o/r/pull/1', ...over });
const idOf = (c: string) => `override-${c.repeat(24)}`;

const pill = () => document.getElementById('btn-approvals') as HTMLButtonElement;
const sheet = () => document.querySelector('.approvals-sheet');
const rows = () => [...document.querySelectorAll('.approvals-sheet .pa-row')] as HTMLElement[];
const q = <T extends Element = HTMLElement>(sel: string) => document.querySelector(sel) as T;
const tick = () => act(() => tickApprovals());
const open = () => act(() => openApprovals());
const click = async (el: Element) => {
  await act(async () => {
    fireEvent.click(el);
    await new Promise((r) => setTimeout(r, 0));
  });
};
async function list(requests: unknown) {
  listing = requests === null || typeof requests !== 'object' || !Array.isArray(requests) ? requests : { requests };
  await tick();
}

// --- the pill ----------------------------------------------------------------------------

test('the pill shows only while a request is pending, with the count in its text and its name', async () => {
  await list([]);
  expect(pill().hidden).toBe(true);
  await list([req(), req({ id: idOf('c') })]);
  expect(pill().hidden).toBe(false);
  expect(pill().textContent).toBe(' 2 pending approvals');
  // a screen reader hears the count, not only a label
  expect(pill().getAttribute('aria-label')).toBe('2 approval requests waiting, tap to review');
  await list([req()]);
  expect(pill().textContent).toBe(' 1 pending approval');
  expect(pill().getAttribute('aria-label')).toBe('1 approval request waiting, tap to review');
  // CMX-135: the trailing word is its own span, which a phone hides
  expect(q('#btn-approvals .pa-pill-word').textContent).toBe(' approval');
  expect(q('#btn-approvals svg')).not.toBeNull();
});

test('a click on the pill opens the sheet', async () => {
  await list([req()]);
  await click(pill());
  expect(sheet()?.textContent).toContain('Pending approvals');
  expect(rows()).toHaveLength(1);
});

test('through a failed refresh the pill keeps the known count, and says it could not refresh only with none', async () => {
  await list([req(), req({ id: idOf('6') })]);
  listing = { error: 'boom' };
  await tick();
  expect(pill().textContent).toBe(' 2 pending approvals');
  await list([]);
  listing = { error: 'boom' };
  await tick();
  expect(pill().hidden).toBe(false);
  expect(pill().textContent).toBe(' couldn’t refresh approvals');
  expect(q('#btn-approvals .pa-pill-word')).toBeNull();
  expect(pill().getAttribute('aria-label')).toBe('couldn’t refresh approvals, tap to review');
});

test('a page whose endpoint NEVER answered stays quiet on a dead fetch; a server-reported error shows', async () => {
  fail = true;
  await tick();
  expect(pill().hidden).toBe(true);
  fail = false;
  listing = { requests: [], error: 'the approvals store cannot be read' };
  await tick();
  expect(pill().hidden).toBe(false);
  expect(pill().textContent).toContain('couldn’t refresh approvals');
});

// --- the sheet ---------------------------------------------------------------------------

test('a row says what, who, why, age and expiry, exactly', async () => {
  listing = { requests: [req({ age_s: 90, expires_in_s: 3 * 3600 + 15 * 60 })] };
  await open();
  const [row] = rows();
  expect(row?.querySelector('.pa-what')?.textContent).toBe('Override-merge cmx-1 past the judge’s blocked on bbbbbbbbbbbb → dev');
  expect(row?.querySelector('.pa-meta')?.textContent?.split(' · ').slice(0, 3).join(' · ')).toBe('asked by orchestrator · 2m ago · expires in 3h 15m');
  expect(row?.querySelector('.pa-why')?.textContent).toBe('“judge flaked”');
});

test('rows keep the server order (oldest first): the sheet never re-sorts', async () => {
  const ids = ['d', 'a', 'c', 'b'].map(idOf);
  listing = { requests: ids.map((id, i) => req({ id, label: `r${i}`, age_s: 4000 - i * 1000 })) };
  await open();
  expect(rows().map((r) => r.dataset.id)).toEqual(ids);
});

test('an override and a release each show their own fields', async () => {
  listing = { requests: [req(), req({ id: idOf('e'), kind: 'release', version: '0.9.0', label: 'v0.9.0', head_sha: null, judge_state: null, base: null })] };
  await open();
  expect(rows().map((r) => r.querySelector('.pa-what')?.textContent)).toEqual(['Override-merge cmx-1 past the judge’s blocked on bbbbbbbbbbbb → dev', 'Release 0.9.0']);
});

test('a blocked request says why and offers no Approve, only Deny', async () => {
  listing = { requests: [req({ blocked: 'CI is failing on this head' })] };
  await open();
  const [row] = rows();
  expect(row?.querySelector('.pa-approve')).toBeNull();
  expect(row?.querySelector('.pa-deny')).not.toBeNull();
  expect(row?.querySelector('.pa-blocked')?.textContent).toBe(" Can't be approved: CI is failing on this head");
});

test('an unreadable request file is shown, marked, with no button at all, and still counted', async () => {
  const id = idOf('f');
  listing = { requests: [req(), { id, kind: 'unreadable', reason: 'not valid JSON', blocked: `unreadable request ${id}: not valid JSON` }] };
  await open();
  const bad = rows()[1];
  expect(bad?.classList.contains('pa-bad')).toBe(true);
  expect(bad?.querySelector('button')).toBeNull();
  expect(bad?.textContent).toContain(`unreadable request ${id}: not valid JSON`);
  expect(pill().textContent).toContain('2 pending approvals');
});

test('with nothing waiting the sheet says so; with nothing known after a failure it never does', async () => {
  await open();
  expect(sheet()?.textContent).toContain('Nothing is waiting for approval.');
  fail = true;
  await tick();
  expect(sheet()?.textContent).not.toContain('Nothing is waiting');
  expect(sheet()?.textContent).toContain('The approvals list is unavailable.');
  expect(q('.pa-stale').hidden).toBe(false);
});

test('approve needs a second, confirming click and posts confirm:true', async () => {
  listing = { requests: [req()] };
  await open();
  const ap = q<HTMLButtonElement>('.pa-approve');
  await click(ap);
  expect(posts).toEqual([]);
  expect(ap.textContent).toBe('Confirm merge');
  expect(ap.classList.contains('pa-armed')).toBe(true);
  await click(ap);
  expect(posts).toEqual([{ path: `/api/override/${ID}/approve`, body: { confirm: true } }]);
});

test('deny posts to the deny action, with an encoded id', async () => {
  const bad = 'x" onclick="alert(1)';
  listing = { requests: [req({ id: bad })] };
  await open();
  expect(rows()[0]?.dataset.id).toBe(bad);
  expect(document.querySelector('[onclick]')).toBeNull();
  await click(q('.pa-deny'));
  expect(posts).toEqual([{ path: `/api/override/${encodeURIComponent(bad)}/deny`, body: {} }]);
});

test('an armed Approve disarms on its own: a later single tap only re-arms', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout'] });
  listing = { requests: [req()] };
  await open();
  const ap = q<HTMLButtonElement>('.pa-approve');
  act(() => {
    fireEvent.click(ap);
  });
  expect(ap.textContent).toBe('Confirm merge');
  act(() => {
    vi.advanceTimersByTime(ARM_MS - 1);
  });
  expect(ap.textContent).toBe('Confirm merge');
  act(() => {
    vi.advanceTimersByTime(1);
  });
  expect(ap.textContent).toBe('Approve');
  expect(ap.classList.contains('pa-armed')).toBe(false);
  act(() => {
    fireEvent.click(ap);
  });
  expect(posts).toEqual([]);
  expect(ARM_MS).toBe(6000);
});

test('a refused decision shows the server reason (as text), and a later success clears it', async () => {
  listing = { requests: [req(), req({ id: idOf('5') })] };
  await open();
  postReply = { ok: false, state: 'expired', detail: '<img src=x onerror=alert(1)>' };
  await click(q('.pa-deny'));
  const err = () => q('.approvals-sheet .pa-err');
  expect(err().hidden).toBe(false);
  expect(err().textContent).toBe('<img src=x onerror=alert(1)>');
  expect(sheet()?.querySelector('img')).toBeNull();
  // the refused row is back to clickable, un-armed
  expect(q<HTMLButtonElement>('.pa-deny').disabled).toBe(false);
  postReply = null;
  await click(q('.pa-deny'));
  expect(err().hidden).toBe(true);
  expect(err().textContent).toBe('');
});

test.each([
  [{ ok: false, error: 'not found' }, 'not found'],
  [{ ok: false, state: 'expired' }, 'expired'],
  [{ ok: false }, 'Could not deny the request'],
  [null, 'Could not deny the request'],
])('a refusal %j reads %s', async (reply, want) => {
  listing = { requests: [req()] };
  await open();
  postReply = reply ?? { ok: false };
  await click(q('.pa-deny'));
  expect(q('.approvals-sheet .pa-err').textContent).toBe(want);
});

test('a decision whose request fails reads "Could not approve the request"', async () => {
  listing = { requests: [req()] };
  await open();
  await click(q('.pa-approve'));
  fail = true;
  await click(q('.pa-approve'));
  expect(q('.approvals-sheet .pa-err').textContent).toBe('Could not approve the request');
});

test('an open sheet follows every tick', async () => {
  listing = { requests: [req()] };
  await open();
  await list([req(), req({ id: idOf('4') })]);
  expect(rows()).toHaveLength(2);
  await list([]);
  expect(rows()).toHaveLength(0);
  expect(pill().hidden).toBe(true);
});

test('a failed fetch keeps what was known and says it could not refresh, until one succeeds', async () => {
  listing = { requests: [req()] };
  await open();
  fail = true;
  await tick();
  expect(rows()).toHaveLength(1);
  expect(pill().hidden).toBe(false);
  expect(q('.pa-stale').hidden).toBe(false);
  expect(q('.pa-stale').textContent).toBe('Couldn’t refresh approvals. Showing the last known list.');
  fail = false;
  await tick();
  expect(q('.pa-stale').hidden).toBe(true);
});

test.each([[{ error: 'store unreadable', requests: [] }], [{ error: 'x' }], [{ requests: 'nope' }], [null]])('an error reply %j keeps the list', async (bad) => {
  listing = { requests: [req()] };
  await open();
  listing = bad;
  await tick();
  expect(rows()).toHaveLength(1);
  expect(q('.pa-stale').hidden).toBe(false);
  expect(pill().hidden).toBe(false);
});

test('Escape closes the sheet', async () => {
  listing = { requests: [req()] };
  await open();
  act(() => {
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' });
  });
  expect(sheet()).toBeNull();
});

// --- escaping: every request-supplied field lands as TEXT --------------------------------

const IMG = '<img src=x onerror=alert(1)>';
const SCRIPT = '"><script>alert(1)</script>';
const SHORT = '<i id=pwn>';
const ATTR = '" onmouseover="alert(1)" x="';
const FIELDS: Record<string, (p: string) => Partial<Request>> = {
  reason: (p) => ({ reason: p }),
  label: (p) => ({ label: p }),
  task_id: (p) => ({ label: null, task_id: p }),
  head_sha: (p) => ({ head_sha: p }),
  judge_state: (p) => ({ judge_state: p }),
  base: (p) => ({ base: p }),
  actor: (p) => ({ actor: p }),
  blocked: (p) => ({ blocked: p }),
  version: (p) => ({ kind: 'release', version: p }),
  question: (p) => ({ kind: 'ask', question: p }),
  kind: (p) => ({ kind: p, question: null }),
  pr_url: (p) => ({ pr_url: p }),
  id: (p) => ({ id: p }),
};
const inert = () => document.querySelector('img, script, [onerror], [onmouseover], #pwn');

test.each(Object.keys(FIELDS).flatMap((f) => [IMG, SCRIPT, SHORT, ATTR].map((p) => [f, p])))('%s: %s renders as text, never markup', async (field, payload) => {
  listing = { requests: [req(FIELDS[field]?.(payload))] };
  await open();
  expect(inert()).toBeNull();
});

test('a PR url is an http(s) link opened safely; anything else is plain text', async () => {
  listing = { requests: [req()] };
  await open();
  const a = q<HTMLAnchorElement>('.pa-meta a');
  expect([a.getAttribute('href'), a.getAttribute('rel'), a.getAttribute('target')]).toEqual(['https://github.com/o/r/pull/1', 'noopener', '_blank']);
  await act(() => openApprovals());
  listing = { requests: [req({ pr_url: 'javascript:alert(1)' })] };
  await tick();
  expect(document.querySelector('.pa-meta a')).toBeNull();
  expect(document.querySelector('[href]')).toBeNull();
  expect(q('.pa-meta .pa-prtext').textContent).toBe('PR: javascript:alert(1)');
});

// --- the pure half -----------------------------------------------------------------------

test('safeHref: http(s) as a PREFIX only, after stripping whitespace and control characters', () => {
  expect(safeHref('https://a.test')).toBe('https://a.test');
  expect(safeHref('HTTPS://github.com/o/r/pull/1')).toBe('HTTPS://github.com/o/r/pull/1');
  expect(safeHref('  http://h.test/x\n')).toBe('http://h.test/x');
  expect(safeHref('https://git\thub.com/x')).toBe('https://github.com/x');
  for (const url of ['javascript:alert(1)', ' JaVaScRiPt:alert(1)', 'java\tscript:alert(1)', '\u0001javascript:alert(1)', 'java\nscript:alert(1)',
    'data:text/html,<script>alert(1)</script>', 'ftp://x.test/a', '//evil.test/x', 'vbscript:x', 'javascript:alert(1)//https://x.test',
    'data:text/html,x#http://x.test', ' x https://x.test', 'mailto:a@b.test?https://x.test', ' \u0001 javascript:1', '\u0085javascript:1']) {
    expect(safeHref(url), JSON.stringify(url)).toBeNull();
  }
  expect(safeHref('\u0085https://a.test')).toBe('https://a.test');
  expect(safeHref(null)).toBeNull();
  expect(safeHref(undefined)).toBeNull();
  expect(safeHref('')).toBeNull();
});

test('durStr: pinned literals', () => {
  const cases: [number | null | undefined, string][] = [[null, ''], [undefined, ''], [0, '<1m'], [29, '<1m'], [30, '1m'], [60, '1m'], [90, '2m'],
    [3540, '59m'], [3600, '1h 0m'], [3660, '1h 1m'], [47 * 3600 + 59 * 60, '47h 59m'], [48 * 3600, '2d 0h'], [50 * 3600, '2d 2h'], [-5, '<1m']];
  for (const [s, want] of cases) expect(durStr(s), `durStr(${s})`).toBe(want);
});

test('whatText per kind', () => {
  expect(whatText(req({ kind: 'release', version: '0.9.0' }))).toBe('Release 0.9.0');
  expect(whatText(req({ kind: 'release', version: null, label: 'v1' }))).toBe('Release v1');
  expect(whatText(req({ kind: 'release', version: null, label: null }))).toBe('Release ?');
  expect(whatText(req({ kind: 'ask', question: 'Ship it?' }))).toBe('Ship it?');
  expect(whatText(req({ kind: 'ask', question: null }))).toBe('ask');
  expect(whatText(req({ judge_state: null, base: null }))).toBe('Override-merge cmx-1 with no judge verdict on bbbbbbbbbbbb');
  expect(whatText(req({ kind: null, label: null, task_id: null, head_sha: null, judge_state: null, base: null }))).toBe('Override-merge ? with no judge verdict on ?');
});

// --- the browser notification ------------------------------------------------------------

test('browser notifications are off by default', async () => {
  await list([]);
  await list([req({ id: idOf('d') })]);
  expect(notes).toEqual([]);
  expect(notifyEnabled()).toBe(false);
});

test('opted in, a NEW request notifies once; what was already waiting does not', async () => {
  await list([req()]); // primes: already seen
  expect(await setApprovalNotify('on')).toBe(true);
  expect(localStorage.getItem(NOTIFY_KEY)).toBe('1');
  const fresh = req({ id: idOf('e') });
  await list([req(), fresh]);
  await list([req(), fresh]);
  expect(notes.map((n) => [n.title, n.opts.tag, n.opts.body])).toEqual([['chela: approval waiting', `chela-approval-${fresh.id}`, whatText(fresh)]]);
});

test('the first listing a fresh page sees never notifies, even opted in; the next new one does', async () => {
  expect(await setApprovalNotify('on')).toBe(true);
  await list([req(), req({ id: idOf('1') })]);
  expect(notes).toEqual([]);
  const next = req({ id: idOf('2') });
  await list([req(), req({ id: idOf('1') }), next]);
  expect(notes.map((n) => n.opts.tag)).toEqual([`chela-approval-${next.id}`]);
});

test('opted in, nothing notifies while permission is not granted, or with no API, or a throwing one', async () => {
  await list([]);
  expect(await setApprovalNotify('on')).toBe(true);
  FakeNotification.permission = 'denied';
  await list([req({ id: idOf('3') })]);
  expect(notes).toEqual([]);
  FakeNotification.permission = 'granted';
  vi.stubGlobal('Notification', Object.assign(function Throwing() {
    throw new Error('needs a service worker');
  }, { permission: 'granted' }));
  await list([req({ id: idOf('3') }), req({ id: idOf('7') })]);
  expect(pill().textContent).toContain('2 pending'); // a throwing constructor stops nothing
  vi.stubGlobal('Notification', undefined);
  await list([req({ id: idOf('3') }), req({ id: idOf('7') }), req({ id: idOf('8') })]);
  expect(notes).toEqual([]);
});

test('setApprovalNotify: off clears it; no API, a refused or a throwing permission request stays off', async () => {
  localStorage.setItem(NOTIFY_KEY, '1');
  expect(await setApprovalNotify('off')).toBe(false);
  expect(notifyEnabled()).toBe(false);
  FakeNotification.permission = 'default';
  FakeNotification.requestPermission = () => Promise.resolve('granted');
  expect(await setApprovalNotify('on')).toBe(true);
  FakeNotification.requestPermission = () => Promise.resolve('denied');
  expect(await setApprovalNotify('on')).toBe(false);
  expect(notifyEnabled()).toBe(false);
  FakeNotification.requestPermission = () => Promise.reject(new Error('blocked'));
  expect(await setApprovalNotify('on')).toBe(false);
  vi.stubGlobal('Notification', undefined);
  expect(await setApprovalNotify('on')).toBe(false);
  expect(notifyEnabled()).toBe(false);
});

test('a refresh that lands while the SSE `overrides` frame fires refetches at once', async () => {
  const { dispatch } = await import('../../../shared/events/index.ts');
  listing = { requests: [req()] };
  await act(async () => {
    dispatch('overrides', null);
    await new Promise((r) => setTimeout(r, 0));
  });
  expect(pill().hidden).toBe(false);
});
