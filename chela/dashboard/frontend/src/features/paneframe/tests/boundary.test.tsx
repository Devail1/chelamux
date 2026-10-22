// FRONTEND_PORT_PLAN.md §3: React renders the terminal <iframe> and owns its src, and never
// renders inside it. One test per row of §3's table of what crosses that boundary, each from
// the parent's side, against a frame PaneFrame drew.
import { expect, test } from 'vitest';
import { IFRAME_CONTRACT, installIframeBridge } from '../../../iframeBridge.ts';
import { applyTermFocus, focusFrame, framesOf, markReady, mountPaneFrames, suspendedPanes } from '../index.ts';
import { frame, must, stage, stubFetch } from './setup.ts';

function drawn(wid = '@1'): HTMLIFrameElement {
  stubFetch();
  markReady(wid);
  stage(wid);
  mountPaneFrames(document);
  return must(frame(wid));
}

test('§3 row 1, iframe → parent: window.parent.chela.openPalette() and .toggleSettings() exist for a pane to call', () => {
  const f = drawn();
  installIframeBridge(window);
  expect(must(f.contentWindow).parent).not.toBe(f.contentWindow); // it has a parent to call
  const chela = (window as Window & { chela?: Record<string, unknown> }).chela;
  expect(typeof chela?.openPalette).toBe('function');
  expect(typeof chela?.toggleSettings).toBe('function');
  expect(Object.keys(IFRAME_CONTRACT).sort()).toEqual(['openPalette', 'toggleSettings']);
});

test('§3 row 2, iframe → parent postMessage (presence): the frame is same-origin and unsandboxed, so the shim posts to location.origin', () => {
  const f = drawn();
  expect(new URL(must(f.getAttribute('src')), location.href).origin).toBe(location.origin);
  expect(f.hasAttribute('sandbox')).toBe(false); // a sandbox would give it an opaque origin
});

test('§3 row 3, iframe reads parent: a suspended frame is src="about:blank" with data-suspended-src on its frameElement', () => {
  const f = drawn();
  const src = f.getAttribute('src');
  suspendedPanes.set(new Set(['@1']));
  expect(f.getAttribute('src')).toBe('about:blank');
  expect(f.getAttribute('data-suspended-src')).toBe(src);
  expect(must(f.contentWindow).frameElement).toBe(f);
  suspendedPanes.set(new Set());
  expect(f.getAttribute('src')).toBe(src);
  expect(f.hasAttribute('data-suspended-src')).toBe(false);
});

test('§3 row 4, iframe reads storage (chela_theme, chela_term_*): same origin, so the pane sees the parent\'s localStorage', () => {
  const f = drawn();
  expect(new URL(must(f.getAttribute('src')), location.href).host).toBe(location.host);
  expect(f.hasAttribute('sandbox')).toBe(false);
  expect(f.hasAttribute('credentialless')).toBe(false); // would partition its storage
});

test('§3 row 5, parent → iframe contentWindow.term: focusFrame routes into xterm', () => {
  const f = drawn();
  let focused = false;
  (must(f.contentWindow) as Window & { term?: unknown }).term = { focus: () => { focused = true; } };
  focusFrame(f);
  expect(focused).toBe(true);
});

test('§3 row 6, parent → iframe contentDocument: the focus ring and the pane keys read and wire it', () => {
  const f = drawn();
  const doc = must(f.contentDocument);
  doc.hasFocus = () => true;
  applyTermFocus();
  expect(must(f.parentElement?.closest('[data-pane]')).classList.contains('term-focused')).toBe(true);
  const added: string[] = [];
  const orig = doc.addEventListener.bind(doc);
  doc.addEventListener = ((type: string, fn: EventListener, opts?: boolean) => { added.push(`${type}:${String(opts)}`); orig(type, fn, opts); }) as typeof doc.addEventListener;
  f.dispatchEvent(new Event('load'));
  expect(added).toEqual(['keydown:true', 'keyup:true']); // capture phase, before xterm
});

test('§3 row 7, parent → iframe contentWindow.location.reload(): the share feature reloads the frames framesOf finds', () => {
  const f = drawn('@2');
  drawn('@3');
  expect(framesOf('@2')).toEqual([f]); // what terminals.js hands configureShareHost as paneFrames
  expect(typeof must(f.contentWindow).location.reload).toBe('function');
});
