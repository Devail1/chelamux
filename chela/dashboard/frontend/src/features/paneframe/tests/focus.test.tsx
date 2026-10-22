// The focus ring follows the frame whose OWN document holds focus (CMX-104), and the pane keys
// a loaded frame carries (CMX-85, CMX-114, CMX-116). tests/wall_focus_tracking.test.mjs runs
// the same ring against the real Wall.
import { act } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import { palette } from '../../palette/index.ts';
import { applyTermFocus, focusedPane, focusFrame, markReady, mountPaneFrames, startTermTimer, termKey } from '../index.ts';
import { frame, host, must, stage, stubFetch } from './setup.ts';

function wall(...wids: string[]) {
  stubFetch();
  for (const w of wids) markReady(w);
  stage(...wids);
  mountPaneFrames(document);
}
const pane = (wid: string) => must(document.querySelector(`[data-pane="${wid}"]`));
const hasFocus = (wid: string, on: boolean) => {
  must(must(frame(wid)).contentDocument).hasFocus = () => on;
};

test('the ring marks the pane whose frame document holds focus, and only it', () => {
  wall('@1', '@2');
  hasFocus('@1', false);
  hasFocus('@2', true);
  applyTermFocus();
  expect(pane('@2').classList.contains('term-focused')).toBe(true);
  expect(pane('@1').classList.contains('term-focused')).toBe(false);
  expect(focusedPane()).toBe(pane('@2'));
  pane('@1').classList.add('term-focused'); // a stray ring, from anywhere
  applyTermFocus();
  expect(document.querySelectorAll('.term-focused').length).toBe(1);
  hasFocus('@2', false);
  applyTermFocus();
  expect(document.querySelectorAll('.term-focused').length).toBe(0);
  expect(focusedPane()).toBeNull();
});

test('the tracking polls while the Wall ticks', async () => {
  vi.useFakeTimers();
  wall('@1');
  hasFocus('@1', true);
  startTermTimer();
  await act(() => vi.advanceTimersByTimeAsync(250));
  expect(pane('@1').classList.contains('term-focused')).toBe(true);
});

test('focusFrame moves the keyboard three ways: the window, the element and xterm', () => {
  wall('@1');
  const f = must(frame('@1'));
  const win = must(f.contentWindow) as Window & { term?: { focus: () => void } };
  const calls: string[] = [];
  win.focus = () => calls.push('window');
  f.focus = () => calls.push('element');
  win.term = { focus: () => calls.push('term') };
  focusFrame(f);
  expect(calls).toEqual(['window', 'element', 'term']);
  delete win.term;
  expect(() => focusFrame(f)).not.toThrow(); // a terminal not booted yet
});

test('a loaded frame opens the palette on Ctrl+K in its own document, before xterm sees it', () => {
  wall('@1');
  const f = must(frame('@1'));
  f.dispatchEvent(new Event('load'));
  const doc = must(f.contentDocument);
  const xterm = doc.appendChild(doc.createElement('textarea')); // its helper textarea, the real target
  let reached = false;
  doc.addEventListener('keydown', () => { reached = true; });
  const e = new KeyboardEvent('keydown', { key: 'k', ctrlKey: true, bubbles: true, cancelable: true });
  act(() => { xterm.dispatchEvent(e); });
  expect(palette.get().open).toBe(true);
  expect(e.defaultPrevented).toBe(true);
  expect(reached).toBe(false);
  const plain = new KeyboardEvent('keydown', { key: 'k', bubbles: true, cancelable: true });
  xterm.dispatchEvent(plain);
  expect(plain.defaultPrevented).toBe(false);
  expect(reached).toBe(true);
});

test('termKey sends the key to the single pane, and nothing without one', async () => {
  const calls = stubFetch();
  await termKey('Tab');
  expect(calls).toEqual([]);
  host.activeWid.mockReturnValue('@4');
  await termKey('Tab');
  expect(calls).toEqual([{ path: '/api/term/key', method: 'POST', body: { agent: '@4', key: 'Tab' } }]);
});
