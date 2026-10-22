// CMX-221 (FRONTEND_PORT_PLAN.md §3): the iframe contract. A pane's shim (app.py
// _TERM_PALETTE_KEY_SHIM) calls exactly window.parent.chela.openPalette() and .toggleSettings();
// this fails if either goes missing, stops reaching its surface, or another is added.
import { afterEach, expect, test } from 'vitest';
import { closePalette, palette } from './features/palette/index.ts';
import { closeSettings, settings } from './features/settings/index.ts';
import { IFRAME_CONTRACT, installIframeBridge } from './iframeBridge.ts';

afterEach(() => {
  closePalette();
  closeSettings();
});

test('the contract is exactly openPalette and toggleSettings', () => {
  expect(Object.keys(IFRAME_CONTRACT).sort()).toEqual(['openPalette', 'toggleSettings']);
});

test('installing puts both on window.chela (beside what is there), and each reaches its surface', () => {
  const win = { chela: { other: 1 } } as unknown as Window;
  installIframeBridge(win);
  const ns = (win as unknown as { chela: Record<string, unknown> }).chela;
  expect(Object.keys(ns).sort()).toEqual(['openPalette', 'other', 'toggleSettings']);
  (ns.openPalette as () => void)();
  expect(palette.get().open).toBe(true);
  (ns.toggleSettings as (f?: string) => void)('notify');
  expect(settings.get()).toMatchObject({ open: true, tab: 'notifications' });
  (ns.toggleSettings as () => void)();
  expect(settings.get().open).toBe(false);
});

test('a page with no window.chela yet gets one', () => {
  const win = {} as Window;
  installIframeBridge(win);
  expect(typeof (win as unknown as { chela: { openPalette: unknown } }).chela.openPalette).toBe('function');
});
