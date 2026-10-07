// THE DISMISS TOAST'S "Undo" BUTTON MUST NOT WRAP (CMX-22). The Recent-sessions dismiss
// toast (nav.js `_showRecentUndoToast`) is a flex row: a label span + an Undo button.
// `.run-toast` sets `word-break: break-word`, which the button inherits — so with a long
// session label ("Dismissed liavacc/cmx-20-retry-of-cmx-17-…") flex shrank the button to
// its min-content and split its text into "Un" / "do". The fix is pure CSS cascade (the
// button is `white-space: nowrap; flex-shrink: 0`, the label `min-width: 0` +
// `overflow-wrap: anywhere`), which no DOM assertion can see — so this runs the REAL
// style.css through jsdom and reads the CASCADED values with getComputedStyle, the same
// technique as tests/gs_files_pointer_events_css.test.mjs. jsdom does no layout, so the
// visual check (390px phone + desktop) was done in a real browser; this pins the rules.
//
// Run: node --test tests/recent_undo_toast_css.test.mjs (tests/test_js_suites.py runs
// every .test.mjs inside pytest, by discovery; needs `pnpm install` for jsdom).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';

const here = path.dirname(fileURLToPath(import.meta.url));
const styleCss = fs.readFileSync(path.join(here, '..', 'chela', 'dashboard', 'static', 'style.css'), 'utf8');

const LONG = 'Dismissed liavacc/cmx-20-retry-of-cmx-17-batch-per-window-tmuxpgrep-probes-via';

function render() {
    // Same markup nav.js `_showRecentUndoToast` writes (tests/sidebar_recent_sessions
    // .test.mjs pins the span's class on the real render), plus a NEGATIVE CONTROL: a
    // bare button in a plain .run-toast, which no CMX-22 rule targets.
    const dom = new JSDOM(`<!doctype html><html><head><style>${styleCss}</style></head><body>
  <div class="run-toast-stack" id="run-toast-stack">
    <div class="run-toast recent-undo-toast" role="status">
      <span class="recent-undo-text">${LONG}</span> <button class="recent-undo">Undo</button>
    </div>
    <div class="run-toast" id="control"><button>Undo</button></div>
  </div>
</body></html>`, { pretendToBeVisual: true });
    const doc = dom.window.document;
    const cs = el => dom.window.getComputedStyle(el);
    return {
        cs,
        toast: doc.querySelector('.recent-undo-toast'),
        text: doc.querySelector('.recent-undo-text'),
        btn: doc.querySelector('.recent-undo'),
        control: doc.querySelector('#control button'),
    };
}

test('the Undo button is nowrap and non-shrinking, so a long label cannot split it into "Un" / "do"', () => {
    const { cs, btn, control } = render();

    // Negative control: a button in a .run-toast that the CMX-22 rule doesn't target is
    // neither nowrap nor non-shrinking — proves the assertions below can fail (and that
    // the fix isn't some blanket rule that happens to cover every button).
    assert.notEqual(cs(control).whiteSpace, 'nowrap', 'control: a plain toast button should not be nowrap');
    assert.notEqual(cs(control).flexShrink, '0', 'control: a plain toast button should be shrinkable');

    // 🔴 GUARD: drop `white-space: nowrap` and the inherited `word-break: break-word`
    // lets "Undo" break mid-word once the button is squeezed.
    assert.equal(cs(btn).whiteSpace, 'nowrap', '.recent-undo must be white-space:nowrap');
    // 🔴 GUARD: drop `flex-shrink: 0` and flex squeezes the button below its label width.
    assert.equal(cs(btn).flexShrink, '0', '.recent-undo must be flex-shrink:0');
});

test('the label span absorbs the squeeze: min-width:0 + overflow-wrap:anywhere, in a flex row', () => {
    const { cs, toast, text } = render();
    assert.equal(cs(toast).display, 'flex', '.recent-undo-toast must be a flex row');
    assert.equal(cs(toast).justifyContent, 'space-between', 'Undo must sit at the row\'s end');
    // 🔴 GUARD: without min-width:0 a flex item can't shrink below its longest unbreakable
    // run, so the long label would push the button out instead of wrapping.
    assert.equal(cs(text).minWidth, '0px', '.recent-undo-text must be min-width:0');
    assert.equal(cs(text).overflowWrap, 'anywhere', '.recent-undo-text must wrap anywhere');
});
