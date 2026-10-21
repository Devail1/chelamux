// CMX-239 (CMX-212 P24): the share dialog on the kit's <Dialog> (ported from
// tests/share_dialog.test.mjs, CMX-403/419). The page-level half (the pill, the red banner, the
// Active shares sheet after a mint) stays in that suite, driven through the real terminals.js.
import { act, fireEvent, render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import { closeShareDialog, fetchShareOptions, mintShare, openShareDialog, ShareDialog, type ShareOptions } from '../index.ts';
import { must, stubFetch } from './setup.ts';

const SANDBOXED: ShareOptions = { share_typing: true, sandboxed: true, typing_allowed: true, window_name: 'shell-1' };
const UNSAFE: ShareOptions = {
  share_typing: true, sandboxed: false, typing_allowed: false, unsandboxed_offered: true,
  unsandboxed_minutes: 30, unsandboxed_choices: [30, 240, 1440, 10080, 20160], unsandboxed_long_minutes: 240, window_name: 'shell-1',
};
const MINTED = { ok: true, mode: 'view', join_url: 'https://relay.test/j/abc', pairing_code: 'P' };

function show(options: ShareOptions, props: { dims?: () => { cols: number; rows: number } | null } = {}) {
  const onClose = vi.fn();
  const onMinted = vi.fn();
  render(<ShareDialog wid="@1" title="shell" options={options} onClose={onClose} onMinted={onMinted} {...props} />);
  return { onClose, onMinted };
}
const radio = (name: RegExp) => screen.getByRole('radio', { name });
const shareBtn = () => screen.getByRole('button', { name: 'Share' });
const type = (label: RegExp, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
async function pick(label: RegExp) {
  await act(async () => {
    fireEvent.click(radio(label));
  });
}
async function clickShare() {
  await act(async () => {
    fireEvent.click(shareBtn());
  });
}
async function pickDuration(text: string) {
  const trigger = screen.getByRole('combobox', { name: 'How long full access lasts' });
  await act(async () => {
    fireEvent.keyDown(trigger, { key: 'Enter' });
  });
  await act(async () => {
    fireEvent.click(screen.getByRole('option', { name: text }));
  });
}
const posted = (calls: { init?: RequestInit }[]) => calls.filter((c) => c.init?.method === 'POST').map((c) => JSON.parse(String(c.init?.body)) as Record<string, unknown>);

test('a labelled dialog; View only is the default and mints mode "view" with the pane size', async () => {
  const calls = stubFetch(() => MINTED);
  const { onClose, onMinted } = show(SANDBOXED, { dims: () => ({ cols: 80, rows: 24 }) });
  expect(screen.getByRole('dialog', { name: 'Share shell' })).toBeTruthy();
  expect(radio(/View only/).getAttribute('aria-checked')).toBe('true');
  expect(radio(/Allow typing/).getAttribute('aria-checked')).toBe('false');
  await clickShare();
  expect(calls).toEqual([{ url: '/api/term/%401/share', init: { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ on: true, mode: 'view', cols: 80, rows: 24 }) } }]);
  expect(onClose).toHaveBeenCalledTimes(1);
  expect(onMinted.mock.calls).toEqual([[MINTED]]);
});

test('no pane size (a frame not ready) mints without cols/rows', async () => {
  const calls = stubFetch(() => MINTED);
  show(SANDBOXED, { dims: () => null });
  await clickShare();
  expect(posted(calls)).toEqual([{ on: true, mode: 'view' }]);
});

test('setting off: Allow typing is disabled and says why; no override', () => {
  show({ share_typing: false, typing_allowed: false, window_name: 'shell-1' });
  expect((radio(/Allow typing/) as HTMLButtonElement).disabled).toBe(true);
  expect(radio(/Allow typing/).closest('label')?.textContent).toContain('Typing is disabled in Settings');
  expect(screen.queryByRole('radio', { name: /UNSANDBOXED/ })).toBeNull();
});

