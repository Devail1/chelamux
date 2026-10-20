// What jsdom lacks and Radix's popper and pointer handling call.
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
};
Element.prototype.hasPointerCapture ??= () => false;
Element.prototype.releasePointerCapture ??= () => {};
Element.prototype.scrollIntoView ??= () => {};

afterEach(() => cleanup());

/** `x`, or a test failure naming what was missing (instead of a non-null assertion). */
export function must<T>(x: T | null | undefined, what = 'element'): T {
  if (x === null || x === undefined) throw new Error(`missing ${what}`);
  return x;
}
