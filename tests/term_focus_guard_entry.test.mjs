// CMX-506: term-focus.js's entry guard — it patches focus() only inside a pane, and only once.
//
// static/term-focus.js (CMX-493) wraps HTMLElement.prototype.focus so a pane that reconnects
// cannot take the keyboard from a sibling. Its first line returns early in two cases:
//   - the page is NOT framed (window.parent === window): a /term/<wid>/ page opened on its own
//     tab has no Wall to defer to, and window.parent.document would be its own document;
//   - the guard already ran in this window (__chelaFocusGuard): a second copy would wrap the
//     wrapper and register every gesture listener twice.
// tests/browser/wall_focus_kept.test.mjs measures the guard's behaviour in Chromium, but it
// skips where no browser is installed and never loads a pane unframed. This runs the real file
// in a vm against a stand-in window, so both early returns are checked on every suite run.
import { describe, test } from 'vitest';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const SRC = readFileSync(new URL('../chela/dashboard/static/term-focus.js', import.meta.url), 'utf8');

/** Run term-focus.js in a fresh realm. `framed` gives the window a distinct parent. */
function load({ framed, guarded = false } = {}) {
    const listeners = [];
    const originalFocus = function () { return 'focused'; };
    class HTMLElement {}
    HTMLElement.prototype.focus = originalFocus;
    const win = {
        addEventListener: (type) => listeners.push(type),
        frameElement: null,
    };
    win.parent = framed ? { document: { activeElement: null, querySelectorAll: () => [] } } : win;
    if (guarded) win.__chelaFocusGuard = true;
    const ctx = {
        window: win,
        HTMLElement,
        document: { hasFocus: () => true },
        performance: { now: () => 0 },
    };
    vm.runInNewContext(SRC, ctx);
    return { win, listeners, originalFocus, HTMLElement };
}

describe('term-focus.js entry guard', () => {
    test('an unframed page (window.parent === window) is left alone: focus, listeners, flag', () => {
        const { win, listeners, originalFocus, HTMLElement } = load({ framed: false });
        assert.equal(HTMLElement.prototype.focus, originalFocus, 'focus() was wrapped on a top-level page');
        assert.deepEqual(listeners, [], 'gesture listeners were added on a top-level page');
        assert.equal(win.__chelaFocusGuard, undefined, 'the guard flag was set on a top-level page');
    });

    test('a pane the guard already ran in is not wrapped or listened to a second time', () => {
        const { listeners, originalFocus, HTMLElement } = load({ framed: true, guarded: true });
        assert.equal(HTMLElement.prototype.focus, originalFocus, 'focus() was wrapped a second time');
        assert.deepEqual(listeners, [], 'gesture listeners were registered a second time');
    });

    test('control: a framed pane loading it the first time is wrapped and flagged', () => {
        const { win, listeners, originalFocus, HTMLElement } = load({ framed: true });
        assert.notEqual(HTMLElement.prototype.focus, originalFocus);
        assert.equal(win.__chelaFocusGuard, true);
        assert.deepEqual(listeners, ['pointerdown', 'mousedown', 'touchstart', 'touchend', 'keydown']);
        // this pane holds the keyboard (document.hasFocus()), so the call goes through
        assert.equal(new HTMLElement().focus(), 'focused');
    });
});
