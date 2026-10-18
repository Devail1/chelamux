// CMX-199: the dashboard draws Lucide icons, not emoji. The rule lives here, not in
// prose: every rendered literal in static/js/*.js and every template's text is scanned,
// and an emoji outside tests/no_emoji_allowlist.json fails the build. The allowlist is
// a per-file ratchet the CMX-212 port slices empty. Run: pnpm exec vitest run <this file>.
import { test } from 'vitest';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { JSDOM } from 'jsdom';
import { checkTree, guardedFiles, scanHtml, scanJs } from './js_helpers/emoji_scan.mjs';

const ROOT = new URL('..', import.meta.url).pathname;
const ALLOW = JSON.parse(readFileSync(join(ROOT, 'tests/no_emoji_allowlist.json'), 'utf8')).files;
const glyphs = hits => hits.map(h => h.glyph).join('');

test('the real tree is green against the allowlist', () => {
    assert.deepEqual(checkTree(ROOT, ALLOW), []);
});

test('the guard covers the dashboard JS and templates', () => {
    const files = guardedFiles(ROOT);
    for (const f of ['static/js/util.js', 'static/js/icons.js', 'templates/index.html']) {
        assert.ok(files.includes(`chela/dashboard/${f}`), f);
    }
    assert.ok(!files.some(f => f.includes('/vendor/') || f.includes('/collab/')));
});

test('JS: emoji in every kind of rendered literal is found, with its line', () => {
    const src = "const a = '⚖ judging';\nconst b = \"ok ✓\";\nconst c = `x ✕`;\n"
        + 'const d = `${a} 👁 ${b} ⌨ ${c} ↗`;\n';
    assert.deepEqual(scanJs(src), [
        { glyph: '⚖', line: 1 }, { glyph: '✓', line: 2 }, { glyph: '✕', line: 3 },
        { glyph: '👁', line: 4 }, { glyph: '⌨', line: 4 }, { glyph: '↗', line: 4 },
    ]);
});

test('JS: comments and regex literals are not rendered, so not flagged', () => {
    const src = "// ⚖ was the old glyph\n/* ⚠ block */\nconst r = /^### 🙈 (\\d+)/m;\nconst s = 'plain';\n";
    assert.deepEqual(scanJs(src), []);
});

test('TSX: JSX text and attribute strings are scanned', () => {
    assert.equal(glyphs(scanJs('const x = <b title="⚠">done ✅</b>;\n', 'a.tsx')), '⚠✅');
});

test('the glyph class: emoji and dingbats in, plain typography and status dots out', () => {
    assert.equal(glyphs(scanJs("'⚖⚠✓✕✗⌨👁🙈⛔🛑★☰⎇⚑🧊↗▶'")), '⚖⚠✓✕✗⌨👁🙈⛔🛑★☰⎇⚑🧊↗▶');
    assert.deepEqual(scanJs("'→ ← · … – ≥ − ○ ● ◆ ▾ ◐ ⊘ ↻ $ C'"), []);
});

test('HTML: text and attributes are scanned; HTML and Jinja comments are not', () => {
    const src = '<!-- ⚖ old -->\n{# ⚠ note #}\n<span>⚖ 0 pending</span>\n<b title="✕">x</b>\n';
    assert.deepEqual(scanHtml(src), [{ glyph: '⚖', line: 3 }, { glyph: '✕', line: 4 }]);
});

test('HTML: an inline script is scanned as JS, keeping true line numbers', () => {
    const src = '<p>a</p>\n<script>\n// 🙋 comment\nx = "🔌";\n</script>\n<p>🔒</p>\n';
    assert.deepEqual(scanHtml(src), [{ glyph: '🔌', line: 4 }, { glyph: '🔒', line: 6 }]);
});

function tree(files) {
    const root = mkdtempSync(join(tmpdir(), 'noemoji-'));
    for (const [rel, body] of Object.entries(files)) {
        mkdirSync(dirname(join(root, rel)), { recursive: true });
        writeFileSync(join(root, rel), body);
    }
    return root;
}
const JS = 'chela/dashboard/static/js/a.js';
const HTML = 'chela/dashboard/templates/index.html';
const TSX = 'frontend/src/App.tsx';

test('negative control: an emoji added to a rendered string or a template goes RED', () => {
    assert.deepEqual(checkTree(tree({ [JS]: "x = 'ok';\n" }), {}), []);
    const [p] = checkTree(tree({ [JS]: "x = 'ok ⚖';\n" }), {});
    assert.match(p, /^chela\/dashboard\/static\/js\/a\.js: 1 emoji, allowlist allows 0 \(⚖ at line 1\)/);
    assert.equal(checkTree(tree({ [HTML]: '<span>⚠</span>' }), {}).length, 1);
});

test('frontend/src is guarded once it exists; its own tests are not', () => {
    const root = tree({ [TSX]: 'export const A = () => <i>🛑</i>;\n', 'frontend/src/A.test.tsx': "t('⚖');\n" });
    assert.deepEqual(guardedFiles(root), [TSX]);
    assert.equal(checkTree(root, {}).length, 1);
});

test('the ratchet: a count above, below or for a vanished file each fail', () => {
    const root = tree({ [JS]: "x = '⚖ ✓';\n" });
    assert.deepEqual(checkTree(root, { [JS]: { count: 2 } }), []);
    assert.match(checkTree(root, { [JS]: { count: 1 } })[0], /2 emoji, allowlist allows 1/);
    assert.match(checkTree(root, { [JS]: { count: 3 } })[0], /2 emoji, allowlist still says 3\. Lower its count/);
    const clean = tree({ [JS]: "x = 'ok';\n" });
    assert.match(checkTree(clean, { [JS]: { count: 1 } })[0], /0 emoji, allowlist still says 1\. Delete its entry/);
    assert.deepEqual(checkTree(clean, { 'chela/dashboard/static/js/gone.js': { count: 1 } }),
        ['chela/dashboard/static/js/gone.js: allowlisted but not a guarded file. Delete its entry.']);
});

test('every allowlist entry names the port slice that removes it', () => {
    for (const [f, e] of Object.entries(ALLOW)) assert.match(e.removed_by, /P\d+/, f);
});

test('icons.js: every meaning draws a vendored Lucide <svg>, and no glyph', async () => {
    const dom = new JSDOM('<!doctype html><title>t</title>');
    globalThis.window = dom.window;
    globalThis.document = dom.window.document;
    const { icon } = await import('../chela/dashboard/static/js/util.js');
    const { ICONS } = await import('../chela/dashboard/static/js/icons.js');
    assert.ok(Object.isFrozen(ICONS));
    assert.deepEqual([ICONS.judge, ICONS.warning, ICONS.blocked], ['scale', 'triangle-alert', 'ban']);
    for (const meaning of Object.keys(ICONS)) {
        const svg = icon(meaning, 14);
        assert.match(svg, /^<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" /, meaning);
        assert.match(svg, /<(path|circle|rect|line|polyline|polygon) /, meaning);
        assert.deepEqual(scanJs(JSON.stringify(svg)), [], meaning);
    }
    // the scale's beam, from lucide's scale.svg
    assert.ok(icon('judge').includes('<path d="M3 7h1a17 17 0 0 0 8-2 17 17 0 0 0 8 2h1"/>'));
    assert.throws(() => icon('scale'), /icon: unknown meaning "scale"/);
    assert.throws(() => icon('toString'), /unknown meaning/);
});
