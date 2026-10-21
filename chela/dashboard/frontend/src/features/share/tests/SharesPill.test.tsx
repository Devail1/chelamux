// CMX-240 (CMX-212 P25): the active-shares pill (CMX-377/422/403), the kill switch's way in:
// hidden while nothing is shared, the count and the most permissive mode in words, red with
// the time left while an UNSANDBOXED override is armed, and a tap opens the sheet.
import { act, fireEvent, render } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';
import { applyShared, closeSharesSheet, mountShares, shares, SharesPill, SharesSheet } from '../index.ts';
import { settle, serve } from './server.ts';
import { must } from './setup.ts';

beforeEach(() => {
  closeSharesSheet();
  shares.set(new Map());
});

const pill = () => must(document.getElementById('btn-shares'));

test('hidden while nothing is shared', () => {
  render(<SharesPill id="btn-shares" />);
  expect(pill().hidden).toBe(true);
  expect(pill().getAttribute('aria-label')).toBe('Active shares');
});

test('a view-only share: "1 sharing", the eye, and a label that says view only', () => {
  render(<SharesPill id="btn-shares" />);
  act(() => applyShared({ '@1': { mode: 'view' } }, true));
  expect(pill().hidden).toBe(false);
  expect(pill().querySelector('.si-text')?.textContent).toBe('1 sharing');
  expect(pill().dataset.mode).toBe('view');
  expect(pill().querySelector('svg')?.classList.contains('lucide-eye')).toBe(true);
  expect(pill().getAttribute('aria-label')).toBe('1 active share (view only), tap to manage or stop');
  expect(pill().classList.contains('si-unsandboxed')).toBe(false);
});

test('typing wins over view, and the keyboard shows', () => {
  render(<SharesPill id="term-shares" />);
  act(() => applyShared({ '@1': { mode: 'view' }, '@2': { mode: 'typing' } }, true));
  const p = must(document.getElementById('term-shares'));
  expect(p.dataset.mode).toBe('typing');
  expect(p.querySelector('.si-text')?.textContent).toBe('2 sharing');
  expect(p.querySelector('svg')?.classList.contains('lucide-keyboard')).toBe(true);
});

test('⭐ an armed UNSANDBOXED override turns it red and says so, with the time left', () => {
  vi.spyOn(Date, 'now').mockReturnValue(1_700_000_000_000);
  render(<SharesPill id="btn-shares" />);
  act(() => applyShared({ '@1': { mode: 'unsandboxed', expires_at: 1_700_000_000 + 3 * 3600 + 5 * 60 + 10 } }, true));
  expect(pill().classList.contains('si-unsandboxed')).toBe(true);
  expect(pill().dataset.mode).toBe('unsandboxed');
  expect(pill().querySelector('.si-text')?.textContent).toBe('UNSANDBOXED: guest can type · 3h 5m left');
  expect(pill().getAttribute('aria-label')).toMatch(/^UNSANDBOXED: a guest can type into a real shell\. /);
  vi.restoreAllMocks();
});

test('the last share stopping hides it again', () => {
  render(<SharesPill id="btn-shares" />);
  act(() => applyShared({ '@1': { mode: 'view' } }, true));
  act(() => applyShared({}, true));
  expect(pill().hidden).toBe(true);
});

test('a tap opens the Active shares sheet', async () => {
  render(
    <>
      <SharesPill id="btn-shares" />
      <SharesSheet />
    </>,
  );
  serve({ shared: { '@1': { mode: 'view' } } });
  act(() => applyShared({ '@1': { mode: 'view' } }, true));
  fireEvent.click(pill());
  await settle();
  expect(document.querySelector('.shares-sheet .ss-row[data-wid="@1"]')).toBeTruthy();
});

test('mountShares puts each pill copy in its slot, and the sheet in its own', async () => {
  document.body.innerHTML = '<div class="safety-float"><span id="shares-pill-float"></span></div><span id="shares-pill-row"></span><div id="shares-sheet-island"></div>';
  let roots: ReturnType<typeof mountShares> = [];
  act(() => {
    roots = mountShares(document);
  });
  act(() => applyShared({ '@1': { mode: 'view' } }, true));
  expect(document.querySelector('.safety-float > #shares-pill-float > #btn-shares')).toBeTruthy();
  expect(document.querySelector('#shares-pill-row > #term-shares')).toBeTruthy();
  serve({ shared: { '@1': { mode: 'view' } } });
  fireEvent.click(must(document.getElementById('term-shares')));
  await settle();
  expect(document.querySelector('.shares-sheet .ss-row[data-wid="@1"]')).toBeTruthy();
  act(() => roots.forEach((r) => r?.unmount()));
});

test('the countdown clock ticks only while an override is on a pill, and stops with the last one', () => {
  vi.useFakeTimers();
  const { unmount } = render(<SharesPill id="btn-shares" />);
  act(() => applyShared({ '@1': { mode: 'view' } }, true));
  expect(vi.getTimerCount()).toBe(0);
  act(() => applyShared({ '@1': { mode: 'unsandboxed', expires_at: null } }, true));
  expect(vi.getTimerCount()).toBe(1);
  unmount();
  expect(vi.getTimerCount()).toBe(0);
});

test('the countdown moves on its own every 30 s', () => {
  vi.useFakeTimers();
  vi.setSystemTime(1_700_000_000_000);
  render(<SharesPill id="btn-shares" />);
  act(() => applyShared({ '@1': { mode: 'unsandboxed', expires_at: 1_700_000_000 + 5 * 60 + 10 } }, true));
  expect(pill().querySelector('.si-text')?.textContent).toBe('UNSANDBOXED: guest can type · 5m left');
  act(() => vi.advanceTimersByTime(60_000));
  expect(pill().querySelector('.si-text')?.textContent).toBe('UNSANDBOXED: guest can type · 4m left');
});

test('the countdown keeps ticking while another pill is still on the page', () => {
  vi.useFakeTimers();
  vi.setSystemTime(1_700_000_000_000);
  const a = render(<SharesPill id="btn-shares" />);
  render(<SharesPill id="term-shares" />);
  act(() => applyShared({ '@1': { mode: 'unsandboxed', expires_at: 1_700_000_000 + 5 * 60 + 10 } }, true));
  a.unmount();
  act(() => vi.advanceTimersByTime(60_000));
  expect(document.querySelector('#term-shares .si-text')?.textContent).toBe('UNSANDBOXED: guest can type · 4m left');
});
