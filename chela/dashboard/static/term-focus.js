// CMX-493: a pane never takes the keyboard by itself. Injected into ttyd's page by term_http,
// before ttyd's own (body) script.
//
// ttyd calls terminal.focus() every time its socket opens (onSocketOpen): on load, on each
// term-reconnect.js reload, for a pane the wall just added. The panes are same-origin frames, so
// that focus() takes the keyboard from whichever pane the user is typing in: its ring and cursor
// go, and the keys land in the pane that reconnected. So a focus() the page calls on itself is
// dropped while the user is typing somewhere else on the dashboard:
//   - another pane holds the keyboard, or is the one it returns to (the window is in the
//     background, or that pane is itself mid-reload), or
//   - a text field of the dashboard does (a rename, the palette).
// Still allowed: a focus right after a press or a key in this pane, one while this pane already
// holds the keyboard, and the Wall's focusFrame (it focuses the frame element first).
(function () {
    'use strict';
    if (window.parent === window || window.__chelaFocusGuard) return;
    window.__chelaFocusGuard = true;
    var GESTURE_MS = 1000;
    var gesture = -Infinity;
    ['pointerdown', 'mousedown', 'touchstart', 'touchend', 'keydown'].forEach(function (t) {
        window.addEventListener(t, function (e) { if (e.isTrusted) gesture = performance.now(); }, true);
    });

    function takenElsewhere() {
        if (document.hasFocus()) return false;
        if (performance.now() - gesture < GESTURE_MS) return false;
        var page = window.parent.document;
        var me = window.frameElement;
        var ae = page.activeElement;
        if (ae && ae !== me
            && (/^(INPUT|TEXTAREA|SELECT)$/.test(ae.tagName) || ae.isContentEditable
                || ae.classList.contains('term-frame'))) return true;
        // activeElement can lag behind the pane that really holds the keyboard (CMX-104)
        return Array.prototype.some.call(page.querySelectorAll('iframe.term-frame'), function (f) {
            return f !== me && f.contentDocument.hasFocus();
        });
    }

    var focus = HTMLElement.prototype.focus;
    HTMLElement.prototype.focus = function () {
        var taken = false;
        try { taken = takenElsewhere(); } catch (e) { /* a parent or sibling we may not read */ }
        if (!taken) return focus.apply(this, arguments);
        return undefined;
    };
})();