test('not a sandboxed session: Allow typing disabled with the New session hint', () => {
  show({ share_typing: true, sandboxed: false, typing_allowed: false, window_name: 'shell-1' });
  expect((radio(/Allow typing/) as HTMLButtonElement).disabled).toBe(true);
  expect(must(document.querySelector('.sd-reason')).textContent).toBe('Not a sandboxed session. Start one from New session → Sandboxed');
});

test('setting on + verified sandbox: Allow typing is enabled, no reason, mints mode "typing"', async () => {
  const calls = stubFetch(() => ({ ...MINTED, mode: 'typing' }));
  show(SANDBOXED);
  expect(document.querySelector('.sd-reason')).toBeNull();
  expect(screen.queryByRole('radio', { name: /UNSANDBOXED/ })).toBeNull();
  await pick(/Allow typing/);
  await clickShare();
  expect(posted(calls)).toEqual([{ on: true, mode: 'typing' }]);
});

test('UNSANDBOXED: Share stays disabled until the window name is typed exactly', async () => {
  const calls = stubFetch(() => MINTED);
  show(UNSAFE);
  expect(screen.queryByLabelText(/to confirm/)).toBeNull();
  await pick(/Full access, UNSANDBOXED: the guest can type into a real shell on this machine/);
  expect((shareBtn() as HTMLButtonElement).disabled).toBe(true);
  expect(shareBtn().className).toContain('btn-danger');
  type(/Type shell-1 to confirm/, 'shell-2');
  expect((shareBtn() as HTMLButtonElement).disabled).toBe(true);
  type(/Type shell-1 to confirm/, 'shell-1');
  expect((shareBtn() as HTMLButtonElement).disabled).toBe(false);
  expect(screen.queryByLabelText(/again/)).toBeNull();
  await clickShare();
  expect(posted(calls)).toEqual([{ on: true, mode: 'unsandboxed', minutes: 30, confirm: 'shell-1' }]);
});

test('back to View only drops the override: the confirmation hides and Share is plain again', async () => {
  show(UNSAFE);
  await pick(/UNSANDBOXED/);
  await pick(/View only/);
  expect(screen.queryByLabelText(/to confirm/)).toBeNull();
  expect([(shareBtn() as HTMLButtonElement).disabled, shareBtn().className.includes('btn-danger')]).toEqual([false, false]);
});

test('the picker preselects the configured default and lists the server choices', async () => {
  show({ ...UNSAFE, unsandboxed_minutes: 240 });
  await pick(/UNSANDBOXED/);
  const trigger = screen.getByRole('combobox', { name: 'How long full access lasts' });
  expect(trigger.textContent).toBe('4 h');
  await act(async () => {
    fireEvent.keyDown(trigger, { key: 'Enter' });
  });
  expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual(['30 min', '4 h', '1 day', '7 days', '14 days']);
});

test('a 7-day pick needs the window name twice, and POSTs both with minutes 10080', async () => {
  const calls = stubFetch(() => MINTED);
  show(UNSAFE);
  await pick(/UNSANDBOXED/);
  await pickDuration('7 days');
  type(/Type shell-1 to confirm/, 'shell-1');
  expect((shareBtn() as HTMLButtonElement).disabled).toBe(true);
  type(/Longer than 4 h: type shell-1 again/, 'shell-2');
  expect((shareBtn() as HTMLButtonElement).disabled).toBe(true);
  type(/Longer than 4 h: type shell-1 again/, 'shell-1');
  expect((shareBtn() as HTMLButtonElement).disabled).toBe(false);
  await clickShare();
  expect(posted(calls)).toEqual([{ on: true, mode: 'unsandboxed', minutes: 10080, confirm: 'shell-1', confirm_long: 'shell-1' }]);
});

test.each([
  ['the server refuses with a reason', () => ({ ok: false, error: 'typing is off' }), 'typing is off'],
  ['the server refuses without one', () => ({ ok: false }), 'Share failed'],
  ['the request throws', () => { throw new Error('offline'); }, 'Share failed'],
])('%s: the dialog stays open on its choice, says so, and Share works again', async (_, answer, msg) => {
  stubFetch(answer);
  const { onClose, onMinted } = show(SANDBOXED);
  await clickShare();
  expect(screen.getByRole('alert').textContent).toBe(msg);
  expect([onClose.mock.calls.length, onMinted.mock.calls.length]).toEqual([0, 0]);
  expect((shareBtn() as HTMLButtonElement).disabled).toBe(false);
});

