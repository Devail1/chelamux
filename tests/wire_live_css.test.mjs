// THE WIRE-LIVE CONTENT-COLLAPSE BUG (CMX-120, root-caused live 2026-07-20) — a pure
// CSS-cascade regression that no JS test could catch: starting a wire ("Wire to…")
// added `#term-stage.wire-live .grid-stack-item > .grid-stack-item-content
// { position: relative; }` to give the drop-socket `::after` a positioned ancestor.
// But `.grid-stack-item-content` is ALREADY positioned — gridstack's own vendor CSS
// sets `position: absolute; inset: 0` on it to fill the tile — and that id-selector
// rule outranks gridstack's in specificity, so it clobbered `absolute` with
// `relative`. The content then fell back to its intrinsic flex-column height
// (~200px) while the tile box (`.grid-stack-item`, and its resize handles) stayed
// full height: only the content visibly collapsed.
//
// This runs the REAL stylesheets (gridstack's vendor CSS + style.css) through jsdom
// and reads the CASCADED value with getComputedStyle — jsdom resolves CSS cascade/
// specificity (it just can't do layout geometry), which is exactly what this bug is
// made of. Re-adding a `position` declaration to that `#term-stage.wire-live
// .grid-stack-item > .grid-stack-item-content` selector reintroduces the bug and
// this goes red.
//
// Covers BOTH `.wire-live` rules that touch `.grid-stack-item-content` — the
// plain (unhovered) tile AND the `.wire-target` tile (the one being hovered as a
// drop target during the same gesture). They are separate selectors in style.css
// and a `position` regression on either one independently collapses that tile's
// content, so each needs its own fixture/assertion — a fixture that only ever
// renders the plain-tile markup can't catch a regression added to the
// `.wire-target` rule (and vice versa).
//
// Run: node --test tests/wire_live_css.test.mjs  (pytest runs it via
// tests/test_js_suites.py; needs `npm ci` for jsdom).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';   // needs `npm ci` — tests/test_js_suites.py enforces it

const here = path.dirname(fileURLToPath(import.meta.url));
const staticDir = path.join(here, '..', 'chela', 'dashboard', 'static');
const gridstackCss = fs.readFileSync(path.join(staticDir, 'vendor', 'gridstack.min.css'), 'utf8');
const styleCss = fs.readFileSync(path.join(staticDir, 'style.css'), 'utf8');

function wireLiveContentPosition(itemClass) {
    const dom = new JSDOM(`<!doctype html><html><head>
<style>${gridstackCss}</style>
<style>${styleCss}</style>
</head><body>
  <div id="term-stage" class="wire-live">
    <div class="grid-stack">
      <div class="${itemClass}">
        <div class="grid-stack-item-content">pane content</div>
      </div>
    </div>
  </div>
</body></html>`, { pretendToBeVisual: true });
    const content = dom.window.document.querySelector('.grid-stack-item-content');
    return dom.window.getComputedStyle(content).position;
}

function assertStaysAbsolute(position, label) {
    assert.notEqual(position, 'relative',
        `a \`position\` override on the ${label} selector clobbers gridstack's ` +
        '`absolute; inset:0` and collapses pane content to its intrinsic ~200px ' +
        'height — see CMX-120');
    assert.equal(position, 'absolute',
        `gridstack fills the tile via \`position: absolute; inset: 0\` on ` +
        `.grid-stack-item-content — this must survive ${label} untouched`);
}

test('a live wire drag must not collapse pane content: .grid-stack-item-content ' +
     'stays `absolute` (gridstack\'s fill-the-tile default), never `relative`', () => {
    assertStaysAbsolute(wireLiveContentPosition('grid-stack-item'), '`.wire-live .grid-stack-item`');
});

test('a live wire drag must not collapse the HOVERED drop-target tile\'s content: ' +
     '.grid-stack-item-content stays `absolute` on `.wire-target` too', () => {
    assertStaysAbsolute(wireLiveContentPosition('grid-stack-item wire-target'), '`.wire-target`');
});
