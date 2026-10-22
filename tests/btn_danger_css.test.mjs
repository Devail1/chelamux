// THE INVISIBLE "Del" BUTTON (CMX-101) — a pure CSS-cascade regression. style.css carried
// two `.btn-danger` rules: a legacy one (`background: #da3633`) and a later redesign one that
// set `color: var(--red)` but never reset the legacy fill, so Work → Schedules' "Del" (and the
// agent detail's "Stop") rendered red text on a red fill.
//
// Same recipe as tests/wire_live_css.test.mjs: the REAL style.css under jsdom, read through
// getComputedStyle (jsdom resolves cascade + custom properties, not layout). jsdom leaves a
// `var(--x)` colour unresolved in the computed value, so resolve() reads the custom property
// off <body> for the theme under test. `:hover` can't be triggered in jsdom, so the hover pair
// is read straight off the parsed CSSOM rule and resolved the same way.
//
// Run: pnpm exec vitest run tests/btn_danger_css.test.mjs (tests/test_js_suites.py runs every
// .test.mjs inside pytest; needs `pnpm install` for jsdom).
import { test } from 'vitest';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';   // needs `pnpm install` — tests/test_js_suites.py enforces it

const here = path.dirname(fileURLToPath(import.meta.url));
const staticDir = path.join(here, '..', 'chela', 'dashboard', 'static');
const styleCss = fs.readFileSync(path.join(staticDir, 'style.css'), 'utf8');
const settingsModel = fs.readFileSync(path.join(staticDir, '..', 'frontend', 'src', 'features', 'settings', 'model.ts'), 'utf8');

// The Settings picker's theme list — the same source tests/test_on_accent_contrast.py reads.
const THEMES = [...settingsModel.match(/const THEME_LABELS(?::[^=]*)? = \{([\s\S]*?)\};/)[1].matchAll(/(\w+):\s*'/g)]
    .map(m => m[1]);

const AA = 4.5;

function mount(theme) {
    const dom = new JSDOM(`<!doctype html><html><head><style>${styleCss}</style></head>
<body data-theme="${theme}"><table><tbody><tr><td>
  <button onclick="">Disable</button>
  <button class="btn-danger" onclick="">Del</button>
</td></tr></tbody></table></body></html>`, { pretendToBeVisual: true });
    const w = dom.window;
    const bodyStyle = w.getComputedStyle(w.document.body);
    const resolve = (value) => {
        let v = String(value).trim();
        for (let i = 0; i < 5; i++) {
            const m = v.match(/^var\(\s*(--[\w-]+)\s*\)$/);
            if (!m) break;
            v = bodyStyle.getPropertyValue(m[1]).trim();
        }
        return v;
    };
    return { w, resolve, btn: w.document.querySelector('.btn-danger') };
}

// -> [r, g, b, a] (0-255, a 0-1), or throws on a value it can't read.
function rgba(value) {
    const v = value.trim().toLowerCase();
    if (v === 'transparent') return [0, 0, 0, 0];
    let m = v.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/);
    if (m) {
        const h = m[1].length === 3 ? [...m[1]].map(c => c + c).join('') : m[1];
        return [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16)).concat(1);
    }
    m = v.match(/^rgba?\(([^)]+)\)$/);
    if (m) {
        const p = m[1].split(',').map(s => parseFloat(s));
        return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
    }
    throw new Error(`unreadable colour: ${value}`);
}

function luminance([r, g, b]) {
    const lin = [r, g, b].map(c => {
        c /= 255;
        return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
}

function contrast(a, b) {
    const [hi, lo] = [luminance(rgba(a)), luminance(rgba(b))].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
}

function cssRules(w, selector) {
    const out = [];
    const walk = (rules) => {
        for (const r of rules) {
            // A CSSStyleRule exposes cssRules too (CSS nesting) — test selectorText first.
            if (r.selectorText) {
                if (r.selectorText.split(',').map(s => s.trim()).includes(selector)) out.push(r);
            } else if (r.cssRules) walk(r.cssRules);
        }
    };
    for (const sheet of w.document.styleSheets) walk(sheet.cssRules);
    return out;
}

test('.btn-danger is defined by ONE rule (and one :hover) — no legacy rule left to leak a fill', () => {
    const { w } = mount('dark');
    assert.equal(cssRules(w, '.btn-danger').length, 1,
        'two `.btn-danger` rules is the CMX-101 bug shape: the later one sets the ink, the ' +
        'earlier one still paints the fill');
    assert.equal(cssRules(w, '.btn-danger:hover').length, 1);
});

for (const theme of THEMES) {
    test(`[${theme}] resting .btn-danger: no fill, label differs from its background and meets AA`, () => {
        const { w, resolve, btn } = mount(theme);
        const cs = w.getComputedStyle(btn);
        // jsdom computes a `background: var(--x)` shorthand (the base `button` rule's
        // var(--border) fill) as a TRANSPARENT background-color, and keeps the var() only in
        // the cascaded `background` value — so read that first, or a missing reset passes.
        const shorthand = cs.getPropertyValue('background').trim();
        const bg = resolve(/^var\(/.test(shorthand) ? shorthand : cs.backgroundColor);
        const ink = resolve(cs.color);
        assert.equal(rgba(bg)[3], 0,
            `resting .btn-danger must be outlined, not filled — got background ${bg} ` +
            '(a solid fill under the red redesign ink is the invisible-"Del" bug)');
        assert.notDeepEqual(rgba(ink), rgba(bg), `label ${ink} on background ${bg}`);
        // A transparent button shows whatever it sits on: the page (--bg) or a card (--surface).
        for (const backdrop of ['--bg', '--surface']) {
            const under = resolve(`var(${backdrop})`);
            const ratio = contrast(ink, under);
            assert.ok(ratio >= AA,
                `[${theme}] label ${ink} on ${backdrop} ${under} is ${ratio.toFixed(2)}:1, below AA`);
        }
        // jsdom computes a var() border-color shorthand as black, so read the rule itself.
        const [rest] = cssRules(w, '.btn-danger');
        assert.equal(resolve(rest.style.getPropertyValue('border-color')), resolve('var(--red)'),
            'the red outline (with the label) is what marks the button as destructive');
    });

    test(`[${theme}] hovered .btn-danger: solid red fill, ink meets AA against it`, () => {
        const { w, resolve } = mount(theme);
        const [hover] = cssRules(w, '.btn-danger:hover');
        const fill = resolve(hover.style.getPropertyValue('background') ||
                             hover.style.getPropertyValue('background-color'));
        const ink = resolve(hover.style.getPropertyValue('color'));
        assert.equal(fill, resolve('var(--red)'), 'hover fills with the theme --red');
        const ratio = contrast(ink, fill);
        assert.ok(ratio >= AA,
            `[${theme}] hover ink ${ink} on ${fill} is ${ratio.toFixed(2)}:1, below AA`);
    });
}
