// CMX-476 "the device you're using wins": a frame reports user input on its own connection, the
// server then sizes the window to it. Driven by the events a real browser fires in the frame.
import { expect, test, vi } from 'vitest';
import { focusFrame, INPUT_EVENTS, INPUT_REPORT_MS, markReady, mountPaneFrames, reportInput, termKey, termPaste, wireInputReport } from '../index.ts';
import { frame, host, must, stage, stubFetch, type Call } from './setup.ts';

function wall(...wids: string[]) {
  for (const w of wids) markReady(w);
  stage(...wids);
  mountPaneFrames(document);
}
const inputs = (calls: Call[]) =>
  calls.filter((c) => c.path.endsWith('/input')).map((c) => [decodeURIComponent(c.path.split('/')[3] ?? ''), (c.body as { cid: string }).cid, c.method]);
const cidOf = (ifr: HTMLIFrameElement) => new URL(must(ifr.getAttribute('src')), 'http://x').searchParams.get('cid');

test('the input events are keys, taps, clicks and scrolls, and never a resize or a bare focus', () => {
  expect([...INPUT_EVENTS]).toEqual(['keydown', 'pointerdown', 'touchstart', 'wheel']);
  expect(INPUT_REPORT_MS).toBe(2000);
});

test.each(['keydown', 'pointerdown', 'touchstart', 'wheel'])('a %s in the frame reports input for ITS cid, at once', (ev) => {
  const calls = stubFetch();
  wall('@1', '@2');
  const f1 = must(frame('@1'));
  wireInputReport(f1);
  must(f1.contentDocument).dispatchEvent(new Event(ev));
  expect(inputs(calls)).toEqual([['@1', cidOf(f1), 'POST']]);
});

test.each(['resize', 'focus', 'focusin', 'scroll'])('a %s in the frame is not input', (ev) => {
  const calls = stubFetch();
  wall('@1');
  const f1 = must(frame('@1'));
  wireInputReport(f1);
  must(f1.contentDocument).dispatchEvent(new Event(ev));
  must(f1.contentWindow).dispatchEvent(new Event(ev));
  expect(inputs(calls)).toEqual([]);
});

test('reports are throttled per connection: the first at once, then at most one per INPUT_REPORT_MS', () => {
  vi.useFakeTimers();
  const calls = stubFetch((p) => (p.endsWith('/input') ? { tracked: true } : {}));
  wall('@1', '@2');
  const f1 = must(frame('@1')), f2 = must(frame('@2'));
  reportInput(f1);
  reportInput(f1);
  reportInput(f2); // another connection is never held back by this one
  expect(inputs(calls).map((c) => c[0])).toEqual(['@1', '@2']);
  vi.advanceTimersByTime(INPUT_REPORT_MS - 1);
  reportInput(f1);
  expect(inputs(calls).length).toBe(2);
  vi.advanceTimersByTime(1);
  reportInput(f1);
  expect(inputs(calls).map((c) => c[0])).toEqual(['@1', '@2', '@1']);
});

test('a blanked frame (no cid) reports nothing', () => {
  const calls = stubFetch();
  const ifr = document.createElement('iframe');
  ifr.setAttribute('src', 'about:blank');
  reportInput(ifr);
  ifr.setAttribute('src', '/term/%401/'); // a joiner's clean URL: no cid
  reportInput(ifr);
  expect(inputs(calls)).toEqual([]);
});

test('a frame mounted on the wall reports input once loaded', () => {
  const calls = stubFetch();
  wall('@3');
  const f = must(frame('@3'));
  f.dispatchEvent(new Event('load'));
  must(f.contentDocument).dispatchEvent(new Event('keydown'));
  expect(inputs(calls).map((c) => c[0])).toEqual(['@3']);
});

test('focusing a pane from the page is input on this device', () => {
  const calls = stubFetch();
  wall('@4');
  focusFrame(must(frame('@4')));
  expect(inputs(calls).map((c) => c[0])).toEqual(['@4']);
});

test('a keybar key and a paste report input for the single pane', async () => {
  const calls = stubFetch();
  wall('@5');
  host.activeWid.mockReturnValue('@5');
  await termKey('Escape');
  expect(inputs(calls).map((c) => c[0])).toEqual(['@5']);
  expect(calls.some((c) => c.path === '/api/term/key')).toBe(true);
  vi.useFakeTimers();
  vi.advanceTimersByTime(INPUT_REPORT_MS);
  vi.stubGlobal('navigator', { clipboard: { readText: () => Promise.resolve('hi') } });
  await termPaste(document.createElement('button'));
  expect(inputs(calls).map((c) => c[0])).toEqual(['@5', '@5']);
});

test('with no pane on the keybar, nothing is reported', async () => {
  const calls = stubFetch();
  wall('@6');
  host.activeWid.mockReturnValue('');
  await termKey('Escape');
  await termPaste(document.createElement('button'));
  expect(inputs(calls)).toEqual([]);
});

test.each([
  ['untracked', () => stubFetch((p) => (p.endsWith('/input') ? { tracked: false } : {}))],
  ['failed', () => { stubFetch(); vi.stubGlobal('fetch', () => Promise.reject(new Error('down'))); }],
])('an %s report does not hold the next input back', async (_k, setup) => {
  stubFetch();
  wall('@7');
  const f = must(frame('@7'));
  setup();
  const seen: string[] = [];
  const real = globalThis.fetch;
  vi.stubGlobal('fetch', (u: string, i?: RequestInit) => { seen.push(u); return real(u, i); });
  reportInput(f);
  await new Promise((r) => setTimeout(r, 0));
  reportInput(f);
  expect(seen.filter((u) => u.endsWith('/input')).length).toBe(2);
});

test('a tracked report holds the next input back', async () => {
  const calls = stubFetch((p) => (p.endsWith('/input') ? { tracked: true } : {}));
  wall('@8');
  const f = must(frame('@8'));
  reportInput(f);
  await new Promise((r) => setTimeout(r, 0));
  reportInput(f);
  expect(inputs(calls).length).toBe(1);
});
