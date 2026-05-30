// ---------------------------------------------------------------------------
// Render: Agents
// ---------------------------------------------------------------------------

function _sortAgents(agents) {
    // Plain alphabetical — every window is a peer session (no managed roster).
    return agents.sort((a, b) => a.name.localeCompare(b.name));
}

function _renderCard(a) {
    const name = escHtml(a.name);

    // Health dot comes straight from /api/agents liveness:
    // green (alive) / yellow (waiting on input) / red (offline).
    const dotColor = a.health || (a.claude_running ? 'green' : 'grey');

    // Kebab menu — same controls for all agents
    let menuItems = `
        <div class="menu-item" onclick="openSendMsg('${name}')">Message</div>`;
    if (a.has_schedules) {
        menuItems += `
        <div class="menu-item" onclick="triggerSchedule('${name}')">Trigger Schedule</div>`;
    }
    menuItems += `
        <div class="menu-sep"></div>
        <details class="menu-group">
            <summary class="menu-item">Context ▸</summary>
            <div class="menu-item" onclick="checkAgentContext('${name}')">Check</div>
            <div class="menu-item" onclick="compactAgent('${name}')">Compact</div>
            <div class="menu-item" onclick="clearContext('${name}')">Clear</div>
        </details>
        <div class="menu-sep"></div>`;
    if (a.claude_running) {
        menuItems += `
        <div class="menu-item" onclick="restartAgent('${name}')">Restart</div>
        <div class="menu-item menu-danger" onclick="stopAgent('${name}')">Stop</div>`;
    } else {
        menuItems += `
        <div class="menu-item" onclick="startAgent('${name}')">Start</div>`;
    }

    // Liveness line: native session status (busy/idle/waiting) when claude is
    // running, else a plain offline note. Replaces the old heartbeat age.
    const statusLine = a.session_status
        ? `<span>Status: ${escHtml(a.session_status)}</span>`
        : (a.claude_running ? '<span>Status: running</span>' : '');

    let scheduleLine = '';
    if (a.schedule_last_run || a.schedule_next_run) {
        const parts = [];
        if (a.schedule_last_run) parts.push('Last: ' + shortTime(a.schedule_last_run));
        if (a.schedule_next_run) parts.push('Next: ' + relativeTime(a.schedule_next_run));
        scheduleLine = `<span>${parts.join(' · ')}</span>`;
    }

    // Recap (latest away_summary) — truncated by default, expand on click.
    // <details>/<summary> handles the toggle for free; full text is in the
    // summary so the truncated head reads as a sentence, full body shown
    // below when expanded. Hidden entirely if no recap.
    let recapBlock = '';
    if (a.recap) {
        const head = a.recap.length > 90 ? a.recap.slice(0, 90).trimEnd() + '…' : a.recap;
        // Recaps are sparse (Claude Code emits away_summary only occasionally),
        // so surface the record's age — dimmed once >1h — to make a lagging
        // recap read as stale instead of looking current.
        let ageTag = '';
        if (a.recap_ts) {
            const ageS = (Date.now() - new Date(a.recap_ts)) / 1000;
            const cls = ageS > 3600 ? 'recap-age stale' : 'recap-age';
            ageTag = ` <span class="${cls}">${ageStr(ageS)}</span>`;
        }
        recapBlock = `
        <details class="agent-recap">
            <summary><span class="recap-label">Recap</span>${ageTag} <span class="recap-head">${escHtml(head)}</span></summary>
            <div class="recap-body">${escHtml(a.recap)}</div>
        </details>`;
    }

    // PR badge — last pr-link record; clickable, opens in new tab. Hidden
    // if no PR (don't render placeholder).
    let prBadge = '';
    if (a.pr && a.pr.url) {
        const label = a.pr.number ? `PR #${a.pr.number}` : 'PR';
        const repo = a.pr.repository ? `${a.pr.repository} ` : '';
        prBadge = `<a class="pr-badge" href="${escHtml(a.pr.url)}" target="_blank" rel="noopener noreferrer" title="${escHtml(repo + label)}">${escHtml(label)}</a>`;
    }

    return `
    <div class="agent-card agent-card-session">
        <div class="agent-header">
            <div class="agent-name">
                <span class="health-dot ${dotColor}"></span>
                ${escHtml(_displayLabel(a.window_id || a.name))}
                <span class="claude-badge ${a.claude_running ? 'claude-on' : 'claude-off'}">${a.claude_running ? 'running' : 'stopped'}</span>
                ${prBadge}
            </div>
            <div class="kebab-wrap">
                <button class="kebab-btn" onclick="toggleMenu(this)">&#8942;</button>
                <div class="kebab-menu">${menuItems}</div>
            </div>
        </div>
        <div class="agent-meta">
            <span>Window: ${a.window_id || 'none'}</span>
            ${statusLine}
            ${scheduleLine}
        </div>
        <div class="context-bar-wrap" id="ctx-${name}">
            <div class="context-bar"><div class="context-bar-fill" style="width:0%"></div></div>
            <span class="context-label">Context: --</span>
        </div>
        ${recapBlock}
    </div>`;
}

async function refreshAgents() {
    const agents = await api('/api/agents');
    _agentsCache = agents;
    const sorted = _sortAgents(agents);

    const grid = $('#agent-grid');
    let html = sorted.map(a => _renderCard(a)).join('');

    if (!sorted.length) {
        html = '<div style="padding:20px; text-align:center; color:var(--text-dim);">No active sessions found</div>';
    }

    grid.innerHTML = html;
}

function openSendMsg(agent) {
    msgTargetAgent = agent;
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
    // sensible to show (never produces "Resets in NaN").
    if (resetsAt == null || !isFinite(resetsAt)) return null;
    const msLeft = resetsAt * 1000 - Date.now();
    if (msLeft <= 0) return 'Resets now';
    const totalMin = Math.round(msLeft / 60000);
    if (totalMin < 1) return 'Resets soon';
    const h = Math.floor(totalMin / 60);
    const m = totalMin % 60;
    const span = h >= 1 ? `${h} hr ${m} min` : `${m} min`;
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

async function checkAgentContext(agent) {
    const wrap = document.getElementById(`ctx-${agent}`);
    if (wrap) wrap.querySelector('.context-label').textContent = 'Context: loading...';
    const data = await api(`/api/agents/context?agent=${encodeURIComponent(agent)}`);
    _renderContextData(data);
}

async function compactAgent(agent) {
    if (!confirm(`Compact context for ${agent}? This will summarize conversation history.`)) return;
    await api('/api/agents/msg', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent, message: '/compact' }),
    });
    setTimeout(() => checkAgentContext(agent), 8000);
}

async function clearContext(agent) {
    try {
        const res = await api('/api/agents/msg', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ agent, message: '/clear' }),
        });
        console.log('clearContext response:', res);
    } catch (e) {
        console.error('clearContext error:', e);
    }
    setTimeout(() => checkAgentContext(agent), 5000);
}

async function doRediscover() {
    await api('/api/agents/rediscover', { method: 'POST' });
    refresh();
}

async function checkContext() {
    const data = await api('/api/agents/context');
    _renderContextData(data);
}

async function doBroadcast() {
    const msg = $('#broadcast-input').value.trim();
    if (!msg) return;
    await api('/api/agents/broadcast', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: msg }),
    });
    $('#broadcast-input').value = '';
}

