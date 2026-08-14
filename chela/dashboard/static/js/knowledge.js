// --- Stage 0: ES-module imports ---
import { attrEsc, escHtml } from './util.js';

// ---------------------------------------------------------------------------
// Markdown rendering — the OKF viewer's Knowledge tab (browse/search/graph over
// /api/knowledge/*) was one of the CMX-279 strip's five deleted views (Liav
// named only Wall and Work as views he actually opens). What survives here is
// the dependency-free markdown->HTML renderer (knMd/knInline, plus their
// knLink helper) — it is reused verbatim by the Work view's task-detail
// modal (taskmodal.js/taskmodalmodel.js) and by kanban.js for inline card
// text, so it is not exclusive to the deleted view and stays.
// ---------------------------------------------------------------------------

// A markdown link's target. The Knowledge concept browser this used to route
// `.md` links to (via chela.knOpen) was deleted with the rest of that view
// (CMX-279) — an in-bundle `.md` link now has nowhere left to open inside the
// app, so it renders as a plain external-style link like everything else
// here, rather than wiring an onclick to a function that no longer exists.
function knLink(text, href) {
    if (href.startsWith('#')) return text;
    return `<a href="${attrEsc(href)}" target="_blank" rel="noopener">${text}</a>`;
}

// Inline markdown on an already-HTML-escaped string: code, links, bold.
function knInline(s) {
    s = escHtml(s);
    s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
    s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (m, t, href) => knLink(t, href));
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    return s;
}

// Minimal block-level markdown → HTML for OKF bodies (headings, lists,
// blockquotes, fenced code, paragraphs). Intentionally tiny and dependency-free.
//
// `listType` tracks which of the two list kinds (if any) is currently open —
// 'ul' for a `-`/`*` run, 'ol' for a `1.`/`2.` run — instead of a single
// boolean, so a `-` run and a `1.` run are never merged into one list even if
// they're adjacent (switching kinds closes the old list and opens the new
// one, same as a blank line or heading would).
function knMd(src) {
    const lines = (src || '').split('\n');
    let html = '', listType = null, inCode = false;
    const closeList = () => {
        if (listType === 'ul') html += '</ul>';
        else if (listType === 'ol') html += '</ol>';
        listType = null;
    };
    for (const raw of lines) {
        if (/^```/.test(raw)) {
            closeList();
            if (inCode) { html += '</code></pre>'; inCode = false; }
            else { html += '<pre class="kn-code"><code>'; inCode = true; }
            continue;
        }
        if (inCode) { html += escHtml(raw) + '\n'; continue; }
        const line = raw.replace(/\s+$/, '');
        if (line === '') { closeList(); continue; }
        const h = line.match(/^(#{1,4})\s+(.*)$/);
        if (h) { closeList(); const lv = h[1].length; html += `<h${lv} class="kn-mh">${knInline(h[2])}</h${lv}>`; continue; }
        if (/^>\s?/.test(line)) { closeList(); html += `<blockquote>${knInline(line.replace(/^>\s?/, ''))}</blockquote>`; continue; }
        const ol = line.match(/^\s*\d+\.\s+(.+)$/);
        if (ol) {
            if (listType !== 'ol') { closeList(); html += '<ol class="kn-ol">'; listType = 'ol'; }
            html += `<li>${knInline(ol[1])}</li>`;
            continue;
        }
        const li = line.match(/^[-*]\s+(.*)$/);
        if (li) {
            if (listType !== 'ul') { closeList(); html += '<ul class="kn-ul">'; listType = 'ul'; }
            html += `<li>${knInline(li[1])}</li>`;
            continue;
        }
        closeList();
        html += `<p>${knInline(line)}</p>`;
    }
    closeList();
    if (inCode) html += '</code></pre>';
    return html;
}

// --- Stage 0: ES-module exports ---
export { knInline, knMd };
