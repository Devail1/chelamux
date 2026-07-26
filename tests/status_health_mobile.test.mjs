// CMX-179 round-2 fix — the #status-health-warn pill overflowed .topbar-actions at a
// 375px viewport (feed down => pill visible), pushing #btn-decisions off-screen and
// causing horizontal page scroll. jsdom has no real layout engine (no scrollWidth
// geometry), so this cannot reproduce the pixel overflow itself — that pixel check is
// the reviewer's to run. What jsdom CAN resolve is CSS cascade/specificity, so this
// guards the two declarations that make the pill shrinkable instead of a fixed-width
// nowrap block: `.status-health-warn` must be allowed to shrink (min-width: 0, a
// non-zero flex-shrink), and its text must ellipsize rather than overflow. The glyph
// stays a separate, non-shrinking element — only the word may be truncated, so glyph +
// word (never colour alone) both still render as far as space allows.
//
// Run: node --test tests/status_health_mobile.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `npm ci` for jsdom).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';   // needs `npm ci` — tests/test_js_suites.py enforces it

const here = path.dirname(fileURLToPath(import.meta.url));
const staticDir = path.join(here, '..', 'chela', 'dashboard', 'static');
const styleCss = fs.readFileSync(path.join(staticDir, 'style.css'), 'utf8');
const html = fs.readFileSync(path.join(here, '..', 'chela', 'dashboard', 'templates', 'index.html'), 'utf8');

function dom() {
    return new JSDOM(`<!doctype html><html><head><style>${styleCss}</style></head><body>` +
        `<div class="topbar-actions">` +
        `<span class="status-health-warn" id="status-health-warn">` +
        `<span aria-hidden="true">&#9888;</span><span class="shw-text">agent status unavailable</span>` +
        `</span>` +
        `<button class="icon-btn" id="btn-decisions"></button>` +
        `</div></body></html>`, { pretendToBeVisual: true });
}

test('index.html wraps the marker word in .shw-text (not a bare text node) so it can be ' +
     'targeted for ellipsis independently of the fixed-width glyph', () => {
    const m = html.match(/<span class="status-health-warn"[\s\S]*?<\/span>\s*<\/span>/);
    assert.ok(m, 'could not find the #status-health-warn markup block in index.html');
    assert.match(m[0], /<span class="shw-text">agent status unavailable<\/span>/,
        'the word "agent status unavailable" must be in its own .shw-text span');
});

test('.status-health-warn is allowed to shrink inside .topbar-actions (min-width: 0, ' +
     'non-zero flex-shrink) instead of forcing its full nowrap width', () => {
    const el = dom().window.document.querySelector('.status-health-warn');
    const cs = dom().window.getComputedStyle(el);
    assert.equal(cs.minWidth, '0px',
        'without min-width: 0 a flex item never shrinks below its content size — the ' +
        'nowrap pill would keep forcing .topbar-actions wider than the viewport');
    assert.notEqual(cs.flexShrink, '0',
        'flex-shrink: 0 (or the default with no min-width override) would pin this pill ' +
        'at its full width and push #btn-decisions off-screen, as it did in round 2');
});

test('.shw-text ellipsizes instead of overflowing once the pill is forced to shrink', () => {
    const el = dom().window.document.querySelector('.shw-text');
    const cs = dom().window.getComputedStyle(el);
    assert.equal(cs.overflow, 'hidden');
    assert.equal(cs.textOverflow, 'ellipsis');
    assert.equal(cs.whiteSpace, 'nowrap');
});
