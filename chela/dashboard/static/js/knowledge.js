// ---------------------------------------------------------------------------
// Render: Knowledge (OKF viewer)
//
// Read-only browser over the exported OKF bundle (chela's fleet knowledge as
// typed markdown + frontmatter). Four surfaces per docs/OKF.md: glance (counts /
// freshest / activity log), browse (concepts by directory → a concept with its
// frontmatter header card + computed BACKLINKS), search, and graph.
//
// The bundle is PRIVATE local data; the routes it reads (/api/knowledge/*) are
// loopback-only. This module is vanilla JS like the rest of the dashboard. The
// markdown render + path resolve helpers (knMd/knResolve) are deliberately
// self-contained so the portable viewer.html (Phase 4) can reuse them verbatim.
// ---------------------------------------------------------------------------

const _kn = { tree: null, view: 'glance', path: null, q: '' };

// Entry point — called by the global refresh loop while the Knowledge tab is
// active. Loads the tree once; never clobbers a concept the user is reading.
async function refreshKnowledge() {
    if (!_kn.tree) {
        await knLoadTree();
        return;
    }
    if (_kn.view === 'glance') knRenderGlance();
}

async function knLoadTree() {
    const el = $('#kn-content');
    if (el && !_kn.tree) el.innerHTML = '<div class="kn-loading">Loading knowledge…</div>';
    _kn.tree = await api('/api/knowledge/tree');
    if (_kn.view === 'glance') knRenderGlance();
}

async function knRefresh(btn) {
    if (btn) { btn.disabled = true; btn.textContent = 'Exporting…'; }
    _kn.tree = await api('/api/knowledge/export', { method: 'POST' });
    if (btn) { btn.disabled = false; btn.textContent = 'Refresh'; }
    knBackToGlance();
}

function knBackToGlance() {
    _kn.view = 'glance';
    _kn.path = null;
    const s = $('#kn-search'); if (s) s.value = '';
    knRenderGlance();
}

// --- Glance + Browse -------------------------------------------------------

