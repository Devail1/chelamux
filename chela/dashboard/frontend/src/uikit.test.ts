// CMX-249 (FRONTEND_PORT_PLAN.md §6.8, Liav 10-19): ONE UI kit. No component outside
// src/shared/ui renders a raw <input>, <select>, <textarea>, <button> or <dialog>, or imports
// @radix-ui/* itself: it uses the kit (FieldRow, TextInput, Select, Toggle, Button, Dialog...),
// which wraps Radix for behaviour and style.css for the look. A JSX scan over the TypeScript
// AST, so a comment or a string that mentions a tag is not a hit; createElement of one of
// these tags by its literal name is a hit too. A tag held in a variable is not seen.
import ts from 'typescript';
import { expect, test } from 'vitest';
import pkg from '../package.json';

const RAW = new Set(['input', 'select', 'textarea', 'button', 'dialog']);

/**
 * The exemptions: `file` (relative to src/) and the raw `tag` it may render, each with its
 * reason. A stale entry (the file no longer renders it) fails too, so the list only shrinks.
 */
const ALLOW: { file: string; tag: string; reason: string }[] = [
  {
    file: 'features/shell/lib/App.tsx',
    tag: 'input',
    reason: 'the sidebar jump box is uncontrolled: the sidebar controller reads and filters on its DOM value per keystroke; TextInput is controlled',
  },
  {
    file: 'features/tilefoot/lib/TileFoot.tsx',
    tag: 'textarea',
    reason: 'an off-screen clipboard fallback for a plain-http dashboard (CMX-417), created and removed in one call, never rendered UI',
  },
  {
    file: 'features/paneframe/lib/paste.ts',
    tag: 'textarea',
    reason: 'the same off-screen clipboard fallback, for the keybar Paste',
  },
];

export type Hit = { file: string; tag: string; line: number };

