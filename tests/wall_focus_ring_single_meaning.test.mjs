// CMX-104: ONE WHOLE-PANE RING STYLE, AND IT MEANS "THE KEYBOARD IS HERE".
// Liav (red-weak) saw two rings on a two-pane Wall — the .term-focused overlay on one
// pane, Focus layout's .wall-focus-main 1px accent outer ring on the other — and could
// not tell which was active (he read one as an "orchestrator ring"; no such styling
// exists). Waiting was the focus ring's twin told apart by hue alone, a room drew a
// third ring, and a MAXIMIZED pane drew a second, concentric inset ring under the focus
// overlay. The rule now: the crisp ring — a zero-offset, zero-blur, spread-only
// box-shadow — is painted ONLY by .term-focused's ::before overlay. Every other
// whole-pane state is a different KIND of mark (waiting = a blurred glow, room = a
// header stripe, pinned = a dashed border) with a word/icon in the header.
//
// The pane element's own box-shadow is read through jsdom's cascade
// (getComputedStyle); the ::before overlay (which jsdom cannot resolve) through the
// CSSOM of the REAL style.css, as tests/wall_focus_ring_overlay_css.test.mjs does.
//
// Run: pnpm exec vitest run tests/wall_focus_ring_single_meaning.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `pnpm install` for jsdom).
import { test } from 'vitest';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';   // needs `pnpm install` — tests/test_js_suites.py enforces it

const here = path.dirname(fileURLToPath(import.meta.url));
const styleCss = fs.readFileSync(path.join(here, '..', 'chela', 'dashboard', 'static', 'style.css'), 'utf8');

// The persistent (non-drag) whole-pane states a Wall pane can sit in, each as
// [label, class on .grid-stack-item, class on .grid-stack-item-content].
const STATES = [
    ['waiting', '', 'term-waiting'],
    ['in a room', '', 'in-room'],
    ['pinned', 'pane-pinned', ''],
    ['the Focus layout main pane', 'wall-focus-main', ''],
    ['in the Focus layout strip', 'wall-focus-strip', ''],
    ['maximized', '', 'pane-maximized'],
    ['showing an action bar', '', 'has-term-action'],
];

const tileHTML = (id, itemCls, contentCls) => `
  <div class="grid-stack-item ${itemCls}" id="${id}-item">
    <div class="grid-stack-item-content ${contentCls}" id="${id}" style="--room-accent: #e69f00">
      <div class="gs-head" id="${id}-head"></div><iframe class="term-frame"></iframe>
    </div>
  </div>`;

const dom = new JSDOM(`<!doctype html><html><head><style>${styleCss}</style></head>
<body class="pane-is-maximized">
  ${STATES.map(([, i, c], n) => tileHTML(`s${n}`, i, c)).join('')}
  ${tileHTML('maxfocus', '', 'pane-maximized term-focused')}
</body></html>`, { pretendToBeVisual: true });
const win = dom.window;

const RULES = [...win.document.styleSheets[0].cssRules].filter((r) => r.selectorText);
const sel = (r) => r.selectorText.split(',').map((x) => x.trim());
const lastDecl = (selector, prop) => {
    const hits = RULES.filter((r) => sel(r).includes(selector) && r.style.getPropertyValue(prop));
    return hits.length ? hits[hits.length - 1].style.getPropertyValue(prop) : '';
};

// Split a box-shadow list on top-level commas (not the ones inside rgba()/var()).
function layers(shadow) {
    const out = []; let depth = 0, cur = '';
    for (const ch of shadow || '') {
        if (ch === '(') depth++;
        if (ch === ')') depth--;
        if (ch === ',' && depth === 0) { out.push(cur.trim()); cur = ''; } else cur += ch;
    }
    if (cur.trim()) out.push(cur.trim());
    return out.filter((l) => l && l !== 'none');
}
// A crisp ring: x=0, y=0, blur=0 and a positive spread — what the focus ring is.
const RING = /^(inset\s+)?0(px)?\s+0(px)?\s+0(px)?\s+[1-9][\d.]*px\b/;
const ringLayers = (shadow) => layers(shadow).filter((l) => RING.test(l));
const boxShadow = (el) => win.getComputedStyle(el).boxShadow;

test('the focus overlay IS a crisp ring (or "no other state is a ring" below is vacuous)', () => {
    assert.equal(ringLayers(lastDecl('.grid-stack-item-content.term-focused::before', 'box-shadow')).length, 1);
});

for (const [label, , contentCls] of STATES) {
    const n = STATES.findIndex((s) => s[0] === label);
    test(`CMX-104: a pane that is ${label} draws no focus-like ring on the pane, its tile, its header or the overlay`, () => {
        const d = win.document;
        for (const el of [d.getElementById(`s${n}`), d.getElementById(`s${n}-item`), d.getElementById(`s${n}-head`)]) {
            const shadow = boxShadow(el);
            assert.deepEqual(ringLayers(shadow), [],
                `${label}: #${el.id} computes box-shadow "${shadow}" — a crisp spread ring reads as keyboard focus`);
        }
        if (contentCls) {
            const over = lastDecl(`.grid-stack-item-content.${contentCls}::before`, 'box-shadow');
            assert.equal(over, '', `${label}: .${contentCls}::before paints the focus overlay ("${over}")`);
        }
    });
}

test('CMX-104: waiting stays visible — a blurred glow, not nothing', () => {
    const shadow = boxShadow(win.document.getElementById(`s${STATES.findIndex((s) => s[0] === 'waiting')}`));
    assert.ok(layers(shadow).some((l) => /var\(--yellow\)/.test(l) && !RING.test(l)),
        `the waiting pane must keep a yellow glow, got "${shadow}"`);
});

test('CMX-104: a room is still marked — a stripe on the header, in the room colour', () => {
    const head = win.document.getElementById(`s${STATES.findIndex((s) => s[0] === 'in a room')}-head`);
    assert.match(boxShadow(head), /var\(--room-accent/, 'the in-room header stripe is gone');
});

test('CMX-104: a maximized + focused pane has exactly ONE ring source — the focus overlay', () => {
    const d = win.document;
    const sources = [];
    for (const el of [d.getElementById('maxfocus'), d.getElementById('maxfocus-item'), d.getElementById('maxfocus-head')]) {
        if (ringLayers(boxShadow(el)).length) sources.push(`#${el.id} (${boxShadow(el)})`);
    }
    for (const cls of ['pane-maximized', 'term-focused']) {
        const over = lastDecl(`.grid-stack-item-content.${cls}::before`, 'box-shadow');
        if (ringLayers(over).length) sources.push(`.${cls}::before (${over})`);
    }
    assert.deepEqual(sources, ['.term-focused::before (inset 0 0 0 2px var(--accent))'],
        'a maximized pane must not add its own frame to the focus ring — two concentric rings');
});