function knRenderGlance() {
    const t = _kn.tree;
    const el = $('#kn-content');
    if (!el) return;
    if (!t || t.exported === false) {
        el.innerHTML = `
          <div class="work-empty">
            <div class="work-empty-icon">◆</div>
            <div class="work-empty-title">No knowledge bundle yet</div>
            <div class="work-empty-body">
              chela exports its fleet's working knowledge — agents, runs, schedules and the
              projects they touch — as an <a href="https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf"
              target="_blank" rel="noopener">Open Knowledge Format</a> bundle: typed markdown you can
              glance at, browse, and follow by backlink.
            </div>
            <span class="work-empty-cta" onclick="knRefresh()">Export the bundle →</span>
          </div>`;
        return;
    }

    // Split concepts by role so the glance can lead with the live entities
    // (agents → what they're doing) instead of repeating one flat list twice.
    const all = [].concat(...Object.values(t.dirs || {}));
    const agents = all.filter(c => knTypeClass(c.type) === 'agent')
        .sort((a, b) => a.title.localeCompare(b.title));
    const projects = all.filter(c => knTypeClass(c.type) === 'project')
        .sort((a, b) => a.title.localeCompare(b.title));
    const others = all.filter(c => !['agent', 'project'].includes(knTypeClass(c.type)));
    const projByTitle = {};
    projects.forEach(p => { projByTitle[p.title] = p.path; });

    // Synthesized one-line digest — the "what's in here right now" answer.
    const prCount = all.filter(c => c.pr_url).length;
    const freshTs = all.map(c => c.timestamp).filter(Boolean).sort().slice(-1)[0];
    const bits = [];
    if (agents.length) bits.push(`${agents.length} agent${agents.length === 1 ? '' : 's'}`);
    if (projects.length) bits.push(`${projects.length} project${projects.length === 1 ? '' : 's'}`);
    if (prCount) bits.push(`${prCount} PR${prCount === 1 ? '' : 's'}`);
    const otherCounts = {};
    others.forEach(c => { otherCounts[c.type] = (otherCounts[c.type] || 0) + 1; });
    Object.keys(otherCounts).sort().forEach(ty => bits.push(`${otherCounts[ty]} ${escHtml(ty.toLowerCase())}${otherCounts[ty] === 1 ? '' : 's'}`));
    const digest = bits.join(' · ') + (freshTs ? ` · updated ${relativeTime(freshTs)}` : '');

    // Sections that only render when they have content (graceful when sparse).
    const sessions = agents.length ? `
        <div class="kn-sub">Sessions <span class="kn-dim">· what the fleet is working on</span></div>
        ${agents.map(a => knAgentRow(a, projByTitle)).join('')}` : '';

    const projChips = projects.length ? `
        <div class="kn-sub">Projects</div>
        <div class="kn-pchips">${projects.map(knProjectChip).join('')}</div>` : '';

    const otherSections = Object.keys(otherCounts).sort().map(ty => {
        const items = others.filter(c => c.type === ty)
            .sort((a, b) => (b.timestamp || '').localeCompare(a.timestamp || ''));
        return `<div class="kn-sub">${escHtml(ty)}s</div>${items.map(knCardRow).join('')}`;
    }).join('');

    const activity = (t.log && /\n-\s/.test(t.log)) ? `
        <div class="kn-sub">Activity</div>
        <div class="kn-log">${knMd(t.log, 'log.md')}</div>` : '';

    // Full flat listing kept for completeness, but folded away — the sections
    // above are the primary read.
    const dirs = Object.keys(t.dirs || {}).sort();
    const browse = dirs.map(d => `
        <div class="kn-dir">
          <div class="kn-dir-head">${escHtml(d === '.' ? 'root' : d)}
            <span class="kn-dim">${t.dirs[d].length}</span></div>
          ${t.dirs[d].slice().sort((a, b) => a.title.localeCompare(b.title)).map(knCardRow).join('')}
        </div>`).join('');

    el.innerHTML = `
      <div class="kn-glance">
        <div class="kn-section-title">Glance
          <span class="kn-dim">· OKF v${escHtml(t.okf_version || '')}</span></div>
        <div class="kn-digest">${digest || '<span class="kn-dim">empty bundle</span>'}</div>
        ${sessions}
        ${projChips}
        ${otherSections}
        ${activity}
        <details class="kn-browse">
          <summary>Browse all ${t.total} concepts</summary>
          ${browse || '<div class="kn-dim">No concepts.</div>'}
        </details>
      </div>`;
}

// A standard compact card (search results, backlinks, the flat browse list).
function knCardRow(c) {
    const ts = c.timestamp ? `<span class="kn-card-ts ts">${relativeTime(c.timestamp)}</span>` : '';
    return `
      <div class="kn-card" onclick="knOpen('${attrEsc(c.path)}')">
        <span class="kn-badge kn-badge-${knTypeClass(c.type)}">${escHtml(c.type || 'concept')}</span>
        <span class="kn-card-title">${escHtml(c.title)}</span>
        ${c.description ? `<span class="kn-card-desc">${escHtml(c.description)}</span>` : ''}
        ${ts}
      </div>`;
}

// A richer agent row for the glance feed: name → project, what it's doing
// (recap-derived description), and its latest PR — the insight, not boilerplate.
function knAgentRow(a, projByTitle) {
    const projPath = a.project && projByTitle[a.project];
    const proj = a.project
        ? (projPath
            ? `<a class="kn-feed-proj" onclick="event.stopPropagation();knOpen('${attrEsc(projPath)}')">${escHtml(a.project)}</a>`
            : `<span class="kn-feed-proj">${escHtml(a.project)}</span>`)
        : '';
    const ts = a.timestamp ? `<span class="kn-card-ts ts">${relativeTime(a.timestamp)}</span>` : '';
    const pr = a.pr_url
        ? `<a class="kn-pr" href="${attrEsc(a.pr_url)}" target="_blank" rel="noopener"
             onclick="event.stopPropagation()">PR ↗</a>` : '';
    return `
      <div class="kn-feed-row" onclick="knOpen('${attrEsc(a.path)}')">
        <span class="kn-badge kn-badge-agent">Agent</span>
        <div class="kn-feed-main">
          <div class="kn-feed-top">
            <span class="kn-card-title">${escHtml(a.title)}</span>
            ${proj ? '<span class="kn-feed-arrow">→</span>' + proj : ''}
            ${ts}
          </div>
          ${a.description ? `<div class="kn-feed-desc">${escHtml(a.description)}</div>` : ''}
        </div>
        ${pr}
      </div>`;
}

