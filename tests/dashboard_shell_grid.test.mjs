// THE SIDEBAR+CANVAS SHELL GRID — CSS-SOURCE GUARD (CMX-377 round 2).
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
// So this is a SOURCE assertion, not a rendered one — the same trade this
// repo already makes for other layout-only facts jsdom cannot see (e.g.
// tests/sidebar.test.mjs's "expanded icon matches the collapsed rail" pair).
// It reads the REAL style.css and asserts the actual requirement: `.canvas`
// sits in the same grid row as `.sidebar` (row 1), in the second column.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

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
