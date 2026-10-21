// CMX-240 (CMX-212 P25): the Active shares sheet (ported from tests/share_mode_sheet.test.mjs
// and tests/share_sheet.test.mjs, CMX-152/421/419). Each row: its mode as a control (the live
// one pressed), what THAT share allows, a mode change in place (same link and code), the
// UNSANDBOXED upgrade armed only by the typed window name, and Stop, the kill switch, which
// fails CLOSED: a share whose Stop did not land stays listed.
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { applyShared, closeSharesSheet, fmtShareEnd, openSharesSheet, shareMode, shares, SharesSheet, shareSession, type ShareMode, type ShareOptions } from '../index.ts';
import { INFO, resetShares, serve, settle, type Server } from './server.ts';
import { must } from './setup.ts';

const SANDBOXED: ShareOptions = { share_typing: true, sandboxed: true, typing_allowed: true, window_name: 'shell-1' };
const ALL_OFFERS: ShareOptions = { share_typing: true, sandboxed: false, typing_allowed: false, unsandboxed_offered: true, window_name: 'shell-1' };
const UPGRADE: ShareOptions = { ...ALL_OFFERS, unsandboxed_minutes: 30, unsandboxed_choices: [30, 240, 1440, 10080, 20160], unsandboxed_long_minutes: 240 };
const EXPIRES = 1.9e9;

beforeEach(() => {
  resetShares();
  vi.spyOn(console, 'warn').mockImplementation(() => {}); // owner presence cannot load in jsdom
});
afterEach(() => vi.restoreAllMocks());

async function openSheet(mode: ShareMode, options: ShareOptions, over: Partial<Server> = {}) {
  cleanup(); // one sheet on the page: a second modal would hide the first from the a11y tree
  const s = serve({ shared: { '@1': { mode, expires_at: mode === 'unsandboxed' ? EXPIRES : null } }, options, ...over });
  applyShared(s.shared === 'throw' ? {} : s.shared, true);
  render(<SharesSheet />);
  act(() => openSharesSheet());
  await settle();
  return s;
}
const row = (wid = '@1') => must(document.querySelector<HTMLElement>(`.ss-row[data-wid="${wid}"]`), `row ${wid}`);
const opt = (m: ShareMode) => row().querySelector<HTMLButtonElement>(`.ss-mode-opt[data-mode="${m}"]`);
const pressed = () => [...row().querySelectorAll<HTMLElement>('.ss-mode-opt[aria-pressed="true"]')].map((b) => b.dataset.mode);
const desc = () => must(row().querySelector('.ss-mode-desc')).textContent;
const click = async (el: Element | null) => {
  fireEvent.click(must(el));
  await settle();
};
const type = (label: RegExp, value: string) => fireEvent.change(within(row()).getByLabelText(label), { target: { value } });
const grantBtn = () => within(row()).getByRole('button', { name: 'Grant full access' }) as HTMLButtonElement;
async function pickDuration(text: string) {
  fireEvent.keyDown(within(row()).getByRole('combobox', { name: 'How long full access lasts' }), { key: 'Enter' });
  await settle();
  await click(screen.getByRole('option', { name: text }));
}

test('a labelled sheet; a view-only row presses View only and never says guests can type', async () => {
  await openSheet('view', { share_typing: false, typing_allowed: false, window_name: 'shell-1' });
  expect(screen.getByRole('dialog', { name: 'Active shares' })).toBeTruthy();
  expect(pressed()).toEqual(['view']);
  expect(must(opt('view')).textContent).toBe(' View only');
  expect(must(opt('typing')).textContent).toBe(' Allow typing');
  expect(document.querySelector('.shares-sheet')?.textContent).not.toMatch(/watch and type/);
  expect(desc()).toMatch(/Nothing they type reaches this machine/);
  expect(must(row().querySelector('.ss-mode-desc')).className).toBe('ss-mode-desc');
});

test('a typing share says guests can type', async () => {
  await openSheet('typing', SANDBOXED);
  expect(pressed()).toEqual(['typing']);
  expect(desc()).toMatch(/watch and type/);
});

test("setting off: Allow typing is disabled with the dialog's reason, and a click POSTs nothing", async () => {
  const s = await openSheet('view', { share_typing: false, sandboxed: true, typing_allowed: false, window_name: 'shell-1' });
  expect(must(opt('typing')).disabled).toBe(true);
  expect(row().querySelector('.ss-mode-reason')?.textContent).toBe('Typing is disabled in Settings');
  await click(opt('typing'));
  expect(s.modePosts).toEqual([]);
});