function knProjectChip(p) {
    return `
      <button class="kn-pchip" onclick="knOpen('${attrEsc(p.path)}')">
        <span class="kn-pchip-name">${escHtml(p.title)}</span>
        ${p.description ? `<span class="kn-dim">${escHtml(p.description)}</span>` : ''}
      </button>`;
}

// --- Concept detail (frontmatter card + body + backlinks) ------------------

async function knOpen(path) {
    _kn.view = 'concept';
    _kn.path = path;
    const el = $('#kn-content');
    if (el) el.innerHTML = '<div class="kn-loading">Loading…</div>';
    let c;
    try {
        c = await api('/api/knowledge/concept?path=' + encodeURIComponent(path));
    } catch (e) {
        if (el) el.innerHTML = '<div class="kn-dim">Could not load concept.</div>';
        return;
    }
    if (!el) return;
    if (c.error || !c.path) {
        el.innerHTML = `<div class="kn-detail"><a class="kn-back" onclick="knBackToGlance()">← Knowledge</a>
            <div class="kn-dim">Concept not found.</div></div>`;
        return;
    }

    const fm = c.frontmatter || {};
    const resource = fm.resource
        ? `<a class="kn-resource" href="${attrEsc(fm.resource)}" target="_blank" rel="noopener">${escHtml(fm.resource)}</a>`
        : '';
    const tags = Array.isArray(fm.tags) && fm.tags.length
        ? `<div class="kn-tags">${fm.tags.map(t => `<span class="kn-tag">${escHtml(String(t))}</span>`).join('')}</div>` : '';
    const ts = fm.timestamp ? `<span class="kn-dim">${relativeTime(fm.timestamp)}</span>` : '';

    // Backlinks — the headline feature: what links TO this concept.
    const back = (c.backlinks || []).length
        ? c.backlinks.map(b => `
            <div class="kn-card" onclick="knOpen('${attrEsc(b.path)}')">
              <span class="kn-badge kn-badge-${knTypeClass(b.type)}">${escHtml(b.type || 'concept')}</span>
              <span class="kn-card-title">${escHtml(b.title)}</span>
            </div>`).join('')
        : '<div class="kn-dim">Nothing links here yet.</div>';

    // Raw frontmatter (preserve unknown keys per OKF consumer spec).
    const rawRows = Object.keys(fm).map(k =>
        `<tr><td class="kn-raw-k">${escHtml(k)}</td><td>${escHtml(knScalar(fm[k]))}</td></tr>`).join('');

    el.innerHTML = `
      <div class="kn-detail">
        <a class="kn-back" onclick="knBackToGlance()">← Knowledge</a>
        <div class="kn-head-card">
          <div class="kn-head-top">
            <span class="kn-badge kn-badge-${knTypeClass(c.type)}">${escHtml(c.type || 'concept')}</span>
            <h1 class="kn-title">${escHtml(c.title)}</h1>
            ${ts}
          </div>
          ${fm.description ? `<div class="kn-card-desc">${escHtml(fm.description)}</div>` : ''}
          ${resource}
          ${tags}
        </div>
        <div class="kn-body">${knMd(c.body || '', c.path)}</div>
        <div class="kn-panel">
          <div class="kn-sub">Referenced by <span class="kn-dim">${(c.backlinks || []).length}</span></div>
          ${back}
        </div>
        <details class="kn-raw">
          <summary>Raw frontmatter (${Object.keys(fm).length})</summary>
          <table class="kn-raw-tbl"><tbody>${rawRows}</tbody></table>
        </details>
      </div>`;
}

