// The tab signal (util.js until CMX-248): panes blocked on you put a count in the title and an
// amber badge on the favicon; none restores both. One module, one page: the tests run in order.
import { afterAll, beforeAll, expect, test, vi } from 'vitest';
import { updateTabSignal } from '../index.ts';

const icon = () => document.querySelector<HTMLLinkElement>('link[rel~="icon"]');
const waiting = { session_status: 'waiting' };
const gated = { session_status: 'busy', needs_human: true };
const calm = { session_status: 'busy' };
let draws = 0;
const fills: string[] = [];
const fillText = vi.fn();

beforeAll(() => {
  document.head.innerHTML = '<title>chela – Mission Control</title><link rel="icon" href="static/v/abc/img/favicon.svg" type="image/svg+xml">';
  document.body.style.setProperty('--yellow', '#e3b341');
  const ctx = { beginPath() {}, arc() {}, fill() {}, fillText, set fillStyle(v: string) { fills.push(v); } };
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => ctx as never);
  vi.spyOn(HTMLCanvasElement.prototype, 'toDataURL').mockImplementation(() => `data:image/png;base64,${++draws}`);
});
afterAll(() => vi.restoreAllMocks());

test('nothing blocked: the page\'s own title and its brand favicon, untouched', () => {
  updateTabSignal([calm, {}]);
  expect([document.title, icon()?.getAttribute('href'), icon()?.type, draws]).toEqual(['chela – Mission Control', 'static/v/abc/img/favicon.svg', 'image/svg+xml', 0]);
});

test('a waiting pane and a permission gate both count: the title says it, the favicon is badged amber', () => {
  updateTabSignal([waiting, gated, calm]);
  expect(document.title).toBe('(2) Needs you · chela – Mission Control');
  expect([icon()?.type, icon()?.getAttribute('href'), draws]).toEqual(['image/png', 'data:image/png;base64,1', 1]);
  expect(fills).toEqual(['#e3b341', '#1b1f24']); // the theme's --yellow disc, dark ink on it
  expect(fillText.mock.calls.at(-1)?.[0]).toBe('2');
});

test('the same count again touches nothing: no title churn, no redraw', () => {
  document.title = 'changed by hand';
  updateTabSignal([gated, waiting]);
  expect([document.title, draws]).toEqual(['changed by hand', 1]);
});

test('a new count redraws; more than nine reads 9+', () => {
  updateTabSignal(Array(12).fill(waiting));
  expect(document.title).toBe('(12) Needs you · chela – Mission Control');
  expect(draws).toBe(2);
  expect(fillText.mock.calls.at(-1)?.[0]).toBe('9+');
});

test('back to none restores the brand favicon and the base title', () => {
  updateTabSignal(null);
  expect([document.title, icon()?.getAttribute('href'), icon()?.type]).toEqual(['chela – Mission Control', 'static/v/abc/img/favicon.svg', 'image/svg+xml']);
});

test('a page with no favicon link gets one', () => {
  icon()?.remove();
  updateTabSignal([waiting]);
  expect(icon()?.type).toBe('image/png');
});
