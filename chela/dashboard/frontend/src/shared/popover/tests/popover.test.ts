// CMX-218: the legacy popover placement, moved from nav.js. tests/popover_placement.test.mjs
// still drives it end to end through nav.js's real menus and tap tooltips.
import { afterEach, expect, test } from 'vitest';
import { placePopover, placedPopover, popoverPosition } from '../index.ts';

const anchor = (top: number, left: number, width = 38, height = 38) => ({ top, left, width, bottom: top + height, right: left + width });

test('below the anchor when it fits, a gap under it, right-aligned', () => {
  expect(popoverPosition({ vw: 1440, vh: 900, anchor: anchor(20, 400), w: 210, h: 324 })).toEqual({ top: 64, left: 228, maxHeight: null });
});

test('above the anchor when below has no room (the sidebar-foot gear, 1920x916)', () => {
  expect(popoverPosition({ vw: 1920, vh: 916, anchor: anchor(868, 214), w: 210, h: 324 })).toEqual({ top: 868 - 6 - 324, left: 42, maxHeight: null });
});

test('the gap option sets the distance from the anchor', () => {
  expect(popoverPosition({ vw: 1440, vh: 900, anchor: anchor(20, 400), w: 210, h: 100 }, { gap: 4 }).top).toBe(62);
});

test('neither side has room: the roomier side, pushed back inside, both ways', () => {
  // below 462 vs above 400: below, clamped to 900 - 8 - 500
  expect(popoverPosition({ vw: 1440, vh: 900, anchor: anchor(400, 400), w: 210, h: 500 }).top).toBe(392);
  // above 470 vs below 392: above, clamped to the 8px margin
  expect(popoverPosition({ vw: 1440, vh: 900, anchor: anchor(470, 400), w: 210, h: 500 }).top).toBe(8);
});

test('taller than the viewport: capped to it less both margins, at the top margin', () => {
  expect(popoverPosition({ vw: 1920, vh: 916, anchor: anchor(868, 214), w: 210, h: 2000 })).toEqual({ top: 8, left: 42, maxHeight: 900 });
  // exactly the room fits: no cap
  expect(popoverPosition({ vw: 1920, vh: 916, anchor: anchor(868, 214), w: 210, h: 900 }).maxHeight).toBe(null);
});

test('clamped 8px inside the right and left edges', () => {
  expect(popoverPosition({ vw: 1440, vh: 900, anchor: anchor(20, 1420), w: 210, h: 100 }).left).toBe(1440 - 8 - 210);
  expect(popoverPosition({ vw: 1440, vh: 900, anchor: anchor(20, 0, 20), w: 210, h: 100 }).left).toBe(8);
});

test("align 'center' centres it on the anchor", () => {
  expect(popoverPosition({ vw: 1440, vh: 900, anchor: anchor(20, 300, 60, 30), w: 120, h: 40 }, { align: 'center' }).left).toBe(270);
});

// --- the DOM half: measure, apply, remember, re-place on resize --------------------------

function viewport(width: number, height: number) {
  Object.defineProperty(window, 'innerWidth', { value: width, configurable: true });
  Object.defineProperty(window, 'innerHeight', { value: height, configurable: true });
}

function el(rect: ReturnType<typeof anchor> | null, w = 0, h = 0) {
  const e = document.createElement('div');
  document.body.append(e);
  if (rect) e.getBoundingClientRect = () => ({ ...rect, x: rect.left, y: rect.top, height: rect.bottom - rect.top, toJSON: () => rect }) as DOMRect;
  Object.defineProperty(e, 'offsetWidth', { value: w, configurable: true });
  Object.defineProperty(e, 'offsetHeight', { value: h, configurable: true });
  return e;
}

afterEach(() => {
  document.body.innerHTML = '';
});

test('placePopover writes top/left, a cap only when too tall, and clears a stale cap', () => {
  viewport(1920, 916);
  const a = el(anchor(868, 214));
  const m = el(null, 210, 2000);
  placePopover(m, a);
  expect([m.style.top, m.style.left, m.style.maxHeight, m.style.overflowY]).toEqual(['8px', '42px', '900px', 'auto']);
  Object.defineProperty(m, 'offsetHeight', { value: 100, configurable: true });
  placePopover(m, a, { gap: 4 });
  expect([m.style.top, m.style.maxHeight, m.style.overflowY]).toEqual([`${868 - 4 - 100}px`, '', '']);
});

test('placePopover is a no-op without a popover or an anchor', () => {
  const m = el(null, 10, 10);
  placePopover(m, null);
  placePopover(null, m);
  expect(m.style.top).toBe('');
  expect(placedPopover(m)).toBeUndefined();
});

test('placedPopover remembers the anchor and the options, defaults filled in', () => {
  viewport(1440, 900);
  const a = el(anchor(20, 400));
  const m = el(null, 100, 100);
  placePopover(m, a, { align: 'center' });
  expect(placedPopover(m)).toEqual({ anchor: a, opts: { gap: 6, align: 'center' } });
  placePopover(m, a, { gap: 4 });
  expect(placedPopover(m)).toEqual({ anchor: a, opts: { gap: 4, align: 'right' } });
});

test('an open popover is re-placed on resize; a hidden one is dropped and left alone', () => {
  viewport(1440, 900);
  const a = el(anchor(400, 400));
  const open = el(null, 210, 324);
  const hidden = el(null, 210, 324);
  placePopover(open, a);
  placePopover(hidden, a);
  expect(open.style.top).toBe('444px');
  hidden.style.display = 'none';
  viewport(1440, 700); // 438 + 6 + 324 > 692: it must flip above
  window.dispatchEvent(new Event('resize'));
  expect(open.style.top).toBe(`${400 - 6 - 324}px`);
  expect(hidden.style.top).toBe('444px');
  expect(placedPopover(hidden)).toBeUndefined();
  expect(placedPopover(open)?.anchor).toBe(a);
});

test('a popover whose anchor left the page is dropped on resize', () => {
  viewport(1440, 900);
  const a = el(anchor(400, 400));
  const m = el(null, 210, 324);
  placePopover(m, a);
  a.remove();
  window.dispatchEvent(new Event('resize'));
  expect(placedPopover(m)).toBeUndefined();
});
