// The markdown renderer (static/js/knowledge.js's knMd/knInline until CMX-228; ported from
// tests/taskmodal_model.test.mjs). Each row is a payload the branch it names provably
// changes (never a fixed point of its transform, DEFEAT_SHAPES #24/#25), so a corruption of
// any one branch goes red. Plus CMX-228's link allowlist: only http(s), mailto and relative
// URLs become links.
import { render } from '@testing-library/react';
import { expect, test } from 'vitest';
import { Markdown, escapeHtml, inlineHtml, isSafeHref, markdownHtml } from '../index.ts';

test('a heading + numbered list + inline code: the exact shape the brief pane depends on', () => {
  const src = '### OBJECTIVE\nBuild `sample()` with these steps:\n1. First step with `code`.\n2. Second step.\n\nSome paragraph.\n';
  expect(markdownHtml(src)).toBe(
    '<h3 class="kn-mh">OBJECTIVE</h3>' +
      '<p>Build <code>sample()</code> with these steps:</p>' +
      '<ol class="kn-ol"><li>First step with <code>code</code>.</li><li>Second step.</li></ol>' +
      '<p>Some paragraph.</p>',
  );
});

test('empty, null and undefined render nothing', () => {
  expect(markdownHtml('')).toBe('');
  expect(markdownHtml(null)).toBe('');
  expect(markdownHtml(undefined)).toBe('');
});

test('a `-` run renders <ul> with a MATCHING closing tag, not <ol>', () => {
  expect(markdownHtml('- one\n- two')).toBe('<ul class="kn-ul"><li>one</li><li>two</li></ul>');
});

test('a heading between a `-` run and a `1.` run splits them into two lists', () => {
  expect(markdownHtml('- a\n### H\n1. b')).toBe('<ul class="kn-ul"><li>a</li></ul><h3 class="kn-mh">H</h3><ol class="kn-ol"><li>b</li></ol>');
});

test('a bold span, an external link, a relative .md link and a #anchor link', () => {
  const src = '- ship **the wall** now\n- see [docs](https://example.com/x) and [notes](foo.md) but not [here](#anchor)';
  expect(markdownHtml(src)).toBe(
    '<ul class="kn-ul">' +
      '<li>ship <strong>the wall</strong> now</li>' +
      '<li>see <a href="https://example.com/x" target="_blank" rel="noopener">docs</a>' +
      ' and <a href="foo.md" target="_blank" rel="noopener">notes</a>' +
      ' but not here</li>' +
      '</ul>',
  );
});

test('a blockquote and a fenced block take their own branches; fenced lines are not inline-rendered', () => {
  expect(markdownHtml('> quoted line\n```\nconst x = 1;\n**not bold**\n```\nafter')).toBe(
    '<blockquote>quoted line</blockquote><pre class="kn-code"><code>const x = 1;\n**not bold**\n</code></pre><p>after</p>',
  );
});

test('HTML-special characters are escaped before any markdown rule runs', () => {
  expect(markdownHtml('<img src=x onerror=alert(1)> & unescaped')).toBe('<p>&lt;img src=x onerror=alert(1)&gt; &amp; unescaped</p>');
  expect(escapeHtml('<a href="x">&</a>')).toBe('&lt;a href=&quot;x&quot;&gt;&amp;&lt;/a&gt;');
});

test('a link href holding a double quote cannot break out of the attribute', () => {
  expect(markdownHtml('[text](foo"bar)')).toBe('<p><a href="foo&quot;bar" target="_blank" rel="noopener">text</a></p>');
});

