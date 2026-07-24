// --- Stage 0: ES-module imports ---
import { $, api, attrEsc, escHtml, relativeTime } from './util.js';

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

const _kn = { tree: null, view: 'glance', path: null, q: '', sigma: null };

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
    knKillGraph();
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
            <span class="work-empty-cta" onclick="chela.knRefresh()">Export the bundle →</span>
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
      <div class="kn-card" onclick="chela.knOpen('${attrEsc(c.path)}')">
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
            ? `<a class="kn-feed-proj" onclick="event.stopPropagation();chela.knOpen('${attrEsc(projPath)}')">${escHtml(a.project)}</a>`
            : `<span class="kn-feed-proj">${escHtml(a.project)}</span>`)
        : '';
    const ts = a.timestamp ? `<span class="kn-card-ts ts">${relativeTime(a.timestamp)}</span>` : '';
    const pr = a.pr_url
        ? `<a class="kn-pr" href="${attrEsc(a.pr_url)}" target="_blank" rel="noopener"
             onclick="event.stopPropagation()">PR ↗</a>` : '';
    return `
      <div class="kn-feed-row" onclick="chela.knOpen('${attrEsc(a.path)}')">
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
      <button class="kn-pchip" onclick="chela.knOpen('${attrEsc(p.path)}')">
        <span class="kn-pchip-name">${escHtml(p.title)}</span>
        ${p.description ? `<span class="kn-dim">${escHtml(p.description)}</span>` : ''}
      </button>`;
}

// --- Concept detail (frontmatter card + body + backlinks) ------------------