/** Every raw kit element and direct @radix-ui import in one source file. */
export function scan(source: string, file: string): Hit[] {
  const sf = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, file.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const hits: Hit[] = [];
  const hit = (tag: string, node: ts.Node) => hits.push({ file, tag, line: sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1 });
  const radix = (spec: ts.Expression | undefined, node: ts.Node) => {
    if (spec && ts.isStringLiteralLike(spec) && spec.text.startsWith('@radix-ui/')) hit(spec.text, node);
  };
  const visit = (node: ts.Node) => {
    if ((ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) && ts.isIdentifier(node.tagName) && RAW.has(node.tagName.text)) hit(node.tagName.text, node);
    if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) radix(node.moduleSpecifier, node);
    if (ts.isCallExpression(node)) {
      const [arg] = node.arguments;
      if (node.expression.kind === ts.SyntaxKind.ImportKeyword) radix(arg, node);
      const callee = node.expression;
      const name = ts.isPropertyAccessExpression(callee) ? callee.name.text : ts.isIdentifier(callee) ? callee.text : '';
      if (name === 'createElement' && arg && ts.isStringLiteralLike(arg) && RAW.has(arg.text.toLowerCase())) hit(arg.text.toLowerCase(), node);
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return hits;
}

/** Source files, keyed by their path relative to src/. */
type Tree = Record<string, string>;

/** The files the rule covers: .ts/.tsx under src/, minus shared/ui (the kit) and tests. */
export function guarded(tree: Tree): string[] {
  return Object.keys(tree)
    .filter((f) => /\.tsx?$/.test(f) && !f.endsWith('.d.ts') && !/\.test\.tsx?$/.test(f))
    .filter((f) => !f.startsWith('shared/ui/') && !f.split('/').includes('tests'))
    .sort();
}

/** What breaks the rule in `tree`, given the exemptions: an unlisted hit or a stale entry. */
export function problems(tree: Tree, allow: typeof ALLOW): string[] {
  const hits = guarded(tree).flatMap((f) => scan(tree[f] ?? '', f));
  const listed = (h: Hit) => allow.some((a) => a.file === h.file && a.tag === h.tag);
  const out = hits.filter((h) => !listed(h)).map((h) => `${h.file}:${h.line}: raw ${h.tag.startsWith('@') ? `import of ${h.tag}` : `<${h.tag}>`} outside shared/ui; use the kit`);
  for (const a of allow) {
    if (!hits.some((h) => h.file === a.file && h.tag === a.tag)) out.push(`${a.file}: allowlisted <${a.tag}> is not rendered any more; delete its entry`);
  }
  return out;
}

// The real tree: every .ts/.tsx under src/, as text.
const SRC: Tree = Object.fromEntries(
  Object.entries(import.meta.glob<string>('./**/*.{ts,tsx}', { query: '?raw', import: 'default', eager: true })).map(([p, body]) => [p.slice(2), body]),
);

test('the real tree: nothing outside shared/ui renders a raw control or imports Radix, but the listed exemptions', () => {
  expect(problems(SRC, ALLOW)).toEqual([]);
});

test('every exemption gives a reason', () => {
  for (const a of ALLOW) expect(a.reason.length, `${a.file} <${a.tag}>`).toBeGreaterThan(20);
});

test('the rule covers the features and shared code, and leaves the kit and tests alone', () => {
  const files = guarded(SRC);
  for (const f of ['features/shell/lib/App.tsx', 'features/sidebar/lib/Recent.tsx', 'boot.ts', 'shared/icons/index.ts']) expect(files).toContain(f);
  expect(files.some((f) => f.startsWith('shared/ui/'))).toBe(false);
  expect(files.some((f) => f.includes('/tests/') || f.endsWith('.test.ts') || f.endsWith('.test.tsx'))).toBe(false);
});

test('scan: each raw tag in JSX, self-closing or not, with its line', () => {
  const src = 'const A = () => (\n  <div>\n    <button onClick={f}>x</button>\n    <input />\n    <select></select>\n    <textarea />\n    <dialog open />\n  </div>\n);\n';
  expect(scan(src, 'a.tsx')).toEqual([
    { file: 'a.tsx', tag: 'button', line: 3 },
    { file: 'a.tsx', tag: 'input', line: 4 },
    { file: 'a.tsx', tag: 'select', line: 5 },
    { file: 'a.tsx', tag: 'textarea', line: 6 },
    { file: 'a.tsx', tag: 'dialog', line: 7 },
  ]);
});

test('scan: the kit components, a member tag, comments and strings are not hits', () => {
  const src = "// <button> in a comment\nconst s = '<input>';\nconst A = () => <><Button /><TextInput value='' onChange={f} /><ui.button /><Buttonish /></>;\n";
  expect(scan(src, 'a.tsx')).toEqual([]);
});

test('scan: direct @radix-ui imports (static, re-export, dynamic) and createElement of a raw tag are hits', () => {
  const src = "import * as RD from '@radix-ui/react-dialog';\nexport { Root } from '@radix-ui/react-popover';\nvoid import('@radix-ui/react-select');\nimport { x } from 'radix-ish';\nconst t = document.createElement('TEXTAREA');\nconst d = document.createElement('div');\n";
  expect(scan(src, 'a.ts').map((h) => `${h.line}:${h.tag}`)).toEqual(['1:@radix-ui/react-dialog', '2:@radix-ui/react-popover', '3:@radix-ui/react-select', '5:textarea']);
});

test('negative control: a raw <button> added to a fixture component goes RED; the kit and its tests may', () => {
  const clean = { 'features/x/lib/X.tsx': 'export const X = () => <Button>ok</Button>;\n', 'shared/ui/lib/basics.tsx': 'export const B = () => <button />;\n', 'features/x/tests/X.test.tsx': 'render(<button />);\n' };
  expect(problems(clean, [])).toEqual([]);
  const red = problems({ ...clean, 'features/x/lib/X.tsx': 'export const X = () => <button>ok</button>;\n' }, []);
  expect(red).toEqual(['features/x/lib/X.tsx:1: raw <button> outside shared/ui; use the kit']);
});

test('negative control: a raw <dialog> or a direct Radix import outside the kit goes RED', () => {
  expect(problems({ 'features/x/lib/X.tsx': 'export const X = () => <dialog open />;\n' }, [])).toEqual(['features/x/lib/X.tsx:1: raw <dialog> outside shared/ui; use the kit']);
  expect(problems({ 'features/x/lib/X.tsx': "import * as RT from '@radix-ui/react-tooltip';\n" }, [])).toEqual(['features/x/lib/X.tsx:1: raw import of @radix-ui/react-tooltip outside shared/ui; use the kit']);
});

test('an exemption covers only its own file and tag, and a stale one fails', () => {
  const root = { 'features/x/lib/X.tsx': 'export const X = () => <input />;\n' };
  expect(problems(root, [{ file: 'features/x/lib/X.tsx', tag: 'input', reason: 'r' }])).toEqual([]);
  expect(problems(root, [{ file: 'features/x/lib/X.tsx', tag: 'button', reason: 'r' }])).toHaveLength(2);
  expect(problems(root, [{ file: 'features/y/lib/Y.tsx', tag: 'input', reason: 'r' }])).toHaveLength(2);
});

// §7 row 1 (10-19): behaviour from Radix Primitives, the look from style.css. No styled system.
test('no shadcn/ui, Radix Themes or Tailwind among the dependencies; Radix only as primitives', () => {
  const deps = Object.keys({ ...pkg.dependencies, ...pkg.devDependencies });
  expect(deps.filter((d) => /tailwind|shadcn|^@radix-ui\/themes$|^class-variance-authority$/.test(d))).toEqual([]);
  expect(deps.filter((d) => d.startsWith('@radix-ui/')).every((d) => d.startsWith('@radix-ui/react-'))).toBe(true);
});
