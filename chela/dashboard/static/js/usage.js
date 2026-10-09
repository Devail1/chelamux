// ---------------------------------------------------------------------------
// USAGE — the Cost tab's second view (CMX-38): the same fleet, in tokens.
//
// The Cost view lists $ only for sessions whose statusLine writes a cost file,
// so it could not show what emptied the 5-hour limit on 2026-10-08: a
// third-party bot with a broken prompt cache (~4% cache hit, 300-490k tokens
// written per call). /api/usage (app.py → chela/usage.py) reads EVERY Claude
// Code transcript instead — judges, subagents, dispatched agents, background
// sessions, extra roots like a WSL host's Windows side — and returns:
//
//   limits.{five_hour,seven_day}  used_pct / resets_at / burn_pct_per_h /
//                                 hits_100_before_reset — used_pct null means
//                                 UNKNOWN (missing or stale) and renders so,
//                                 never as an empty 0% bar.
//   windows.{30m,today}.rows      one row per transcript, heaviest first.
//
// The "Cost | Usage" toggle lives here too: refreshCostTab() is what the tab
// rail calls, and it refreshes whichever view is showing.
// ---------------------------------------------------------------------------
import { $, $$, api, escHtml } from './util.js';
import { refreshCost } from './cost.js';

const VIEWS = ['cost', 'usage'];
const VIEW_KEY = 'chela_cost_view';
const UWINDOWS = ['30m', 'today'];
const UWINDOW_KEY = 'chela_usage_window';

function _load(key, valid, dflt) {
    try {
        const v = localStorage.getItem(key);
        return valid.includes(v) ? v : dflt;
    } catch (e) { return dflt; }
}
function _store(key, v) {
    try { localStorage.setItem(key, v); } catch (e) { /* ignore */ }
}

let _view = _load(VIEW_KEY, VIEWS, 'cost');
let _uwindow = _load(UWINDOW_KEY, UWINDOWS, '30m');
let _last = null;   // the last /api/usage payload, re-rendered on a window switch