const BRANCH_TABLE: { branch: string; md: string; html: string }[] = [
  {
    branch: 'fenced code: content is escaped verbatim via escHtml, not knInline\'d '
      + '(payload with `<`/`&` so escHtml is NOT the identity — DEFEAT_SHAPES #24)',
    md: '```\n<script>alert(1)</script> & co\n```',
    html: '<pre class="kn-code"><code>&lt;script&gt;alert(1)&lt;/script&gt; &amp; co\n</code></pre>',
  },
  {
    branch: 'NEGATIVE CONTROL (not named by the round-5 finding): an unterminated fence '
      + '(odd number of ``` lines — e.g. a truncated judge verdict) still closes the '
      + '<pre>/<code> at EOF',
    md: '```\nunterminated',
    html: '<pre class="kn-code"><code>unterminated\n</code></pre>',
  },
  {
    branch: 'a blank line closes an open list',
    md: '- a\n\n- b',
    html: '<ul class="kn-ul"><li>a</li></ul><ul class="kn-ul"><li>b</li></ul>',
  },
  {
    branch: 'heading level 1 (`#`) — the `#{1,4}` capture is not pinned to a constant (DEFEAT_SHAPES #24)',
    md: '# h1',
    html: '<h1 class="kn-mh">h1</h1>',
  },
  { branch: 'heading level 2 (`##`)', md: '## h2', html: '<h2 class="kn-mh">h2</h2>' },
  { branch: 'heading level 3 (`###`)', md: '### h3', html: '<h3 class="kn-mh">h3</h3>' },
  {
    branch: 'heading level 4 (`####`) — the other end of the `#{1,4}` capture (DEFEAT_SHAPES #24)',
    md: '#### h4',
    html: '<h4 class="kn-mh">h4</h4>',
  },
  { branch: 'blockquote', md: '> quoted', html: '<blockquote>quoted</blockquote>' },
  {
    branch: 'ordered list item (`1.`/`2.`)',
    md: '1. a\n2. b',
    html: '<ol class="kn-ol"><li>a</li><li>b</li></ol>',
  },
  { branch: 'unordered `-` bullet', md: '- a', html: '<ul class="kn-ul"><li>a</li></ul>' },
  {
    branch: 'unordered `*` bullet — the second alternative in `/^[-*]\\s+(.*)$/`, never '
      + 'fixtured before this round (DEFEAT_SHAPES #24)',
    md: '* a',
    html: '<ul class="kn-ul"><li>a</li></ul>',
  },
  {
    branch: 'NEGATIVE CONTROL (not named by the round-5 finding): list-kind switch ol -> ul '
      + '(no blank line) closes the ol\'s </ol> before opening the ul',
    md: '1. a\n- b',
    html: '<ol class="kn-ol"><li>a</li></ol><ul class="kn-ul"><li>b</li></ul>',
  },
  {
    branch: 'list-kind switch ul -> ol (no blank line) closes the ul\'s </ul> before opening the ol',
    md: '- a\n1. b',
    html: '<ul class="kn-ul"><li>a</li></ul><ol class="kn-ol"><li>b</li></ol>',
  },
  {
    branch: 'a `-` run immediately followed by a fenced block: closeList() runs BEFORE the '
      + '<pre> opens, so the </ul> is not left dangling inside the last <li>',
    md: '- a\n```\ncode\n```',
    html: '<ul class="kn-ul"><li>a</li></ul><pre class="kn-code"><code>code\n</code></pre>',
  },
  {
    branch: 'a `#anchor` link renders as plain text, never a clickable <a> (knLink)',
    md: '[here](#anchor)',
    html: '<p>here</p>',
  },
  {
    branch: 'an external link renders via knLink/attrEsc',
    md: '[docs](https://example.com/x)',
    html: '<p><a href="https://example.com/x" target="_blank" rel="noopener">docs</a></p>',
  },
  // --- round 6 additions: the round-5 table enumerated knMd's branches BY
  // ENTRY CONDITION (does this branch emit its tag) but not by the transforms
  // each branch applies once inside — DEFEAT_SHAPES #24 recurring one level
  // deeper (see DEFEAT_SHAPES #25). The blockquote and heading rows above
  // (`> quoted`, `# h1`...`#### h4`) are plain alphanumeric text — identity
  // under knInline — so dropping the knInline call from either branch left
  // both rows byte-identical. And of closeList()'s eight call sites the
  // table exercised six; these two close the remaining pair (blockquote and
  // paragraph immediately after an open list, no blank line).
  {
    branch: 'blockquote content is run through knInline, not spliced raw — payload with '
      + 'a bold span AND HTML-special characters so knInline is NOT the identity '
      + '(DEFEAT_SHAPES #25)',
    md: '> quoted **bold** <img src=x onerror=alert(1)>',
    html: '<blockquote>quoted <strong>bold</strong> &lt;img src=x onerror=alert(1)&gt;</blockquote>',
  },
  {
    branch: 'heading content is run through knInline, not spliced raw — payload with a '
      + 'bold span AND HTML-special characters so knInline is NOT the identity '
      + '(DEFEAT_SHAPES #25)',
    md: '### Fix <Wall> & the **grid**',
    html: '<h3 class="kn-mh">Fix &lt;Wall&gt; &amp; the <strong>grid</strong></h3>',
  },
  {
    branch: 'a `-` run immediately followed by a blockquote: closeList() runs BEFORE '
      + 'the <blockquote> opens, so the </ul> is not left dangling inside the last '
      + '<li> (the closeList() call site the round-5 table did not cover)',
    md: '- a\n> quoted',
    html: '<ul class="kn-ul"><li>a</li></ul><blockquote>quoted</blockquote>',
  },
  {
    branch: 'a `-` run immediately followed by a plain continuation line (no blank '
      + 'line, no fence): closeList() runs BEFORE the <p> opens (the eighth and '
      + 'last closeList() call site the round-5 table did not cover)',
    md: '- ship the wall\nthen review it',
    html: '<ul class="kn-ul"><li>ship the wall</li></ul><p>then review it</p>',
  },
  {
    branch: 'a whitespace-only line closes an open list exactly like a truly blank '
      + 'line — the trailing-whitespace strip (`raw.replace(/\\s+$/, \'\')`) runs '
      + 'before the blank-line check',
    md: '- a\n   \n- b',
    html: '<ul class="kn-ul"><li>a</li></ul><ul class="kn-ul"><li>b</li></ul>',
  },
  {
    branch: 'an indented ordered-list item (leading spaces before the digit) still '
      + 'matches the ordered-item branch, not the paragraph fallback',
    md: '  1. a',
    html: '<ol class="kn-ol"><li>a</li></ol>',
  },
];