test('Share is not re-sent while a mint is in flight', async () => {
  let release!: (v: unknown) => void;
  const fetch = vi.fn(() => new Promise((r) => (release = r)));
  vi.stubGlobal('fetch', fetch);
  show(SANDBOXED);
  await clickShare();
  expect((shareBtn() as HTMLButtonElement).disabled).toBe(true);
  await clickShare();
  expect(fetch).toHaveBeenCalledTimes(1);
  await act(async () => release({ json: () => Promise.resolve(MINTED) }));
});

test('Cancel and Escape close without minting', async () => {
  const calls = stubFetch(() => MINTED);
  const { onClose } = show(SANDBOXED);
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
  expect(onClose).toHaveBeenCalledTimes(2);
  expect(calls).toEqual([]);
});

test('reads: options from the encoded path; an unreadable or empty answer is {}', async () => {
  const calls = stubFetch(() => SANDBOXED);
  expect(await fetchShareOptions('@1')).toEqual(SANDBOXED);
  expect(calls).toEqual([{ url: '/api/term/%401/share-options' }]);
  stubFetch(() => null);
  expect(await fetchShareOptions('@1')).toEqual({});
  stubFetch(() => { throw new Error('offline'); });
  expect(await fetchShareOptions('@1')).toEqual({});
});

test('mintShare: only ok === true is a share', async () => {
  stubFetch(() => ({ ok: 1 }));
  expect(await mintShare('@1', { on: true, mode: 'view' })).toEqual({ ok: false, error: 'Share failed' });
  stubFetch(() => null);
  expect(await mintShare('@1', { on: true, mode: 'view' })).toEqual({ ok: false, error: 'Share failed' });
  stubFetch(() => MINTED);
  expect(await mintShare('@1', { on: true, mode: 'view' })).toEqual({ ok: true, resp: MINTED });
});

test('openShareDialog renders before it resolves; closeShareDialog removes it', async () => {
  stubFetch(() => SANDBOXED);
  const onMinted = vi.fn();
  await act(() => openShareDialog({ wid: '@1', title: 'shell', onMinted }));
  expect(screen.getByRole('dialog', { name: 'Share shell' })).toBeTruthy();
  act(() => closeShareDialog());
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('a close while the options read is in flight wins: the dialog never shows', async () => {
  stubFetch(() => SANDBOXED);
  await act(async () => {
    const p = openShareDialog({ wid: '@1', title: 'shell', onMinted: () => {} });
    closeShareDialog();
    await p;
  });
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('a second open starts fresh: the earlier choice does not carry over', async () => {
  stubFetch(() => SANDBOXED);
  await act(() => openShareDialog({ wid: '@1', title: 'one', onMinted: () => {} }));
  await pick(/Allow typing/);
  await act(() => openShareDialog({ wid: '@2', title: 'two', onMinted: () => {} }));
  expect(screen.getAllByRole('dialog')).toHaveLength(1);
  expect(screen.getByRole('heading').textContent).toBe('Share two');
  expect(radio(/View only/).getAttribute('aria-checked')).toBe('true');
  act(() => closeShareDialog());
});

test('the mounted dialog mints and hands the response to its opener', async () => {
  const calls = stubFetch((url) => (url.endsWith('/share-options') ? SANDBOXED : MINTED));
  const onMinted = vi.fn();
  await act(() => openShareDialog({ wid: '@1', title: 'shell', dims: () => ({ cols: 100, rows: 30 }), onMinted }));
  await clickShare();
  expect(posted(calls)).toEqual([{ on: true, mode: 'view', cols: 100, rows: 30 }]);
  expect(onMinted.mock.calls).toEqual([[MINTED]]);
  expect(screen.queryByRole('dialog')).toBeNull();
});
