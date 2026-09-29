// THE SIDEBAR+CANVAS SHELL GRID — CSS-SOURCE GUARD (CMX-377 round 2) +
// CASCADE-RESOLVED GUARD (CMX-377 round 3, defeat_shapes #377b).
//
// jsdom does no layout: it happily builds a DOM for `.canvas { grid-row: 2 }`
// and reports nothing wrong, because there is no layout engine underneath to
// notice that `.app` only ever declares ONE row. The bug this guards against
// shipped exactly that way — a leftover `grid-row: 2` from the removed topbar
// row, caught only by opening the branch in a real browser (PR #529 round 2):
// `.canvas` auto-placed at column 1 / row 2 (nothing occupies column 2 at all),
// so the Wall rendered off-screen below the sidebar and the whole right side
// of the viewport was blank. 4000+ green jsdom tests never saw it.
//
// Round 2's fix was a SOURCE assertion anchored to the exact top-level
// `.canvas { ... }` rule text. Round 3's judge defeated it with a SECOND rule,
// `.app > .canvas { grid-row: 2; }`, appended after the first: a different
// selector string, so `_topLevelRuleBody('\\.canvas')`'s `^\.canvas\s*\{`
// anchor never matches it, but `.app > .canvas` has HIGHER specificity
// (0,2,0 vs `.canvas` alone's 0,1,0) and so wins the cascade in a real
// browser regardless of source order — recreating the exact off-screen-Wall
// bug the round-2 guard exists to catch, invisibly to a guard that only reads
// one selector's source text (defeat_shapes #377b).
//
// The fix is not a longer regex (any selector shape reaching `.canvas` —
// `.app .canvas`, `#canvas`, an attribute qualifier, `.canvas.foo` — is a new
// string to anchor against, and the list never closes). It's asking the
// question jsdom CAN actually answer honestly for a non-layout property like
// `grid-row`/`grid-column`: jsdom's CSSOM resolves the CASCADE (specificity,
// then source order) even though it never computes on-screen box positions
// (confirmed empirically — see below). Mounting the real stylesheet against
// the real `.app > .sidebar` + `.app > .canvas` structure and reading
// `getComputedStyle(canvas).gridRow` answers "which rule actually wins,"
// which is the fact round 2's source-only guard could only approximate.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { JSDOM } from 'jsdom';   // needs `pnpm install` — tests/test_js_suites.py enforces it
import { cssForViewport, DESKTOP, DESKTOP_DARK } from './js_helpers/css_viewport.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', 'chela', 'dashboard');
const CSS = readFileSync(join(ROOT, 'static', 'style.css'), 'utf8');

// Anchored at column 0 (no leading whitespace) so this matches ONLY the
// top-level `.canvas { ... }` rule — not the `@media (max-width: 768px)`
// block's indented `.canvas { grid-column: 1; ... }` override, which
// legitimately drops the sidebar out of the grid entirely for the off-canvas
// phone drawer and is a different, already-correct rule.
function _topLevelRuleBody(selector) {
    const m = CSS.match(new RegExp('^' + selector + '\\s*\\{([^}]*)\\}', 'm'));
    assert.ok(m, `top-level CSS rule not found: ${selector}`);
    return m[1];
}

test('CMX-377 round 2 GUARD: .canvas sits in the SAME grid row as .sidebar, in column 2 — never auto-placed onto row 2', () => {
    const canvas = _topLevelRuleBody('\\.canvas');
    // The actual defect: any rule that puts .canvas on row 2+ (or leaves the
    // row unset while something else claims row 1 alone) re-creates the
    // off-screen-Wall bug, because .app declares only one explicit row.
    assert.doesNotMatch(canvas, /grid-row\s*:\s*[2-9]/,
        '.canvas is placed on grid-row 2 or later — with .app a single-row grid, this shoves it ' +
        'below the sidebar (PR #529 round 2: the Wall rendered off-screen, the whole right side ' +
        'of the viewport was blank)');
    assert.match(canvas, /grid-row\s*:\s*1\b/,
        '.canvas does not explicitly claim grid-row 1 — relying on auto-placement here is exactly ' +
        'what broke: nothing occupies column 2 on its own, so a missing/looser rule can silently ' +
        'auto-place it back onto row 2 the same way the original defect did');
    assert.match(canvas, /grid-column\s*:\s*2\b/,
        '.canvas does not explicitly claim grid-column 2 (the column after .sidebar) — without an ' +
        'explicit column, auto-placement is one accidental removal of a leading rule away from ' +
        'putting it back in column 1, on top of the sidebar');
});

// CMX-377 round 3, defeat_shapes #377b: the CASCADE-RESOLVED companion to the
// source guard above. Mounts the REAL style.css against the real `.app` shell
// shape and reads the RESOLVED grid placement — the same "which declaration
// actually wins" question a browser answers, regardless of which selector
// string carries the winning declaration. This is what catches
// `.app > .canvas { grid-row: 2; }`: a second, more specific rule the
// source-only guard above never anchors against, but which jsdom's CSSOM
// still resolves correctly (confirmed empirically: jsdom implements the
// cascade — specificity then source order — for CSS-standard properties like
// `grid-row`/`grid-column`; it just never computes the resulting on-screen
// box position, which is the one thing this test does NOT need to ask).
test('CMX-377 round 3 GUARD (cascade-resolved): .canvas RESOLVES to grid-row 1 / grid-column 2, however many rules target it', () => {
    // round 6 (defeat_shapes #377e): read the LONGHANDS, under both colour schemes.
    // jsdom never folds `grid-row-start: 2` back into `gridRow`, so round 5's
    // shorthand read stayed "1" under a longhand override every browser applies;
    // cssForViewport expands the shorthands so the cascade compares like with like.
    for (const vp of [DESKTOP, DESKTOP_DARK]) {
        const dom = new JSDOM(
            // round 5 (defeat_shapes #377d): flattened for a 1440px browser — jsdom skips
            // @supports/@media-wrapped rules, so the raw CSS hid `@supports (display: grid)
            // { .app > .canvas { grid-row: 2; } }` from this guard.
            `<!doctype html><html><head><style>${cssForViewport(CSS, vp)}</style></head><body>` +
            '<div class="app"><aside class="sidebar"></aside><main class="canvas" id="canvas"></main></div>' +
            '</body></html>',
            { pretendToBeVisual: true });
        const cs = dom.window.getComputedStyle(dom.window.document.getElementById('canvas'));
        const [rs, re, cs0, ce] = ['grid-row-start', 'grid-row-end', 'grid-column-start', 'grid-column-end'].map(p => cs.getPropertyValue(p));
        const at = vp.colorScheme || 'light';
        assert.equal(rs, '1',
            `[${at}] .canvas resolves grid-row-start "${rs}", not "1" — some rule (however it is spelled, scoped, ` +
            'wrapped or written as a longhand) wins the cascade and places .canvas on row 2+, recreating the ' +
            'off-screen-Wall bug (PR #529 round 2)');
        assert.match(re, /^(auto|2|span 1)$/, `[${at}] .canvas resolves grid-row-end "${re}" — no longer exactly row 1`);
        assert.equal(cs0, '2',
            `[${at}] .canvas resolves grid-column-start "${cs0}", not "2" — .canvas is not actually placed beside the sidebar`);
        assert.match(ce, /^(auto|3|span 1)$/, `[${at}] .canvas resolves grid-column-end "${ce}" — no longer exactly column 2`);
    }
});
