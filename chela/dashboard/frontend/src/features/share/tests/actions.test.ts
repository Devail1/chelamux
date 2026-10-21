// CMX-240 (CMX-212 P25): what a share does to the PANE (still legacy: its title and its ttyd
// iframe come in through configureShareHost) and to the page: minting through the dialog,
// adopt-first, the post-mint reload, the server's mode answer, and the boot read.
import { act } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { closeShareDialog, configureShareHost, followAgents, setShareMode, shares, shareSession, startShares, stopShare } from '../index.ts';
import { INFO, resetShares, serve, settle } from './server.ts';
import { must, stubFetch } from './setup.ts';

type Frame = { frame: HTMLIFrameElement; reload: ReturnType<typeof vi.fn> };

/** A pane frame whose contentWindow is ours: `term` its live size, `reload` its reload. */
function frame(term: { cols: number; rows: number } | null, reloadThrows = false): Frame {
  const f = document.createElement('iframe');
  f.setAttribute('src', '/term/%401/?cid=x');
  const reload = vi.fn(() => {
    if (reloadThrows) throw new Error('cross-frame');
  });
  Object.defineProperty(f, 'contentWindow', { value: { term, location: { reload } } });
  return { frame: f, reload };
}

let frames: Frame[] = [];
beforeEach(() => {
  resetShares();
  frames = [];
  configureShareHost({ paneTitle: (w) => `title of ${w}`, paneFrames: () => frames.map((f) => f.frame) });
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});
afterEach(() => {
  act(() => closeShareDialog());
  document.body.innerHTML = '';
  vi.restoreAllMocks();
});

test('⭐ an unshared pane opens the dialog titled by the host; the mint carries the pane size, then the pane reloads and the sheet opens', async () => {
  const s = serve({ info: {}, shared: {} });
  frames = [frame(null), frame({ cols: 132, rows: 40 })];
  await act(() => shareSession('@1'));
  expect(document.querySelector('[role="dialog"] h3')?.textContent).toBe('Share title of @1');
  s.shared = { '@1': { mode: 'view' } };
  act(() => must(document.querySelector<HTMLButtonElement>('.sd-share')).click());
  await settle();
  expect(s.mintPosts).toEqual([{ on: true, mode: 'view', cols: 132, rows: 40 }]);
  expect(Object.fromEntries(shares.get())).toEqual({ '@1': { mode: 'view', expiresAt: null } });
  expect(frames.map((f) => f.reload.mock.calls.length)).toEqual([1, 1]);
});

test('adopt-first: a live share is never re-minted, nor its pane reloaded', async () => {
  const s = serve({ info: { ...INFO, share_epoch: 3 } });
  frames = [frame({ cols: 80, rows: 24 })];
  await act(() => shareSession('@1'));
  await settle();
  expect(document.querySelector('.share-dialog')).toBeNull();
  expect(s.mintPosts).toEqual([]);
  expect(frames[0]?.reload).not.toHaveBeenCalled();
  expect(shares.get().has('@1')).toBe(true);
});

test('an unreadable share-info is no share: it opens the dialog, never adopts', async () => {
  serve({ info: 'throw', shared: {} });
  await act(() => shareSession('@1'));
  expect(document.querySelector('.share-dialog')).toBeTruthy();
  expect(shares.get().has('@1')).toBe(false);
});

test('a stop reloads the pane; a frame that refuses a reload is reloaded by its src', async () => {
  serve();
  shares.set(new Map([['@1', { mode: 'view', expiresAt: null }]]));
  const ok = frame(null);
  const refusing = frame(null, true);
  const setSrc = vi.fn();
  Object.defineProperty(refusing.frame, 'src', { set: setSrc, get: () => '' });
  frames = [ok, refusing];
  expect(await stopShare('@1')).toBe(true);
  expect(ok.reload).toHaveBeenCalledTimes(1);
  expect(setSrc.mock.calls).toEqual([['/term/%401/?cid=x']]);
});

test('a refused stop reloads nothing', async () => {
  serve({ stop: 'refuse' });
  shares.set(new Map([['@1', { mode: 'view', expiresAt: null }]]));
  frames = [frame(null)];
  expect(await stopShare('@1')).toBe(false);
  expect(frames[0]?.reload).not.toHaveBeenCalled();
});

test('a mode change that errors without a reason says so, and changes nothing', async () => {
  shares.set(new Map([['@1', { mode: 'view', expiresAt: null }]]));
  stubFetch(() => {
    throw new Error('offline');
  });
  expect(await setShareMode('@1', { mode: 'typing' })).toEqual({ ok: false, error: 'Could not change the mode' });
  stubFetch(() => ({ ok: false }));
  expect(await setShareMode('@1', { mode: 'typing' })).toEqual({ ok: false, error: 'Could not change the mode' });
  expect(shares.get().get('@1')?.mode).toBe('view');
});

test('a mode change takes the mode and expiry the server answered', async () => {
  stubFetch(() => ({ ok: true, mode: 'unsandboxed', expires_at: 42 }));
  expect(await setShareMode('@1', { mode: 'typing' })).toEqual({ ok: true });
  expect(shares.get().get('@1')).toEqual({ mode: 'unsandboxed', expiresAt: 42 });
});

test('page load lights the pill for the shares already live, and a failed read leaves it dark', async () => {
  const calls = stubFetch((url) => (url.endsWith('/api/term/shared') ? { '@4': { mode: 'typing', expires_at: null } } : {}));
  startShares();
  await settle();
  expect(calls.map((c) => c.url)).toContain('/api/term/shared');
  expect(Object.fromEntries(shares.get())).toEqual({ '@4': { mode: 'typing', expiresAt: null } });
  resetShares();
  stubFetch(() => {
    throw new Error('offline');
  });
  startShares();
  await settle();
  expect(shares.get().size).toBe(0);
});

test('the agents poll is followed', () => {
  followAgents([{ window_id: '@2', shared: true, share_mode: 'typing' }]);
  expect(shares.get().get('@2')?.mode).toBe('typing');
});

const JSON_POST = (body: unknown) => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

test('the exact requests: Stop POSTs {on:false} to the share; a mode change POSTs to /share-mode', async () => {
  const calls = stubFetch(() => ({ ok: true, mode: 'typing' }));
  await stopShare('a b');
  await setShareMode('a b', { mode: 'typing' });
  expect(calls).toEqual([
    { url: '/api/term/a%20b/share', init: JSON_POST({ on: false }) },
    { url: '/api/term/a%20b/share-mode', init: JSON_POST({ mode: 'typing' }) },
  ]);
});

test('a refused Stop reads /api/term/shared before it gives up', async () => {
  const calls = stubFetch((url) => (url.endsWith('/shared') ? { '@1': { mode: 'view' } } : { ok: false }));
  expect(await stopShare('@1')).toBe(false);
  expect(calls.map((c) => c.url)).toEqual(['/api/term/%401/share', '/api/term/shared']);
});

test('a pane whose terminal cannot be read mints without a size', async () => {
  const s = serve({ info: {}, shared: {} });
  const f = document.createElement('iframe');
  Object.defineProperty(f, 'contentWindow', {
    get() {
      throw new Error('cross-frame');
    },
  });
  frames = [{ frame: f, reload: vi.fn() }];
  await act(() => shareSession('@1'));
  act(() => must(document.querySelector<HTMLButtonElement>('.sd-share')).click());
  await settle();
  expect(s.mintPosts).toEqual([{ on: true, mode: 'view' }]);
});