test('not sandboxed: Allow typing is disabled with the New session hint', async () => {
  await openSheet('view', ALL_OFFERS);
  expect(must(opt('typing')).disabled).toBe(true);
  expect(row().querySelector('.ss-mode-reason')?.textContent).toBe('Not a sandboxed session. Start one from New session → Sandboxed');
});

test('⭐ view to typing where allowed POSTs /share-mode once, never re-mints, and the row follows', async () => {
  const s = await openSheet('view', SANDBOXED);
  expect(must(opt('typing')).disabled).toBe(false);
  await click(opt('typing'));
  expect(s.modePosts).toEqual([{ mode: 'typing' }]);
  expect(s.mintPosts).toEqual([]);
  expect(pressed()).toEqual(['typing']);
  expect(shareMode('@1')).toBe('typing');
  expect([...row().querySelectorAll('.ss-row-body input')].map((i) => (i as HTMLInputElement).value)).toEqual([INFO.join_url, INFO.pairing_code]);
});

test('down to View only applies at once, with no confirmation', async () => {
  const s = await openSheet('typing', SANDBOXED);
  await click(opt('view'));
  expect(s.modePosts).toEqual([{ mode: 'view' }]);
  expect(pressed()).toEqual(['view']);
});

test('clicking the mode already in force POSTs nothing', async () => {
  const s = await openSheet('typing', SANDBOXED);
  await click(opt('typing'));
  expect(s.modePosts).toEqual([]);
});

test('up to UNSANDBOXED POSTs nothing until the window name is typed; the grant then says when it ends', async () => {
  const s = await openSheet('view', UPGRADE);
  expect(row().querySelector('.ss-unsafe-confirm')).toBeNull();
  await click(opt('unsandboxed'));
  expect(s.modePosts).toEqual([]);
  expect(row().querySelector('.ss-unsafe-confirm')).toBeTruthy();
  expect(grantBtn().disabled).toBe(true);
  type(/Type shell-1 to confirm/, 'shell-2');
  expect(grantBtn().disabled).toBe(true);
  type(/Type shell-1 to confirm/, 'shell-1');
  expect(grantBtn().disabled).toBe(false);
  await click(grantBtn());
  expect(s.modePosts).toEqual([{ mode: 'unsandboxed', confirm: 'shell-1', minutes: 30 }]);
  expect(pressed()).toEqual(['unsandboxed']);
  expect(row().querySelector('.ss-unsafe-confirm')).toBeNull();
  expect(desc()).toContain(`Ends ${fmtShareEnd(1e9, Date.now())}`);
});

test('the upgrade picker lists the server choices, preselected to the default; 30 min needs one name', async () => {
  await openSheet('view', UPGRADE);
  await click(opt('unsandboxed'));
  const trigger = within(row()).getByRole('combobox', { name: 'How long full access lasts' });
  expect(trigger.textContent).toBe('30 min');
  fireEvent.keyDown(trigger, { key: 'Enter' });
  await settle();
  expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual(['30 min', '4 h', '1 day', '7 days', '14 days']);
  expect(within(row()).queryByLabelText(/again/)).toBeNull();
});

test('⭐ an upgrade past 4 h POSTs nothing until the window name is typed TWICE', async () => {
  const s = await openSheet('view', UPGRADE);
  await click(opt('unsandboxed'));
  await pickDuration('14 days');
  type(/Type shell-1 to confirm/, 'shell-1');
  expect(grantBtn().disabled).toBe(true);
  await click(grantBtn());
  expect(s.modePosts).toEqual([]);
  type(/Longer than 4 h: type shell-1 again/, 'shell-1');
  expect(grantBtn().disabled).toBe(false);
  await click(grantBtn());
  expect(s.modePosts).toEqual([{ mode: 'unsandboxed', confirm: 'shell-1', minutes: 20160, confirm_long: 'shell-1' }]);
});

test('a 4 h upgrade (not longer than 4 h) needs one confirmation and carries its minutes', async () => {
  const s = await openSheet('view', UPGRADE);
  await click(opt('unsandboxed'));
  await pickDuration('4 h');
  expect(within(row()).queryByLabelText(/again/)).toBeNull();
  type(/Type shell-1 to confirm/, 'shell-1');
  await click(grantBtn());
  expect(s.modePosts).toEqual([{ mode: 'unsandboxed', confirm: 'shell-1', minutes: 240 }]);
});