for (const { branch, md, html } of BRANCH_TABLE) {
  test(`branch table: ${branch}`, () => {
    expect(markdownHtml(md)).toBe(html);
  });
}

// --- CMX-228: the link allowlist. knowledge.js's knLink linked ANY href, so a brief holding
// [x](javascript:...) rendered a live javascript: link in the task modal.

test.each([
  ['https', 'https://example.com/x'],
  ['http', 'http://example.com/x'],
  ['mailto', 'mailto:someone@example.com'],
  ['upper-case scheme', 'HTTPS://example.com'],
  ['relative path', 'docs/FRONTEND.md'],
  ['root-relative path', '/api/x'],
  ['a colon after a slash is a path, not a scheme', 'a/b:c'],
])('allowed: %s', (_why, href) => {
  expect(isSafeHref(href)).toBe(true);
  expect(inlineHtml(`[x](${href})`)).toBe(`<a href="${href}" target="_blank" rel="noopener">x</a>`);
});

test.each([
  ['javascript:', 'javascript:alert.call'],
  ['mixed-case javascript:', 'JaVaScRiPt:alert.call'],
  ['javascript: behind a tab (a browser strips it)', 'java\tscript:alert.call'],
  ['javascript: behind leading spaces', '  javascript:alert.call'],
  ['javascript: behind a control character', '\u0001javascript:alert.call'],
  ['data:', 'data:text/html;base64,PHNjcmlwdD4='],
  ['vbscript:', 'vbscript:msgbox'],
  ['file:', 'file:///etc/passwd'],
  ['an unknown scheme', 'chela-run:CMX-1'],
])('rejected: %s renders as its text, never a link', (_why, href) => {
  expect(isSafeHref(href)).toBe(false);
  const html = inlineHtml(`[click me](${href})`);
  expect(html).toBe('click me');
  expect(html).not.toContain('<a');
});

test('<Markdown> renders block markdown in a div, inline markdown in a span', () => {
  const { container } = render(
    <>
      <Markdown className="md" source={'## Title\n[x](javascript:alert.call)'} />
      <Markdown inline source="ship **it**" />
    </>,
  );
  const [block, inline] = [...container.children];
  expect(block?.tagName).toBe('DIV');
  expect(block?.className).toBe('md');
  expect(block?.innerHTML).toBe('<h2 class="kn-mh">Title</h2><p>x</p>');
  expect(block?.querySelector('a')).toBeNull();
  expect(inline?.tagName).toBe('SPAN');
  expect(inline?.innerHTML).toBe('ship <strong>it</strong>');
});
