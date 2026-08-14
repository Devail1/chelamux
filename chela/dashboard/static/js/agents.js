// --- Stage 0: ES-module imports ---
import { $, api, closeModal, msgTargetAgent, setMsgTarget, showModal } from './util.js';
import { refresh } from './main.js';

// ---------------------------------------------------------------------------
// Agent actions + context readouts — shared by the agent-detail drill-in
// (nav.js's showAgentDetail/renderAgentDetail) and the header rate-limit
// pills, which are visible from every view. The dedicated Agents grid view
// (refreshAgents -> #agent-grid, the broadcast/rediscover toolbar, and the
// per-card kebab menu's Check/Compact/Clear context actions) was one of the
// five views CMX-279 deleted — Liav named only Wall and Work as views he
// actually opens — so that grid-only rendering is gone with it; what remains
// here is everything still reachable from agent-detail or the always-visible
// chrome.
// ---------------------------------------------------------------------------

function openSendMsg(agent) {
    setMsgTarget(agent);
    $('#modal-msg-agent').textContent = agent;
    $('#modal-msg-text').value = '';
    showModal('modal-msg');
    setTimeout(() => $('#modal-msg-text').focus(), 50);
}

async function doSendMsg() {
    const msg = $('#modal-msg-text').value.trim();
    if (!msg) return;
    await api('/api/agents/msg', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent: msgTargetAgent, message: msg }),
    });
    closeModal('modal-msg');
}

async function triggerSchedule(agent) {
    await api('/api/agents/trigger', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent }),
    });
}

async function stopAgent(agent) {
    if (!confirm('Stop ' + agent + '?')) return;
    const btn = event.target;
    btn.disabled = true;
    btn.textContent = 'Stopping...';
    await api('/api/agents/stop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent }),
    });
    refresh();
}

async function startAgent(agent) {
    const btn = event.target;
    btn.disabled = true;
    btn.textContent = 'Starting...';
    await api('/api/agents/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent }),
    });
    refresh();
}

async function restartAgent(agent) {
    if (!confirm('Restart ' + agent + '?')) return;
    const btn = event.target;
    btn.disabled = true;
    btn.textContent = 'Restarting...';
    await api('/api/agents/restart', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent }),
    });
    refresh();
}

function _rlResetTooltip(resetsAt) {
    // resetsAt is Unix epoch seconds (or null/missing). Returns a human
    // countdown like "Resets in 1 hr 28 min", or null when there's nothing
    // sensible to show (never produces "Resets in NaN"). Long waits (the 7d
    // weekly limit can be ~50 hr out) read in days, not a pile of hours.
    if (resetsAt == null || !isFinite(resetsAt)) return null;
    const msLeft = resetsAt * 1000 - Date.now();
    if (msLeft <= 0) return 'Resets now';
    const totalMin = Math.round(msLeft / 60000);
    if (totalMin < 1) return 'Resets soon';
    const h = Math.floor(totalMin / 60);
    const m = totalMin % 60;
    let span;
    if (h >= 24) {
        const d = Math.floor(h / 24);
        const rh = h % 24;
        span = rh >= 1 ? `${d} day${d > 1 ? 's' : ''} ${rh} hr` : `${d} day${d > 1 ? 's' : ''}`;
    } else if (h >= 1) {
        span = `${h} hr ${m} min`;
    } else {
        span = `${m} min`;
    }
    return `Resets in ${span}`;
}

// How long ago the winning sample was actually written by the agent (ts is now
// the cache file's mtime). Returns null when the timestamp is missing/unusable.
function _readingAge(ts) {
    if (!ts) return null;
    const ms = Date.now() - Date.parse(ts);
    if (!isFinite(ms) || ms < 0) return null;
    const min = Math.round(ms / 60000);
    if (min < 1) return 'updated just now';
    if (min < 60) return `updated ${min} min ago`;
    const h = Math.floor(min / 60);
    return `updated ${h} hr ${min % 60} min ago`;
}

// A reading older than this is shown dimmed — no agent has refreshed its
// statusline recently, so the account-wide number may have moved on.
const RL_STALE_MS = 10 * 60 * 1000;

// A rate-limit is account-wide: pick the freshest sample across agents and
// surface it once in a header pill. Shared by the 5h and 7d (weekly) limits.
function _updateRlPill(pillId, valueId, data, pctKey, resetKey) {
    let pct = null, ts = '', resetsAt = null;
    for (const a of data) {
        if (a[pctKey] != null && (a.ts || '') >= ts) {
            pct = a[pctKey];
            ts = a.ts || '';
            resetsAt = a[resetKey] != null ? a[resetKey] : null;
        }
    }
    const pill = document.getElementById(pillId);
    const value = document.getElementById(valueId);
    if (!pill || !value) return;
    if (pct != null) {
        value.textContent = Math.round(pct) + '%';
        value.className = 'value ' + (pct > 80 ? 'red' : pct > 60 ? 'yellow' : '');
        const stale = ts && (Date.now() - Date.parse(ts)) > RL_STALE_MS;
        pill.style.opacity = stale ? '0.5' : '';
        pill.style.display = '';
        const tip = [_rlResetTooltip(resetsAt), _readingAge(ts)].filter(Boolean).join(' · ');
        if (tip) pill.title = tip; else pill.removeAttribute('title');
    } else {
        pill.style.display = 'none';
        pill.removeAttribute('title');
    }
}

function _renderContextData(data) {
    _updateRlPill('hdr-ratelimit-pill', 'hdr-ratelimit', data, 'rate_limit_pct', 'rate_limit_resets_at');
    _updateRlPill('hdr-weekly-rl-pill', 'hdr-weekly-rl', data, 'weekly_rl_pct', 'weekly_rl_resets_at');
    for (const a of data) {
        const w = document.getElementById(`ctx-${a.name}`);
        if (!w) continue;
        const fill = w.querySelector('.context-bar-fill');
        const label = w.querySelector('.context-label');
        if (a.used_pct != null) {
            fill.style.width = a.used_pct + '%';
            fill.className = 'context-bar-fill' + (a.used_pct > 80 ? ' ctx-danger' : a.used_pct > 60 ? ' ctx-warn' : '');
            let parts = [`Context: ${a.used}/${a.total} (${a.used_pct}%${a.estimated ? '~' : ''})`];
            if (a.model) parts.push(a.model);
            if (a.cost_usd != null) parts.push(`$${a.cost_usd}`);
            if (a.estimated) parts.push('est');
            label.textContent = parts.join(' · ');
            // Tooltip: session name, plus a note when the reading is a transcript
            // estimate (install the statusLine hook for exact context %).
            const tips = [];
            if (a.session_name) tips.push(a.session_name);
            if (a.estimated) tips.push('estimate from transcript — run `chela install-statusline` for exact context %');
            if (tips.length) label.title = tips.join(' · '); else label.removeAttribute('title');
        } else {
            label.textContent = 'Context: unavailable';
        }
    }
}

async function checkContext() {
    const data = await api('/api/agents/context');
    _renderContextData(data);
}

// --- Stage 0: ES-module exports ---
export { checkContext };

// --- Stage 0: window.chela — surface reachable from inline HTML handlers ---
window.chela = window.chela || {};
Object.assign(window.chela, { doSendMsg, openSendMsg, restartAgent, startAgent, stopAgent, triggerSchedule });
