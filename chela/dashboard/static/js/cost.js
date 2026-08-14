// ---------------------------------------------------------------------------
// COST — fleet spend, revived (CMX-287) as a tab in the Settings modal rather
// than its own nav view (CMX-279 deleted the view; the backend route and this
// data were never touched — see git history on this file for the pre-CMX-279
// standalone-view version).
//
// The statusLine payload carries Claude Code's own cumulative session cost
// (cost.total_cost_usd); context.py._parse_cache_file extracts it and it lands
// in context_snapshots.cost_usd. /api/cost?window=... (app.py) serves it two
// ways: `live` is a current-snapshot read (context.live_snapshot, no DB
// dependency); `today`/`7d`/`30d` are period rollups over context_snapshots
// history via context.windowed_cost.
//
// "Project" is the same grouping the sidebar already uses (_agentProject, from
// the session's cwd basename) — joined in here via a parallel /api/agents
// fetch, exactly like refreshSidebar() already joins agents + context to paint
// the ctx% chip. One convention for "what project is this agent", not two.
// ---------------------------------------------------------------------------
import { $, $$, _agentProject, api, escHtml } from './util.js';

// The selected window is UI state, not fetch state — persisted per browser
// like work.js's segment, so a reload lands you back on the period you had
// open instead of resetting to Live.
const WINDOWS = ['live', 'today', '7d', '30d'];
const WINDOW_KEY = 'chela_cost_window';

function _loadWindow() {
    try {
        const v = localStorage.getItem(WINDOW_KEY);
        return WINDOWS.includes(v) ? v : 'live';
    } catch (e) { return 'live'; }
}
let _window = _loadWindow();

function _applyWindowButtons() {
    $$('#cost-window .cost-window-btn').forEach(b => {
        const on = b.dataset.win === _window;
        b.classList.toggle('active', on);
        b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
}

function setCostWindow(win) {
    _window = WINDOWS.includes(win) ? win : 'live';
    try { localStorage.setItem(WINDOW_KEY, _window); } catch (e) { /* ignore */ }
    _applyWindowButtons();
    return refreshCost();
}

function _fmtCost(v) {
    return v == null ? null : '$' + v.toFixed(2);
}

// Pure over its input: rows shaped [{name, project, model, cost_usd}], grouped
// into a table of project subtotals + agent lines + a fleet-total footer.
function renderCostTable(rows) {
    const host = $('#cost-table');
    if (!host) return;
    const list = rows || [];

    if (!list.length) {
        host.innerHTML = '<div class="side-empty">No cost data yet — install the '
            + 'statusLine hook (<code>chela install-statusline</code>) so agents report '
            + 'session cost.</div>';
        return;
    }

    const byProject = {};
    list.forEach(r => { (byProject[r.project] = byProject[r.project] || []).push(r); });

    const projects = Object.keys(byProject).map(name => {
        const agents = byProject[name].slice().sort((a, b) => (b.cost_usd || 0) - (a.cost_usd || 0));
        const known = agents.some(a => a.cost_usd != null);
        const total = agents.reduce((s, a) => s + (a.cost_usd || 0), 0);
        return { name, agents, total, known };
    }).sort((a, b) => b.total - a.total);

    let fleetTotal = 0, fleetKnown = false;
    list.forEach(r => { if (r.cost_usd != null) { fleetTotal += r.cost_usd; fleetKnown = true; } });

    let body = '';
    for (const p of projects) {
        body += `<tbody class="cost-project">
            <tr class="cost-project-row">
                <td colspan="3">${escHtml(p.name)}</td>
                <td class="num">${p.known ? _fmtCost(p.total) : '—'}</td>
            </tr>`;
        for (const a of p.agents) {
            body += `<tr class="cost-agent-row">
                <td></td>
                <td>${escHtml(a.name)}</td>
                <td>${escHtml(a.model || '—')}</td>
                <td class="num">${_fmtCost(a.cost_usd) || '—'}</td>
            </tr>`;
        }
        body += '</tbody>';
    }

    host.innerHTML = `<table class="cost-table">
        <thead><tr><th>Project</th><th>Agent</th><th>Model</th><th class="num">Cost</th></tr></thead>
        ${body}
        <tfoot><tr class="cost-total-row">
            <td colspan="3">Fleet total</td>
            <td class="num">${fleetKnown ? _fmtCost(fleetTotal) : '—'}</td>
        </tr></tfoot>
    </table>`;
}

// Fetch + join: /api/cost (scoped to the selected window) has the cost,
// /api/agents has the cwd _agentProject groups by. Same two-fetch shape as
// refreshSidebar(). Both responses are defensively coerced to arrays — the
// Settings modal's other tabs tolerate a bare `{}` from a flaky/mocked
// endpoint (see settings_dispatch.test.mjs's catch-all), and this tab must too
// rather than throwing out of an un-awaited call in renderSettings().
async function refreshCost() {
    _applyWindowButtons();
    const host = $('#cost-table');
    let agents, ctx;
    try {
        [agents, ctx] = await Promise.all([api('/api/agents'), api(`/api/cost?window=${_window}`)]);
    } catch (e) {
        if (host) host.innerHTML = '<div class="side-empty">Cost data unavailable.</div>';
        return;
    }
    const cwdByName = {};
    (Array.isArray(agents) ? agents : []).forEach(a => { cwdByName[a.name] = a.cwd; });
    const rows = (Array.isArray(ctx) ? ctx : []).map(c => ({
        name: c.name,
        model: c.model,
        cost_usd: c.cost_usd,
        project: _agentProject({ cwd: cwdByName[c.name] }) || '(unknown)',
    }));
    renderCostTable(rows);
}

window.chela = window.chela || {};
Object.assign(window.chela, { setCostWindow });

// --- Stage 0: ES-module exports ---
export { refreshCost, renderCostTable, setCostWindow };
