// CMX-95 slice E: the update banner's dismiss ✕ matches the Reload button beside it — a
// 24px-high box with a 24×24 CSS-px minimum target (WCAG 2.5.8) and a 14px glyph — while a
// toolbar `.icon-btn` elsewhere keeps its 38×38 / 17px size. Same recipe as
// tests/wire_live_css.test.mjs: the REAL style.css in jsdom, the CASCADED value via
// getComputedStyle (jsdom resolves cascade/specificity, not layout geometry). The banner is
// the one version.js really builds, not a hand-made fixture.
//
// Run: pnpm exec vitest run tests/update_banner_close_size.test.mjs (tests/test_js_suites.py runs it).
import { beforeAll, test } from 'vitest';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';   // needs `pnpm install` — tests/test_js_suites.py enforces it

const here = path.dirname(fileURLToPath(import.meta.url));
const styleCss = fs.readFileSync(
    path.join(here, '..', 'chela', 'dashboard', 'static', 'style.css'), 'utf8');

let win;

beforeAll(async () => {
    const dom = new JSDOM(`<!doctype html><html><head><style>${styleCss}</style></head><body>
<button class="icon-btn" id="btn-decisions" aria-label="Decisions inbox">x</button>
</body></html>`, { url: 'http://localhost:5005/' });
    win = dom.window;
    win.CHELA_ASSET_VERSION = 'aaaaaaaaaaaa';
    for (const k of ['window', 'document']) {
        Object.defineProperty(globalThis, k, { value: win[k], writable: true, configurable: true });
    }
    const { applyVersion } = await import('../chela/dashboard/static/js/version.js');
    assert.equal(applyVersion('bbbbbbbbbbbb'), true);   // raises the real banner
});

test('the banner ✕ is 24px high with a 24×24 minimum target and a 14px glyph', () => {
    const close = document.getElementById('update-banner-close');
    assert.ok(close.closest('.update-banner'), 'the ✕ must live inside .update-banner');
    const cs = win.getComputedStyle(close);
    assert.equal(cs.height, '24px');
    assert.equal(cs.minWidth, '24px');
    assert.equal(cs.minHeight, '24px');
    assert.equal(cs.fontSize, '14px');
    assert.equal(cs.width, 'auto');
});

test('a toolbar .icon-btn outside the banner keeps its 38×38 / 17px size', () => {
    const cs = win.getComputedStyle(document.getElementById('btn-decisions'));
    assert.equal(cs.width, '38px');
    assert.equal(cs.height, '38px');
    assert.equal(cs.fontSize, '17px');
});