test('the confirmation takes focus and defaults to the configured time box, else 30 minutes', async () => {
  await openSheet('view', { ...ALL_OFFERS, unsandboxed_minutes: 45 });
  await click(opt('unsandboxed'));
  expect(document.activeElement).toBe(within(row()).getByLabelText(/Type shell-1 to confirm/));
  expect(within(row()).getByRole('combobox', { name: 'How long full access lasts' }).textContent).toBe('45 min');
  act(() => closeSharesSheet());
  await openSheet('view', ALL_OFFERS);
  await click(opt('unsandboxed'));
  expect(within(row()).getByRole('combobox', { name: 'How long full access lasts' }).textContent).toBe('30 min');
});

test('a multi-day UNSANDBOXED row names the day it ends and the time left, in the row and its badge', async () => {
  const now = (EXPIRES - (12 * 86400 + 4 * 3600 + 30)) * 1000;
  vi.spyOn(Date, 'now').mockReturnValue(now);
  await openSheet('unsandboxed', UPGRADE);
  expect(desc()).toContain(`Ends ${fmtShareEnd(EXPIRES, now)} (12d 4h left).`);
  expect(must(row().querySelector('.ss-mode-desc')).classList.contains('ss-mode-desc-unsafe')).toBe(true);
  expect(row().querySelector('.ss-mode-unsafe')?.textContent).toBe('UNSANDBOXED · 12d 4h left');
});

test('the UNSANDBOXED override is offered ONLY when every condition holds', async () => {
  for (const [why, o] of [
    ['window is sandboxed', { ...ALL_OFFERS, sandboxed: true, typing_allowed: true }],
    ['server does not offer it', { ...ALL_OFFERS, unsandboxed_offered: false }],
    ['no window name to confirm', { ...ALL_OFFERS, window_name: '' }],
    ['setting off', { ...ALL_OFFERS, share_typing: false }],
  ] as const) {
    await openSheet('view', o);
    expect(opt('unsandboxed'), why).toBeNull();
    act(() => closeSharesSheet());
  }
  await openSheet('view', ALL_OFFERS);
  expect(opt('unsandboxed')).toBeTruthy();
});

test('a live UNSANDBOXED share always shows its pressed mode, never a re-grant box', async () => {
  await openSheet('unsandboxed', { share_typing: false, sandboxed: true, typing_allowed: false, window_name: 'shell-1' });
  expect(pressed()).toEqual(['unsandboxed']);
  await click(opt('unsandboxed'));
  expect(row().querySelector('.ss-unsafe-confirm')).toBeNull();
});

test('only the UNSANDBOXED option wears the danger style', async () => {
  await openSheet('view', ALL_OFFERS);
  expect([...row().querySelectorAll<HTMLElement>('.ss-mode-opt-unsafe')].map((b) => b.dataset.mode)).toEqual(['unsandboxed']);
});

test("a refused change shows the server's reason and leaves the mode as it was", async () => {
  const s = await openSheet('view', SANDBOXED, { refuse: 'Not a sandboxed session. Start one from New session → Sandboxed' });
  await click(opt('typing'));
  expect(s.modePosts).toEqual([{ mode: 'typing' }]);
  expect(within(row()).getByRole('alert').textContent).toBe('Not a sandboxed session. Start one from New session → Sandboxed');
  expect(pressed()).toEqual(['view']);
  expect(shareMode('@1')).toBe('view');
});

test('the mode shown after a change is what the SERVER reports, not what was asked', async () => {
  await openSheet('view', SANDBOXED, { serverMode: 'view' });
  await click(opt('typing'));
  expect(shareMode('@1')).toBe('view');
  expect(pressed()).toEqual(['view']);
});

test('the controls lock while a change is in flight', async () => {
  const s = await openSheet('view', ALL_OFFERS);
  await click(opt('unsandboxed'));
  type(/Type shell-1 to confirm/, 'shell-1');
  fireEvent.click(grantBtn());
  expect(grantBtn().disabled).toBe(true);
  expect(must(opt('view')).disabled).toBe(true);
  await settle();
  expect(s.modePosts).toEqual([{ mode: 'unsandboxed', confirm: 'shell-1', minutes: 30 }]);
});

test('the "why not" reason shows only where typing is unavailable and not in force', async () => {
  await openSheet('view', SANDBOXED);
  expect(row().querySelector('.ss-mode-reason')).toBeNull();
  act(() => closeSharesSheet());
  // A typing share whose window just stopped verifying: still pressed, still downgradable.
  await openSheet('typing', { share_typing: true, sandboxed: false, typing_allowed: false, window_name: 'shell-1' });
  expect(row().querySelector('.ss-mode-reason')).toBeNull();
  expect([must(opt('typing')).disabled, must(opt('view')).disabled]).toEqual([false, false]);
});

