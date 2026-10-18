// The no-emoji guard's scanner (CMX-199). It finds emoji and pictographic symbols in
// what the dashboard RENDERS: string and template literals (and JSX text) in its JS,
// and the text of its HTML templates. Comments and regex literals are not rendered,
// so they are skipped: a comment may name the glyph a conversion replaced.
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import ts from 'typescript';

// Extended_Pictographic is Unicode's emoji-capable set (⚖ ⚠ 👁 ⌨ ↗ …). It leaves out
// the text-style dingbats the legacy UI also used as icons (✓ ✕ ✗ ★ ☰), so the whole
// Miscellaneous Symbols + Dingbats blocks (U+2600-U+27BF) are in too, plus ⎇ (U+2387).
// Plain typography (→ · … ≥) and the geometric status dots (○ ● ◆) are not emoji.
const EMOJI = /[\p{Extended_Pictographic}\u2600-\u27BF\u2387]/gu;

const RENDERED = new Set([
    ts.SyntaxKind.StringLiteral,
    ts.SyntaxKind.NoSubstitutionTemplateLiteral,
    ts.SyntaxKind.TemplateHead,
    ts.SyntaxKind.TemplateMiddle,
    ts.SyntaxKind.TemplateTail,
    ts.SyntaxKind.JsxText,
]);

function _hits(text, offset, lineOf) {
    return [...text.matchAll(EMOJI)].map(m => ({ glyph: m[0], line: lineOf(offset + m.index) }));
}

function _lineOfIn(src) {
    return pos => src.slice(0, pos).split('\n').length;
}

// -> [{glyph, line}] for every emoji inside a rendered literal of `src`.
function scanJs(src, fileName = 'x.js') {
    const kind = /\.tsx$/.test(fileName) ? ts.ScriptKind.TSX
        : /\.ts$/.test(fileName) ? ts.ScriptKind.TS
        : /\.jsx$/.test(fileName) ? ts.ScriptKind.JSX : ts.ScriptKind.JS;
    const sf = ts.createSourceFile(fileName, src, ts.ScriptTarget.Latest, true, kind);
    const out = [];
    const lineOf = _lineOfIn(src);
    const walk = node => {
        if (RENDERED.has(node.kind)) out.push(..._hits(node.getText(sf), node.getStart(sf), lineOf));
        ts.forEachChild(node, walk);
    };
    walk(sf);
    return out;
}

// Blank a span but keep its newlines, so later line numbers stay true.
const _blank = s => s.replace(/[^\n]/g, ' ');

// -> [{glyph, line}] for every emoji in the template's text and attributes. HTML and
// Jinja comments are skipped; an inline <script> is scanned as JS (its comments skipped).
function scanHtml(src) {
    const out = [];
    const lineOf = _lineOfIn(src);
    let text = src.replace(/<!--[\s\S]*?-->|\{#[\s\S]*?#\}/g, _blank);
    text = text.replace(/(<script\b[^>]*>)([\s\S]*?)(<\/script>)/gi, (_whole, open, body, close, at) => {
        const base = lineOf(at + open.length) - 1;
        for (const h of scanJs(body)) out.push({ glyph: h.glyph, line: base + h.line });
        return open + _blank(body) + close;
    });
    out.push(..._hits(text, 0, lineOf));
    return out.sort((a, b) => a.line - b.line);
}

function _walk(dir, keep) {
    if (!existsSync(dir)) return [];
    return readdirSync(dir).flatMap(name => {
        const p = join(dir, name);
        if (statSync(p).isDirectory()) return name === 'node_modules' ? [] : _walk(p, keep);
        return keep(name) ? [p] : [];
    });
}

// The files the guard covers, repo-relative. frontend/src is the React port's tree
// (CMX-171): covered as soon as it exists, its own tests excepted (they may hold
// emoji as fixtures).
function guardedFiles(root) {
    const top = (sub, re) => {
        const dir = join(root, sub);
        return existsSync(dir) ? readdirSync(dir).filter(n => re.test(n)).map(n => join(dir, n)) : [];
    };
    return [
        ...top('chela/dashboard/static/js', /\.js$/),
        ...top('chela/dashboard/templates', /\.html$/),
        ..._walk(join(root, 'frontend/src'), n => /\.[jt]sx?$/.test(n) && !/\.test\./.test(n)),
    ].map(p => relative(root, p)).sort();
}

function scanFile(root, rel) {
    const src = readFileSync(join(root, rel), 'utf8');
    return rel.endsWith('.html') ? scanHtml(src) : scanJs(src, rel);
}

// The ratchet. `allowlist` maps a repo-relative file to the EXACT number of emoji it
// still carries. Returns one message per problem; [] means the guard is green.
function checkTree(root, allowlist) {
    const problems = [];
    const files = guardedFiles(root);
    for (const rel of files) {
        const hits = scanFile(root, rel);
        const allowed = Object.hasOwn(allowlist, rel) ? allowlist[rel].count : 0;
        const where = hits.map(h => `${h.glyph} at line ${h.line}`).join(', ');
        if (hits.length > allowed) {
            problems.push(`${rel}: ${hits.length} emoji, allowlist allows ${allowed} (${where}). `
                + 'Draw an icon with icon() from util.js / icons.js instead.');
        } else if (hits.length < allowed) {
            problems.push(`${rel}: ${hits.length} emoji, allowlist still says ${allowed}. `
                + (hits.length ? 'Lower its count' : 'Delete its entry') + ' in tests/no_emoji_allowlist.json.');
        }
    }
    for (const rel of Object.keys(allowlist)) {
        if (!files.includes(rel)) problems.push(`${rel}: allowlisted but not a guarded file. Delete its entry.`);
    }
    return problems;
}

export { EMOJI, checkTree, guardedFiles, scanHtml, scanJs };
