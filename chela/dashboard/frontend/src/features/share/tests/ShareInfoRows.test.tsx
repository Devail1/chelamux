// CMX-239: a share's join link + pairing code rows (ported from terminals.js
// _shareInfoRowsHTML/_wireShareCopyButtons) and the pane toast.
import { act, fireEvent, render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import { paneToast, ShareInfoRows } from '../index.ts';
import './setup.ts';

const values = (root: ParentNode) => [...root.querySelectorAll('input')].map((i) => [i.readOnly, i.value]);

test('the link and the code, each readonly with its label', () => {
  const { container } = render(<ShareInfoRows info={{ join_url: 'https://relay.test/j/abc', pairing_code: 'PAIR1' }} emptyMsg="none" />);
  expect(values(container)).toEqual([[true, 'https://relay.test/j/abc'], [true, 'PAIR1']]);
  expect([...container.querySelectorAll('.field-label')].map((l) => l.textContent)).toEqual(['Join link', 'Pairing code']);
  expect(screen.getByLabelText('Pairing code')).toBe(container.querySelectorAll('input')[1]);
  expect(container.querySelector('.field-hint')?.textContent).toBe('Needed to decrypt');
});

test.each([
  [{ pairing_code: 'PAIR1' }, [[true, 'PAIR1']]],
  [{ join_url: 'https://relay.test/j/abc' }, [[true, 'https://relay.test/j/abc']]],
])('one of the two (%j) shows just that row', (info, want) => {
  const { container } = render(<ShareInfoRows info={info} emptyMsg="none" />);
  expect(values(container)).toEqual(want);
  expect(container.querySelector('.tsp-warn')).toBeNull();
});

test.each([[null], [{}]])('nothing to show (%j) says so instead of empty rows', (info) => {
  const { container } = render(<ShareInfoRows info={info} emptyMsg="Link unavailable." />);
  expect(container.querySelector('input')).toBeNull();
  expect(container.querySelector('.tsp-warn')?.textContent).toBe('Link unavailable.');
});

function clipboard(writeText?: (t: string) => Promise<void>) {
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: writeText ? { writeText } : undefined });
}

test('Copy writes the value, says Copied, and goes back to Copy after 1.5 s', async () => {
  vi.useFakeTimers();
  const writes: string[] = [];
  clipboard((t) => {
    writes.push(t);
    return Promise.resolve();
  });
  render(<ShareInfoRows info={{ pairing_code: 'PAIR1' }} emptyMsg="" />);
  const btn = screen.getByRole('button');
  await act(async () => {
    fireEvent.click(btn);
  });
  expect([writes, btn.textContent]).toEqual([['PAIR1'], 'Copied']);
  act(() => vi.advanceTimersByTime(1499));
  expect(btn.textContent).toBe('Copied');
  act(() => vi.advanceTimersByTime(1));
  expect(btn.textContent).toBe('Copy');
});

test.each([
  ['a refused write', () => Promise.reject(new Error('denied'))],
  ['no clipboard API', undefined],
])('%s never claims Copied', async (_, writeText) => {
  clipboard(writeText);
  render(<ShareInfoRows info={{ pairing_code: 'PAIR1' }} emptyMsg="" />);
  await act(async () => {
    fireEvent.click(screen.getByRole('button'));
  });
  expect(screen.getByRole('button').textContent).toBe('Copy');
});

test('paneToast lands under the pane header, replaces the previous one, and goes after 4 s', () => {
  vi.useFakeTimers();
  document.body.innerHTML = '<div class="gs-head"><span><button id="a"></button></span></div>';
  const btn = document.getElementById('a');
  paneToast(btn, 'first');
  paneToast(btn, 'wire failed');
  const toasts = () => [...document.querySelectorAll('.gs-head > .term-share-toast')].map((t) => t.textContent);
  expect(toasts()).toEqual(['wire failed']);
  vi.advanceTimersByTime(3999);
  expect(toasts()).toEqual(['wire failed']);
  vi.advanceTimersByTime(1);
  expect(toasts()).toEqual([]);
});

test('paneToast outside a header uses the anchor\'s parent; with no anchor it only logs', () => {
  document.body.innerHTML = '<div id="p"><button id="a"></button></div>';
  paneToast(document.getElementById('a'), 'hi');
  expect(document.querySelector('#p > .term-share-toast')?.textContent).toBe('hi');
  const log = vi.spyOn(console, 'log').mockImplementation(() => {});
  paneToast(null, 'lost');
  expect(log.mock.calls).toEqual([['[chela-share]', 'lost']]);
  log.mockRestore();
});
