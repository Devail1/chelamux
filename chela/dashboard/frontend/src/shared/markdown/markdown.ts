// ---------------------------------------------------------------------------
// Markdown rendering: the dependency-free markdown-to-HTML renderer the task-detail modal
// (features/taskmodal) and kanban.js's inline card text use. It was static/js/knowledge.js,
// the survivor of the OKF viewer's Knowledge tab (CMX-279 deleted the view); CMX-228
// (CMX-212 P09) moved it here. React renders its output only through <Markdown>.
// ---------------------------------------------------------------------------

/** Escape text for HTML, between tags AND inside a double-quoted attribute. */
export function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// A browser strips ASCII whitespace and C0 controls from a URL before it reads the scheme
// (`java\tscript:` IS javascript:), so the scheme is read the same way.
const isUrlJunk = (c: string) => c.charCodeAt(0) <= 0x20 || c.charCodeAt(0) === 0x7f;
const SCHEME = /^([a-z][a-z0-9+.-]*):/i;
const ALLOWED_SCHEMES = new Set(['http', 'https', 'mailto']);

/**
 * Whether `href` may become a link: http(s), mailto, or a relative URL (no scheme). Anything
 * else (javascript:, data:, vbscript:, file:, ...) renders as plain text. CMX-228: a brief
 * containing `[x](javascript:...)` used to render a live javascript: link (knowledge.js knLink).
 */
export function isSafeHref(href: string): boolean {
  const m = [...href].filter((c) => !isUrlJunk(c)).join('').match(SCHEME);
  return !m || ALLOWED_SCHEMES.has((m[1] ?? '').toLowerCase());
}

// A markdown link's target. `href` comes from already-escaped text, so it is attribute-safe as
// is. An in-page `#` anchor has nowhere to go in the app, so it renders as its text.
function link(text: string, href: string): string {
  if (href.startsWith('#') || !isSafeHref(href)) return text;
  return `<a href="${href}" target="_blank" rel="noopener">${text}</a>`;
}

/** Inline markdown (code, links, bold) on raw text; the result is HTML. */
export function inlineHtml(src: string): string {
  let s = escapeHtml(src);
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_m, t: string, href: string) => link(t, href));
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  return s;
}

/**
 * Minimal block-level markdown to HTML (headings, lists, blockquotes, fenced code, paragraphs).
 * Intentionally tiny and dependency-free.
 *
 * `listType` tracks which of the two list kinds (if any) is open, 'ul' for a `-`/`*` run, 'ol'
 * for a `1.`/`2.` run, so adjacent runs of the two kinds are never merged into one list.
 */
export function markdownHtml(src: string | null | undefined): string {
  const lines = (src || '').split('\n');
  let html = '';
  let listType: 'ul' | 'ol' | null = null;
  let inCode = false;
  const closeList = () => {
    if (listType) html += `</${listType}>`;
    listType = null;
  };
  for (const raw of lines) {
    if (/^```/.test(raw)) {
      closeList();
      html += inCode ? '</code></pre>' : '<pre class="kn-code"><code>';
      inCode = !inCode;
      continue;
    }
    if (inCode) {
      html += escapeHtml(raw) + '\n';
      continue;
    }
    const line = raw.replace(/\s+$/, '');
    if (line === '') {
      closeList();
      continue;
    }
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) {
      closeList();
      const lv = (h[1] ?? '#').length;
      html += `<h${lv} class="kn-mh">${inlineHtml(h[2] ?? '')}</h${lv}>`;
      continue;
    }
    if (/^>\s?/.test(line)) {
      closeList();
      html += `<blockquote>${inlineHtml(line.replace(/^>\s?/, ''))}</blockquote>`;
      continue;
    }
    const ol = line.match(/^\s*\d+\.\s+(.+)$/);
    const item = ol ?? line.match(/^[-*]\s+(.*)$/);
    if (item) {
      const kind = ol ? 'ol' : 'ul';
      if (listType !== kind) {
        closeList();
        html += `<${kind} class="kn-${kind}">`;
        listType = kind;
      }
      html += `<li>${inlineHtml(item[1] ?? '')}</li>`;
      continue;
    }
    closeList();
    html += `<p>${inlineHtml(line)}</p>`;
  }
  closeList();
  if (inCode) html += '</code></pre>';
  return html;
}