test('the expiry is forgotten when the share leaves UNSANDBOXED', async () => {
  await openSheet('unsandboxed', ALL_OFFERS);
  expect(shares.get().get('@1')?.expiresAt).toBe(EXPIRES);
  await click(opt('view'));
  expect(shares.get().get('@1')).toEqual({ mode: 'view', expiresAt: null });
});

test('a row draws no mode control until its options are read, but its Stop is there at once', async () => {
  const s = await openSheet('view', SANDBOXED, { hangRows: true });
  expect(row().querySelector('.ss-mode-opt')).toBeNull();
  expect(desc()).toBe('Loading…');
  const stop = within(row()).getByRole('button', { name: 'Stop' }) as HTMLButtonElement;
  expect(stop.disabled).toBe(false);
  await click(stop);
  expect(s.stopPosts).toEqual(['@1']);
});

test('the row shows the join link and pairing code; an unreadable one says so', async () => {
  await openSheet('view', SANDBOXED);
  expect([...row().querySelectorAll('.ss-row-body input')].map((i) => (i as HTMLInputElement).value)).toEqual([INFO.join_url, INFO.pairing_code]);
  act(() => closeSharesSheet());
  await openSheet('view', SANDBOXED, { info: 'throw' });
  expect(row().querySelector('.ss-row-body')?.textContent).toBe('Link unavailable. Reopen this sheet to retry.');
});

test('⭐ Stop revokes the share: POST {on:false}, the row leaves, and the last one closes the sheet', async () => {
  const s = serve({ shared: { '@1': { mode: 'view' }, '@2': { mode: 'typing' } }, options: SANDBOXED });
  applyShared(s.shared as Record<string, { mode: ShareMode }>, true);
  render(<SharesSheet />);
  act(() => openSharesSheet());
  await settle();
  await click(within(row('@1')).getByRole('button', { name: 'Stop' }));
  expect(s.stopPosts).toEqual(['@1']);
  expect(document.querySelector('.ss-row[data-wid="@1"]')).toBeNull();
  expect(screen.getByRole('dialog', { name: 'Active shares' })).toBeTruthy();
  await click(within(row('@2')).getByRole('button', { name: 'Stop' }));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(shares.get().size).toBe(0);
});

test('⭐ a Stop the server refused fails CLOSED: the share stays listed, says so, and can be retried', async () => {
  const s = await openSheet('view', SANDBOXED, { stop: 'refuse' });
  await click(within(row()).getByRole('button', { name: 'Stop' }));
  expect(s.stopPosts).toEqual(['@1']);
  expect(within(row()).getByRole('alert').textContent).toBe('Stop failed: this share is still live. Try again.');
  expect((within(row()).getByRole('button', { name: 'Stop' }) as HTMLButtonElement).disabled).toBe(false);
  expect(shares.get().has('@1')).toBe(true);
});