async function knOpen(path) {
    knKillGraph();
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
        el.innerHTML = `<div class="kn-detail"><a class="kn-back" onclick="chela.knBackToGlance()">← Knowledge</a>
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
            <div class="kn-card" onclick="chela.knOpen('${attrEsc(b.path)}')">
              <span class="kn-badge kn-badge-${knTypeClass(b.type)}">${escHtml(b.type || 'concept')}</span>
              <span class="kn-card-title">${escHtml(b.title)}</span>
            </div>`).join('')
        : '<div class="kn-dim">Nothing links here yet.</div>';

    // Raw frontmatter (preserve unknown keys per OKF consumer spec).
    const rawRows = Object.keys(fm).map(k =>
        `<tr><td class="kn-raw-k">${escHtml(k)}</td><td>${escHtml(knScalar(fm[k]))}</td></tr>`).join('');

    el.innerHTML = `
      <div class="kn-detail">
        <a class="kn-back" onclick="chela.knBackToGlance()">← Knowledge</a>
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
    knKillGraph();
    _kn.view = 'search';
    const el = $('#kn-content');
    let params = '/api/knowledge/search?q=' + encodeURIComponent(q || '');
    if (type) params += '&type=' + encodeURIComponent(type);
    const rows = await api(params);
    if (!el) return;
    const head = `<div class="kn-section-title">Search
        <span class="kn-dim">· ${rows.length} result${rows.length === 1 ? '' : 's'}${type ? ' · type ' + escHtml(type) : ''}</span>
        <a class="kn-back" style="float:right" onclick="chela.knBackToGlance()">← Knowledge</a></div>`;
    const body = rows.length ? rows.map(r => `
        <div class="kn-card" onclick="chela.knOpen('${attrEsc(r.path)}')">
          <span class="kn-badge kn-badge-${knTypeClass(r.type)}">${escHtml(r.type || 'concept')}</span>
          <span class="kn-card-title">${escHtml(r.title)}</span>
          ${r.description ? `<span class="kn-card-desc">${escHtml(r.description)}</span>` : ''}
          ${r.snippet ? `<div class="kn-snippet">${escHtml(r.snippet)}</div>` : ''}
        </div>`).join('') : '<div class="kn-dim">No matches.</div>';
    el.innerHTML = `<div class="kn-glance">${head}${body}</div>`;
}

// --- Graph -------------------------------------------------------------
//
// Renderer = sigma.js + graphology (WebGL), vendored+minified at
// static/vendor/sigma-graph.min.js and loaded as a global (window.chelaGraphLibs)
// — the same pattern as gridstack. graphology-layout-forceatlas2 gives an actual
// force-directed layout instead of the old hand-rolled circular SVG.
//
// The DOM/WebGL glue (knRenderSigma) is not unit-tested — sigma needs a real
// WebGL context, which jsdom doesn't provide. What IS tested is the pure data
// step (knGraphModel/knNodeColor below): seeding node positions, resolving
// per-type colors, and filtering edges — no graphology/Sigma/DOM involved.

const _KN_GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));

// Deterministic, non-overlapping seed layout (golden-angle spiral). forceAtlas2
// needs a starting position per node before it can spread them; a symmetric seed
// (e.g. a plain circle) leaves force-directed layout stuck at a degenerate fixed
// point, so this is a correctness requirement, not cosmetic.
function knGraphSeed(i) {
    const r = Math.sqrt(i + 1);
    const a = i * _KN_GOLDEN_ANGLE;
    return { x: r * Math.cos(a), y: r * Math.sin(a) };
}

// type -> fixed Okabe-Ito colorblind-safe hex (https://jfly.uni-koeln.de/color/,
// verified distinguishable under deuteranopia/protanopia). NOT theme CSS vars —
// a theme's --green/--accent/--yellow are picked for contrast, not colorblind
// separability, so resolving through them risked two node types landing on
// hues a red-weak viewer can't tell apart. `other` stays the theme-dim grey:
// achromatic, so it's colorblind-safe by construction and still reads as "no
// strong category" across every theme.
const _KN_TYPE_COLOR = {
    agent: '#009E73',   // bluish green
    run: '#0072B2',     // blue
    sched: '#E69F00',   // orange
    project: '#CC79A7', // reddish purple
    other: { varName: '--text-dim', fallback: '#8b949e' },
};

// type -> a short glyph rendered as a label prefix. Colour is reinforcement
// only — Liav is red-weak (deuteranomaly), so the type must also be legible
// from a non-hue cue, and the row must stay identifiable in greyscale.
const _KN_TYPE_GLYPH = {
    agent: 'A', run: 'R', sched: 'S', project: 'P', other: '•',
};

function knNodeGlyph(type) {
    return _KN_TYPE_GLYPH[knTypeClass(type)] || _KN_TYPE_GLYPH.other;
}

// Rendered node label: glyph prefix + title, so the type is legible even with
// colour stripped out (greyscale, colorblind, or a monochrome print of a demo).
function knNodeLabel(type, title) {
    return `[${knNodeGlyph(type)}] ${title}`;
}

// Resolve a concept type to a render color. `cssVar(name, fallback)` is injected
// so this stays pure/testable (production passes knCssVar, which reads the DOM).
// Only `other` still resolves through a theme var; every named type is a fixed
// Okabe-Ito hex, deliberately not theme-dependent (see _KN_TYPE_COLOR above).
function knNodeColor(type, cssVar) {
    const c = _KN_TYPE_COLOR[knTypeClass(type)] || _KN_TYPE_COLOR.other;
    if (typeof c === 'string') return c;
    return cssVar(c.varName, c.fallback);
}

// Build a plain-data graph model from the /api/knowledge/graph response: seeded
// node positions + resolved colors, edges filtered to known node pairs (mirrors
// the old SVG renderer's dangling-edge guard), self-loops dropped, deduped. Pure
// — no graphology/Sigma/DOM — so it's unit-testable without a WebGL context.
function knGraphModel(g, cssVar) {
    const nodes = g.nodes || [];
    const known = new Set(nodes.map(n => n.id));
    const outNodes = nodes.map((n, i) => {
        const seed = knGraphSeed(i);
        return {
            id: n.id, title: n.title, type: n.type,
            x: seed.x, y: seed.y,
            color: knNodeColor(n.type, cssVar),
            label: knNodeLabel(n.type, n.title),
        };
    });
    const seen = new Set();
    const outEdges = [];
    for (const e of (g.edges || [])) {
        if (!known.has(e.source) || !known.has(e.target) || e.source === e.target) continue;
        const key = e.source + '\0' + e.target;
        if (seen.has(key)) continue;
        seen.add(key);
        outEdges.push({ source: e.source, target: e.target });
    }
    return { nodes: outNodes, edges: outEdges };
}

// Read a theme CSS var with a fallback (same pattern as util.js's amber lookup).
function knCssVar(name, fallback) {
    const v = (getComputedStyle(document.body).getPropertyValue(name) || '').trim();
    return v || fallback;
}

// The WebGL glue: graphology Graph -> forceAtlas2 layout -> Sigma renderer.
// Click a node to open its concept; hover swaps the cursor.
function knRenderSigma(container, model) {
    const libs = window.chelaGraphLibs;
    if (!libs || !container) return null;
    const { Graph, Sigma, forceAtlas2 } = libs;
    const graph = new Graph();
    model.nodes.forEach(n => {
        graph.addNode(n.id, { x: n.x, y: n.y, size: 5, label: n.label, color: n.color });
    });
    model.edges.forEach(e => {
        graph.addEdge(e.source, e.target, { size: 1, color: knCssVar('--border', '#21262d') });
    });
    forceAtlas2.assign(graph, {
        iterations: 120,
        settings: { gravity: 1, scalingRatio: 8, barnesHutOptimize: model.nodes.length > 200 },
    });
    const renderer = new Sigma(graph, container, {
        renderLabels: true,
        labelRenderedSizeThreshold: 0,
        labelColor: { color: knCssVar('--text-dim', '#8b949e') },
        defaultEdgeColor: knCssVar('--border', '#21262d'),
    });
    renderer.on('clickNode', ({ node }) => knOpen(node));
    renderer.on('enterNode', () => { container.style.cursor = 'pointer'; });
    renderer.on('leaveNode', () => { container.style.cursor = 'default'; });
    return renderer;
}

// Tear down the live Sigma instance — must run before every view change away
// from 'graph' (knBackToGlance/knOpen/knRunSearch) or its WebGL context leaks.
function knKillGraph() {
    if (_kn.sigma) {
        _kn.sigma.kill();
        _kn.sigma = null;
    }
}

// A visible failure state (back link + message) for knShowGraph. Never leave
// the "Building graph…" spinner frozen or an empty <div id="kn-graph-canvas">
// on screen with nothing explaining why — both read as "still working" or
// "no data" when the real story is "this broke".
function knGraphError(msg) {
    return `<div class="kn-glance"><a class="kn-back" onclick="chela.knBackToGlance()">← Knowledge</a>
        <div class="kn-dim kn-graph-error">⚠ ${escHtml(msg)}</div></div>`;
}

async function knShowGraph() {
    knKillGraph();
    _kn.view = 'graph';
    const el = $('#kn-content');
    if (el) el.innerHTML = '<div class="kn-loading">Building graph…</div>';
    let g;
    try {
        g = await api('/api/knowledge/graph');
    } catch (e) {
        if (el) el.innerHTML = knGraphError('Could not load the graph data.');
        return;
    }
    if (!el) return;
    const nodes = g.nodes || [], edges = g.edges || [];
    if (!nodes.length) {
        el.innerHTML = '<div class="kn-glance"><a class="kn-back" onclick="chela.knBackToGlance()">← Knowledge</a>'
            + '<div class="kn-dim">No concepts to graph.</div></div>';
        return;
    }
    el.innerHTML = `
      <div class="kn-glance">
        <div class="kn-section-title">Graph
          <span class="kn-dim">· ${nodes.length} concepts · ${edges.length} links</span>
          <a class="kn-back" style="float:right" onclick="chela.knBackToGlance()">← Knowledge</a></div>
        <div class="kn-graph-wrap"><div id="kn-graph-canvas" class="kn-graph-canvas"></div></div>
      </div>`;
    const model = knGraphModel(g, knCssVar);
    const renderer = knRenderSigma($('#kn-graph-canvas'), model);
    if (!renderer) {
        el.innerHTML = knGraphError('Graph renderer failed to load.');
        return;
    }
    _kn.sigma = renderer;
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
        return `<a class="kn-link" onclick="chela.knOpen('${attrEsc(knResolve(base, href))}')">${text}</a>`;
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

// --- Stage 0: ES-module exports ---
// knMd/knInline: the dependency-free markdown->HTML renderer, exported for
// taskmodal.js (the task-detail modal's brief pane) to reuse verbatim rather
// than pulling in a markdown library for a second dashboard surface.
export { _kn, knBackToGlance, refreshKnowledge, knGraphModel, knNodeColor, knNodeGlyph, knNodeLabel, knGraphError, knMd, knInline };

// --- Stage 0: window.chela — surface reachable from inline HTML handlers ---
window.chela = window.chela || {};
Object.assign(window.chela, { knBackToGlance, knOnSearch, knOpen, knRefresh, knShowGraph });