// --- Search ----------------------------------------------------------------

let _knSearchTimer = null;
function knOnSearch(q) {
    _kn.q = q;
    clearTimeout(_knSearchTimer);
    _knSearchTimer = setTimeout(() => knRunSearch(q.trim()), 180);
}

function knFilterType(type) {
    const s = $('#kn-search');
    if (s) s.value = '';
    _kn.q = '';
    knRunSearch('', type);
}

async function knRunSearch(q, type) {
    if (!q && !type) { knBackToGlance(); return; }
    _kn.view = 'search';
    const el = $('#kn-content');
    let params = '/api/knowledge/search?q=' + encodeURIComponent(q || '');
    if (type) params += '&type=' + encodeURIComponent(type);
    const rows = await api(params);
    if (!el) return;
    const head = `<div class="kn-section-title">Search
        <span class="kn-dim">· ${rows.length} result${rows.length === 1 ? '' : 's'}${type ? ' · type ' + escHtml(type) : ''}</span>
        <a class="kn-back" style="float:right" onclick="knBackToGlance()">← Knowledge</a></div>`;
    const body = rows.length ? rows.map(r => `
        <div class="kn-card" onclick="knOpen('${attrEsc(r.path)}')">
          <span class="kn-badge kn-badge-${knTypeClass(r.type)}">${escHtml(r.type || 'concept')}</span>
          <span class="kn-card-title">${escHtml(r.title)}</span>
          ${r.description ? `<span class="kn-card-desc">${escHtml(r.description)}</span>` : ''}
          ${r.snippet ? `<div class="kn-snippet">${escHtml(r.snippet)}</div>` : ''}
        </div>`).join('') : '<div class="kn-dim">No matches.</div>';
    el.innerHTML = `<div class="kn-glance">${head}${body}</div>`;
}

// --- Graph -----------------------------------------------------------------

async function knShowGraph() {
    _kn.view = 'graph';
    const el = $('#kn-content');
    if (el) el.innerHTML = '<div class="kn-loading">Building graph…</div>';
    const g = await api('/api/knowledge/graph');
    if (!el) return;
    const nodes = g.nodes || [], edges = g.edges || [];
    if (!nodes.length) {
        el.innerHTML = '<div class="kn-glance"><a class="kn-back" onclick="knBackToGlance()">← Knowledge</a>'
            + '<div class="kn-dim">No concepts to graph.</div></div>';
        return;
    }
    // Circular layout: cheap, deterministic, dependency-free. Position nodes on a
    // ring, draw edges as lines, label each node; click to open the concept.
    const W = 760, H = 520, cx = W / 2, cy = H / 2, R = Math.min(W, H) / 2 - 70;
    const pos = {};
    nodes.forEach((n, i) => {
        const a = (i / nodes.length) * 2 * Math.PI - Math.PI / 2;
        pos[n.id] = { x: cx + R * Math.cos(a), y: cy + R * Math.sin(a) };
    });
    const lines = edges.map(e => {
        const a = pos[e.source], b = pos[e.target];
        if (!a || !b) return '';
        return `<line x1="${a.x.toFixed(1)}" y1="${a.y.toFixed(1)}" x2="${b.x.toFixed(1)}" y2="${b.y.toFixed(1)}" class="kn-edge"/>`;
    }).join('');
    const dots = nodes.map(n => {
        const p = pos[n.id];
        const anchor = p.x < cx - 20 ? 'end' : (p.x > cx + 20 ? 'start' : 'middle');
        const dx = p.x < cx - 20 ? -8 : (p.x > cx + 20 ? 8 : 0);
        return `<g class="kn-node kn-node-${knTypeClass(n.type)}" onclick="knOpen('${attrEsc(n.id)}')">
            <circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="6"/>
            <text x="${(p.x + dx).toFixed(1)}" y="${(p.y + 4).toFixed(1)}" text-anchor="${anchor}">${escHtml(n.title)}</text>
          </g>`;
    }).join('');
    el.innerHTML = `
      <div class="kn-glance">
        <div class="kn-section-title">Graph
          <span class="kn-dim">· ${nodes.length} concepts · ${edges.length} links</span>
          <a class="kn-back" style="float:right" onclick="knBackToGlance()">← Knowledge</a></div>
        <div class="kn-graph-wrap">
          <svg viewBox="0 0 ${W} ${H}" class="kn-graph" preserveAspectRatio="xMidYMid meet">
            <g class="kn-edges">${lines}</g>${dots}
          </svg>
        </div>
      </div>`;
}

