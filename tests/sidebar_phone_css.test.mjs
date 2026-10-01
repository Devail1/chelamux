// CMX-404 — the phone sidebar drawer on a real iPhone (Safari). Two CSS causes:
//   1. `.sidebar { height: 100vh }`: iOS Safari's 100vh includes the strip under its
//      bottom toolbar, so the drawer's foot (readouts + the Settings/menu button)
//      sat behind it. Chromium has no such toolbar — vh == dvh there — so no
//      browser-driven test can see this one; it is held here, on the source.
//   2. `body.sidebar-collapsed …` rules outside the desktop media block: the class
//      is the DESKTOP icon-rail state, persisted per browser, and an unscoped rule
//      applied it inside the phone drawer (it hid the New session + Jump row). The
//      laid-out half of this lives in tests/browser/sidebar_phone_drawer.test.mjs;
//      this file holds the rule for EVERY such selector, including ones no fixture
//      happens to render.
//
// Run: node --test tests/sidebar_phone_css.test.mjs (tests/test_js_suites.py runs
// every .test.mjs inside pytest, by discovery).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.join(here, '..', 'chela', 'dashboard', 'static', 'style.css'), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '');

// Every style rule as { selector, body, media } — `media` is the prelude of the
// enclosing @-block chain ('' at top level). A tiny brace walker: style.css has no
// strings containing braces once comments are gone.
function rules(src) {
    const out = [];
    const stack = [];
    let start = 0;
    for (let i = 0; i < src.length; i++) {
        const c = src[i];
        if (c === '{') {
            const prelude = src.slice(start, i).trim();
            if (prelude.startsWith('@')) {
                stack.push(prelude);
                start = i + 1;
            } else {
                const end = src.indexOf('}', i);
                out.push({ selector: prelude, body: src.slice(i + 1, end), media: stack.join(' ') });
                i = end;
                start = end + 1;
            }
        } else if (c === '}') {
            stack.pop();
            start = i + 1;
        } else if (c === ';' && stack.length === 0 && src.slice(start, i).trim().startsWith('@')) {
            start = i + 1;   // @import / @charset
        }
    }
    return out;
}
const ALL = rules(css);

test('the walker actually sees the stylesheet (a parse that found nothing proves nothing)', () => {
    assert.ok(ALL.length > 500, `only ${ALL.length} rules parsed`);
    assert.ok(ALL.some(r => r.media.includes('max-width: 768px') && /(^|,)\s*\.sidebar\s*(,|$)/.test(r.selector)),
        'the phone .sidebar drawer rule was not found inside a max-width:768px block');
});

test('.sidebar\'s height is 100dvh, with a 100vh fallback declared BEFORE it', () => {
    const base = ALL.filter(r => r.media === '' && r.selector === '.sidebar');
    assert.equal(base.length, 1, 'expected exactly one top-level `.sidebar {…}` rule');
    const heights = [...base[0].body.matchAll(/(?:^|;)\s*height\s*:\s*([^;]+)/g)].map(m => m[1].trim());
    assert.deepEqual(heights, ['100vh', '100dvh'],
        `.sidebar heights are ${JSON.stringify(heights)} — the last must be 100dvh (iOS Safari's ` +
        '100vh runs under its bottom toolbar) and a 100vh fallback must precede it');
    // …and nothing more specific re-sets it to a bare vh height on a phone.
    for (const r of ALL) {
        if (!/(^|,)\s*\.sidebar\s*(\.open)?\s*(,|$)/.test(r.selector) || r === base[0]) continue;
        const h = [...r.body.matchAll(/(?:^|;)\s*height\s*:\s*([^;]+)/g)].map(m => m[1].trim());
        assert.ok(!h.some(v => /\bvh\b/.test(v)),
            `\`${r.selector}\` (${r.media || 'top level'}) re-sets height to ${JSON.stringify(h)}`);
    }
});

test('the drawer foot keeps its env(safe-area-inset-bottom) padding on a phone', () => {
    const foot = ALL.filter(r => r.media.includes('max-width: 768px') && r.selector === '.sidebar-foot');
    assert.ok(foot.some(r => /padding-bottom\s*:[^;]*env\(safe-area-inset-bottom\)/.test(r.body)),
        'no phone `.sidebar-foot` rule pads the bottom by env(safe-area-inset-bottom)');
});

test('every `body.sidebar-collapsed` rule is scoped to the desktop (min-width: 769px) block', () => {
    const collapsed = ALL.filter(r => r.selector.includes('sidebar-collapsed'));
    assert.ok(collapsed.length >= 15, `only ${collapsed.length} sidebar-collapsed rules found`);
    assert.ok(collapsed.some(r => /\.sidebar-quick\b/.test(r.selector)),
        'the desktop rail no longer hides .sidebar-quick (the accepted desktop behaviour)');
    const leaked = collapsed.filter(r => !/min-width:\s*769px/.test(r.media));
    assert.deepEqual(leaked.map(r => `${r.selector} [${r.media || 'top level'}]`), [],
        'these desktop icon-rail rules also apply inside the phone drawer');
});