function _paintSeg(sel, attr, current) {
    $$(sel).forEach(b => {
        const on = b.dataset[attr] === current;
        b.classList.toggle('active', on);
        b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
}

function _applyView() {
    _paintSeg('#cost-view .cost-view-btn', 'view', _view);
    const cost = $('#cost-pane'), usage = $('#usage-pane');
    if (cost) cost.hidden = _view !== 'cost';
    if (usage) usage.hidden = _view !== 'usage';
}

function refreshCostTab() {
    _applyView();
    return _view === 'usage' ? refreshUsage() : refreshCost();
}

function setCostView(view) {
    _view = VIEWS.includes(view) ? view : 'cost';
    _store(VIEW_KEY, _view);
    return refreshCostTab();
}

function setUsageWindow(win) {
    _uwindow = UWINDOWS.includes(win) ? win : '30m';
    _store(UWINDOW_KEY, _uwindow);
    _paintSeg('#usage-window .usage-window-btn', 'win', _uwindow);
    if (_last) renderUsageTable(_rowsFor(_last));
}

function _rowsFor(payload) {
    const w = payload && payload.windows && payload.windows[_uwindow];
    return (w && Array.isArray(w.rows)) ? w.rows : [];
}

// 1234 → "1.2k", 3400000 → "3.4M".
function fmtTokens(n) {
    if (n == null) return '—';
    if (n >= 1e9) return (n / 1e9).toFixed(1) + 'B';
    if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
    if (n >= 1e3) return (n / 1e3).toFixed(1) + 'k';
    return String(n);
}

function _fmtWhen(epoch) {
    if (epoch == null) return '';
    const s = Math.round(epoch - Date.now() / 1000);
    if (s <= 0) return 'now';
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    if (h >= 24) return `${Math.floor(h / 24)}d ${h % 24}h`;
    return h ? `${h}h ${m}m` : `${m}m`;
}

// One limit bar. `e.used_pct == null` is UNKNOWN: no fill, the word "unknown"
// and the reason — a missing or stale sample must never read as an empty 0%.
function renderLimitBar(label, e) {
    const entry = e || {};
    if (entry.used_pct == null) {
        return `<div class="usage-limit unknown" data-limit="${escHtml(label)}">
            <div class="usage-limit-head"><span>${escHtml(label)}</span>
                <span class="usage-limit-val">unknown</span></div>
            <div class="usage-bar" role="img" aria-label="${escHtml(label)}: unknown"><div class="usage-bar-fill" style="width:0"></div></div>
            <div class="usage-limit-sub">${escHtml(entry.reason || 'no data')}</div>
        </div>`;
    }
    const pct = Math.max(0, Math.min(100, entry.used_pct));
    const level = pct >= 80 ? 'high' : pct >= 60 ? 'warn' : '';
    const burn = entry.burn_pct_per_h == null ? 'burn n/a'
        : `burn ${entry.burn_pct_per_h.toFixed(1)}%/h`;
    let proj = '';
    if (entry.hits_100_before_reset === true) {
        proj = `<span class="usage-flag">▲ REACHES 100% BEFORE RESET</span>`;
    } else if (entry.hits_100_before_reset === false) {
        proj = `<span>projected ${entry.projected_pct}% at reset</span>`;
    }
    return `<div class="usage-limit ${level}" data-limit="${escHtml(label)}">
        <div class="usage-limit-head"><span>${escHtml(label)}</span>
            <span class="usage-limit-val">${entry.used_pct}%</span></div>
        <div class="usage-bar" role="img" aria-label="${escHtml(label)}: ${entry.used_pct}% used"><div class="usage-bar-fill" style="width:${pct}%"></div></div>
        <div class="usage-limit-sub"><span>resets in ${_fmtWhen(entry.resets_at)}</span>
            <span>${burn}</span>${proj}</div>
    </div>`;
}

function renderLimits(limits) {
    const host = $('#usage-limits');
    if (!host) return;
    const l = limits || {};
    host.innerHTML = renderLimitBar('5h', l.five_hour) + renderLimitBar('7d', l.seven_day);
}

function sparkline(values) {
    const v = Array.isArray(values) ? values : [];
    if (!v.length) return '';
    const max = Math.max(...v, 1), w = 60, h = 16, bw = w / v.length;
    const bars = v.map((x, i) => {
        const bh = x > 0 ? Math.max(1, (x / max) * h) : 0;
        return `<rect x="${(i * bw).toFixed(2)}" y="${(h - bh).toFixed(2)}" width="${Math.max(1, bw - 1).toFixed(2)}" height="${bh.toFixed(2)}"/>`;
    }).join('');
    return `<svg class="usage-spark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-hidden="true">${bars}</svg>`;
}

function _hitCell(r) {
    const hit = r.cache_hit == null ? '—' : Math.round(r.cache_hit * 100) + '%';
    // Flagged as a WORD plus a SHAPE, not colour alone.
    return r.cache_broken
        ? `${hit} <span class="usage-flag" title="cache hit under 50% over ≥10 requests and >1M tokens">▲ CACHE BROKEN</span>`
        : hit;
}

function renderUsageTable(rows) {
    const host = $('#usage-table');
    if (!host) return;
    const list = rows || [];
    if (!list.length) {
        host.innerHTML = `<div class="side-empty">No Claude Code requests in this window.</div>`;
        return;
    }
    const body = list.map(r => {
        const sub = r.ai_title ? `<div class="usage-sub">${escHtml(r.ai_title)}</div>` : '';
        return `<tr class="usage-row${r.cache_broken ? ' broken' : ''}">
            <td>${escHtml(r.label || r.session_id || '')}${sub}</td>
            <td>${escHtml(r.model || '—')}</td>
            <td class="num">${r.requests}</td>
            <td class="num">${fmtTokens(r.input)}</td>
            <td class="num">${fmtTokens(r.cache_write)}</td>
            <td class="num">${fmtTokens(r.cache_read)}</td>
            <td class="num">${fmtTokens(r.output)}</td>
            <td class="num usage-weighted">${fmtTokens(r.weighted)}</td>
            <td class="num usage-hit">${_hitCell(r)}</td>
            <td>${sparkline(r.spark)}</td>
        </tr>`;
    }).join('');
    host.innerHTML = `<div class="usage-scroll"><table class="cost-table usage-table">
        <thead><tr><th>Session</th><th>Model</th><th class="num">Req</th><th class="num">Input</th>
            <th class="num">Cache write</th><th class="num">Cache read</th><th class="num">Output</th>
            <th class="num" title="input×1 + cache write×1.25 + cache read×0.1 + output×5">Weighted</th>
            <th class="num">Cache hit</th><th>Activity</th></tr></thead>
        <tbody>${body}</tbody>
    </table></div>`;
}

function _renderRoots(roots) {
    const inp = $('#usage-roots');
    if (!inp || !roots) return;
    if (document.activeElement !== inp) inp.value = (roots.extra || []).join('\n');
    const note = $('#usage-roots-scanned');
    if (note) note.textContent = 'Scanning: ' + ((roots.scanned || []).join(', ') || 'nothing');
}

async function refreshUsage() {
    _paintSeg('#usage-window .usage-window-btn', 'win', _uwindow);
    let payload;
    try {
        payload = await api('/api/usage');
    } catch (e) {
        const host = $('#usage-table');
        if (host) host.innerHTML = '<div class="side-empty">Usage data unavailable.</div>';
        renderLimits(null);
        return;
    }
    _last = (payload && typeof payload === 'object') ? payload : {};
    renderLimits(_last.limits);
    renderUsageTable(_rowsFor(_last));
    _renderRoots(_last.roots);
}

async function saveUsageRoots() {
    const inp = $('#usage-roots');
    const msg = $('#usage-roots-msg');
    if (!inp) return;
    let res;
    try {
        res = await api('/api/usage/roots', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ roots: inp.value }),
        });
    } catch (e) {
        res = null;
    }
    if (!res || !res.ok) {
        if (msg) { msg.className = 's-savemsg err'; msg.textContent = (res && res.error) || 'Save failed'; }
        return;
    }
    if (msg) { msg.className = 's-savemsg ok'; msg.textContent = 'Saved'; }
    await refreshUsage();
}

window.chela = window.chela || {};
Object.assign(window.chela, { setCostView, setUsageWindow, saveUsageRoots });

export {
    fmtTokens, refreshCostTab, refreshUsage, renderLimitBar, renderLimits, renderUsageTable,
    saveUsageRoots, setCostView, setUsageWindow, sparkline,
};