// --- Shared helpers (also reused by the portable viewer, Phase 4) ----------

// Short, deterministic class suffix for a type badge / node colour.
function knTypeClass(type) {
    const t = (type || '').toLowerCase();
    if (t.includes('agent')) return 'agent';
    if (t.includes('run')) return 'run';
    if (t.includes('schedul')) return 'sched';
    if (t.includes('project')) return 'project';
    return 'other';
}

function knScalar(v) {
    if (v === null || v === undefined) return '';
    if (Array.isArray(v)) return v.join(', ');
    if (typeof v === 'object') return JSON.stringify(v);
    return String(v);
}

// Resolve a bundle markdown link (relative or /absolute) to a root-relative
// posix path, given the linking file's own path. Mirrors okf._resolve_link.
function knResolve(base, href) {
    href = href.split('#')[0];
    if (href.startsWith('/')) return href.replace(/^\/+/, '');
    const dir = base.includes('/') ? base.slice(0, base.lastIndexOf('/')) : '';
    const parts = (dir ? dir.split('/') : []).concat(href.split('/'));
    const out = [];
    for (const p of parts) {
        if (p === '' || p === '.') continue;
        if (p === '..') out.pop(); else out.push(p);
    }
    return out.join('/');
}

function knLink(text, href, base) {
    if (/^(https?:|mailto:)/i.test(href)) {
        return `<a href="${attrEsc(href)}" target="_blank" rel="noopener">${text}</a>`;
    }
    if (href.startsWith('#')) return text;
    if (href.split('#')[0].endsWith('.md')) {
        return `<a class="kn-link" onclick="knOpen('${attrEsc(knResolve(base, href))}')">${text}</a>`;
    }
    return `<a href="${attrEsc(href)}" target="_blank" rel="noopener">${text}</a>`;
}

// Inline markdown on an already-HTML-escaped string: code, links, bold.
function knInline(s, base) {
    s = escHtml(s);
    s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
    s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (m, t, href) => knLink(t, href, base));
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    return s;
}

// Minimal block-level markdown → HTML for OKF bodies (headings, lists,
// blockquotes, fenced code, paragraphs). Intentionally tiny and dependency-free.
function knMd(src, base) {
    const lines = (src || '').split('\n');
    let html = '', inList = false, inCode = false;
    const closeList = () => { if (inList) { html += '</ul>'; inList = false; } };
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
        if (h) { closeList(); const lv = h[1].length; html += `<h${lv} class="kn-mh">${knInline(h[2], base)}</h${lv}>`; continue; }
        if (/^>\s?/.test(line)) { closeList(); html += `<blockquote>${knInline(line.replace(/^>\s?/, ''), base)}</blockquote>`; continue; }
        const li = line.match(/^[-*]\s+(.*)$/);
        if (li) { if (!inList) { html += '<ul class="kn-ul">'; inList = true; } html += `<li>${knInline(li[1], base)}</li>`; continue; }
        closeList();
        html += `<p>${knInline(line, base)}</p>`;
    }
    closeList();
    if (inCode) html += '</code></pre>';
    return html;
}