test('a Stop that errored but the server no longer lists counts as stopped', async () => {
  const s = await openSheet('view', SANDBOXED, { stop: 'throw' });
  s.shared = {};
  await click(within(row()).getByRole('button', { name: 'Stop' }));
  expect(shares.get().has('@1')).toBe(false);
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('a Stop that errored with the server unreadable keeps the share listed', async () => {
  const s = await openSheet('view', SANDBOXED, { stop: 'throw' });
  s.shared = 'throw';
  await click(within(row()).getByRole('button', { name: 'Stop' }));
  expect(shares.get().has('@1')).toBe(true);
  expect(within(row()).getByRole('alert')).toBeTruthy();
});

test('⭐ Stop all stops every share and closes; one that did not stop keeps the sheet open', async () => {
  const s = serve({ shared: { '@1': { mode: 'view' }, '@2': { mode: 'unsandboxed' } }, options: SANDBOXED });
  applyShared(s.shared as Record<string, { mode: ShareMode }>, true);
  render(<SharesSheet />);
  act(() => openSharesSheet());
  await settle();
  await click(screen.getByRole('button', { name: 'Stop all sharing (2)' }));
  expect(s.stopPosts.sort()).toEqual(['@1', '@2']);
  expect(screen.queryByRole('dialog')).toBeNull();

  s.stop = 'refuse';
  s.shared = { '@1': { mode: 'view' } };
  applyShared(s.shared, true);
  act(() => openSharesSheet());
  await settle();
  await click(screen.getByRole('button', { name: 'Stop all sharing (1)' }));
  expect(screen.getByRole('dialog', { name: 'Active shares' })).toBeTruthy();
  expect(screen.getAllByRole('alert').map((a) => a.textContent)).toContain('Some shares did not stop. They are still listed above.');
  expect(row('@1')).toBeTruthy();
});

test('opening reconciles against /api/term/shared; an unreadable one keeps the best-effort list', async () => {
  serve({ shared: { '@1': { mode: 'typing' } }, options: SANDBOXED });
  applyShared({ '@1': { mode: 'view' }, '@9': { mode: 'view' } }, true);
  render(<SharesSheet />);
  act(() => openSharesSheet());
  await settle();
  expect([...document.querySelectorAll<HTMLElement>('.ss-row')].map((r) => r.dataset.wid)).toEqual(['@1']);
  expect(shareMode('@1')).toBe('typing');
  act(() => closeSharesSheet());
  serve({ shared: 'throw', options: SANDBOXED });
  applyShared({ '@1': { mode: 'view' }, '@9': { mode: 'view' } }, true);
  act(() => openSharesSheet());
  await settle();
  expect([...document.querySelectorAll<HTMLElement>('.ss-row')].map((r) => r.dataset.wid)).toEqual(['@1', '@9']);
});

test('no shares: the sheet says so and offers no Stop all', async () => {
  serve({ shared: {} });
  render(<SharesSheet />);
  act(() => openSharesSheet());
  await settle();
  expect(document.querySelector('.ss-empty')?.textContent).toBe('No active shares.');
  expect(screen.queryByRole('button', { name: /Stop all/ })).toBeNull();
});

test('Escape and the close button close the sheet', async () => {
  await openSheet('view', SANDBOXED);
  fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' });
  await settle();
  expect(screen.queryByRole('dialog')).toBeNull();
  act(() => openSharesSheet());
  await settle();
  await click(screen.getByRole('button', { name: 'Close' }));
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('Share on an already-shared pane mints nothing and points the sheet at that share, scrolled to', async () => {
  const s = serve({ shared: { '@1': { mode: 'view' } }, options: SANDBOXED });
  const scrolled: Element[] = [];
  vi.spyOn(Element.prototype, 'scrollIntoView').mockImplementation(function (this: Element) {
    scrolled.push(this);
  });
  render(<SharesSheet />);
  await act(() => shareSession('@1'));
  await settle();
  expect(document.querySelector('.share-dialog')).toBeNull();
  expect(s.mintPosts).toEqual([]);
  expect(row().classList.contains('ss-row-focus')).toBe(true);
  expect(row().querySelector('.ss-already')?.textContent).toMatch(/already shared/);
  expect(opt('typing')).toBeTruthy();
  expect(scrolled).toContain(row());
});

test('the sheet forgets its focused share on close, and a focus on an unlisted share highlights nothing', async () => {
  serve({ shared: { '@1': { mode: 'view' } }, options: SANDBOXED });
  render(<SharesSheet />);
  await act(() => shareSession('@1'));
  await settle();
  expect(row().classList.contains('ss-row-focus')).toBe(true);
  act(() => closeSharesSheet());
  act(() => openSharesSheet());
  await settle();
  expect(row().classList.contains('ss-row-focus')).toBe(false);
  expect(document.querySelector('.ss-already')).toBeNull();
  act(() => openSharesSheet('@404'));
  await settle();
  expect(document.querySelector('.ss-row-focus')).toBeNull();
});

test('each row badge names its mode', async () => {
  await openSheet('typing', SANDBOXED);
  expect(must(row().querySelector('.ss-mode')).getAttribute('title')).toBe('Guest may type (sandboxed session)');
  act(() => closeSharesSheet());
  await openSheet('view', SANDBOXED);
  expect(must(row().querySelector('.ss-mode')).getAttribute('aria-label')).toBe('View only');
});

test('Stop and Stop all say "Stopping…" while the request is in flight', async () => {
  const s = serve({ shared: { '@1': { mode: 'view' }, '@2': { mode: 'view' } }, options: SANDBOXED });
  applyShared(s.shared as Record<string, { mode: ShareMode }>, true);
  render(<SharesSheet />);
  act(() => openSharesSheet());
  await settle();
  const stop = within(row('@1')).getByRole('button', { name: 'Stop' }) as HTMLButtonElement;
  act(() => stop.click());
  expect([stop.textContent, stop.disabled]).toEqual(['Stopping…', true]);
  const all = screen.getByRole('button', { name: /Stop all sharing/ }) as HTMLButtonElement;
  act(() => all.click());
  expect([all.textContent, all.disabled]).toEqual(['Stopping…', true]);
  await settle();
});

test('a non-object /api/term/shared answer is unreadable: a refused Stop keeps the share', async () => {
  const s = await openSheet('view', SANDBOXED, { stop: 'refuse' });
  s.shared = 'oops' as unknown as Server['shared'];
  await click(within(row()).getByRole('button', { name: 'Stop' }));
  expect([...shares.get().keys()]).toEqual(['@1']);
});
