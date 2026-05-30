// ---------------------------------------------------------------------------
// Sidebar + canvas navigation (replaces the old tab bar)
//
// The canvas is a set of .panel elements (one per view) kept from the tab
// layout, so every existing renderer (refreshAgents -> #agent-grid,
// refreshDispatcher -> #dispatcher-content, ...) works unchanged. `currentTab`
// (declared in util.js) is still the active-view variable, so main.js and
// sse.js keep dispatching on it; only the *chrome* that sets it changed from a
// tab bar to this sidebar.
// ---------------------------------------------------------------------------

let _agentFilter = 'all';   // all | claude | shell | server  (sidebar filter)
let _detailAgent = null;    // window name focused in the agent-detail view

// --- View switching --------------------------------------------------------

function selectView(view) {
    currentTab = view;
    _detailAgent = null;

    $$('.panel').forEach(p => p.classList.remove('active'));
    const panel = document.getElementById('panel-' + view);
    if (panel) panel.classList.add('active');

    _syncSidebarActive(view, null);

    // Per-view poll timers (dispatcher / kanban own their own cadence).
    if (view === 'dispatcher') { refreshDispatcher(); startDispatcherTimer(); }
    else { stopDispatcherTimer(); }
    if (view === 'kanban') { refreshKanban(); startKanbanTimer(); }
    else { stopKanbanTimer(); }
    if (TERMINALS_ON && view === 'terminals') startTermTimer();
    else if (TERMINALS_ON) stopTermTimer();

    refresh();
}

// Focus a single agent in the canvas (detail / transcript).
function selectAgent(name) {
    currentTab = 'agent-detail';
    _detailAgent = name;
    $$('.panel').forEach(p => p.classList.remove('active'));
    const panel = document.getElementById('panel-agent-detail');
    if (panel) panel.classList.add('active');
    _syncSidebarActive('agent-detail', name);
    stopDispatcherTimer();
    stopKanbanTimer();
    if (TERMINALS_ON) stopTermTimer();
    renderAgentDetail();
    refreshSummary();
    checkContext();   // fills the detail context bar via ctx-<name>
}

function _syncSidebarActive(view, agentName) {
    $$('.side-item').forEach(el => el.classList.toggle('active', el.dataset.view === view));
    const root = document.querySelector('.side-section .side-head[onclick]');
    if (root) root.classList.toggle('active', view === 'agents');
    $$('.agent-row').forEach(el => el.classList.toggle('active', el.dataset.agent === agentName));
}

// --- Window type (icon + filter) -------------------------------------------
// window_type is authoritative once the backend provides it; until then fall
// back to claude_running.
function _agentType(a) {
    return a.window_type || (a.claude_running ? 'claude' : 'shell');
}
function _typeIcon(type) {
    if (type === 'claude') return '◆';
    if (type === 'server') return '⊕';
    return '❯';   // shell
}

// --- Sidebar agent list ----------------------------------------------------

function setAgentFilter(f) {
    _agentFilter = f;
    $$('#agent-filter button').forEach(b => b.classList.toggle('active', b.dataset.filter === f));
    renderSidebarAgents(_agentsCache || []);
}

function renderSidebarAgents(agents) {
    const host = document.getElementById('sidebar-agents');
    if (!host) return;
    const rows = (agents || [])
        .filter(a => _agentFilter === 'all' || _agentType(a) === _agentFilter)
        .sort((a, b) => a.name.localeCompare(b.name));
    if (!rows.length) {
        host.innerHTML = '<div class="side-empty">No agents</div>';
        return;
    }
    // onclick reads data-agent (handler is on the row, so `this` is the row no
    // matter which child was clicked) — avoids escaping a window name into the
    // inline handler.
    host.innerHTML = rows.map(a => {
        const dot = a.health || (a.claude_running ? 'green' : 'grey');
        const type = _agentType(a);
        const active = a.name === _detailAgent ? ' active' : '';
        return `<div class="agent-row${active}" data-agent="${attrEsc(a.name)}" onclick="selectAgent(this.dataset.agent)">
            <span class="health-dot ${dot}"></span>
            <span class="type-icon" title="${attrEsc(type)}">${_typeIcon(type)}</span>
            <span class="agent-row-name" title="${attrEsc(a.name)}">${escHtml(a.name)}</span>
        </div>`;
    }).join('');
}

// Single source of the always-visible sidebar agent list. Owns the /api/agents
// fetch that also primes _agentsCache (schedule dropdown, detail view, etc.).
async function refreshSidebar() {
    try {
        const agents = await api('/api/agents');
        _agentsCache = agents || [];
        renderSidebarAgents(_agentsCache);
    } catch (e) {
        // transient — keep the last render; the next tick retries.
    }
}

// WORK badges: in-flight runs + open PRs (awaiting review), across workflows.
async function updateWorkBadges() {
    const runsEl = document.getElementById('side-runs-count');
    const prEl = document.getElementById('side-pr-count');
    if (!runsEl && !prEl) return;
    try {
        const d = await api('/api/dispatcher');
        const wfs = (d && d.workflows) || [];
        let runs = 0, prs = 0;
        for (const wf of wfs) {
            runs += (wf.active_runs || []).length;
            prs += (wf.awaiting_review_runs || []).length;
        }
        if (runsEl) runsEl.textContent = runs;
        if (prEl) prEl.textContent = prs + ' PR';
    } catch (e) { /* leave prior values */ }
}

// --- Agent detail view -----------------------------------------------------

function renderAgentDetail() {
    const host = document.getElementById('agent-detail');
    if (!host) return;
    const a = (_agentsCache || []).find(x => x.name === _detailAgent);
    if (!a) {
        host.innerHTML = `<div class="detail-head">
            <span class="detail-back" onclick="selectView('agents')">← Agents</span>
            <span class="detail-title">${escHtml(_detailAgent || '')}</span>
        </div>
        <div class="side-empty">This agent's window is no longer present.</div>`;
        return;
    }
    const dot = a.health || (a.claude_running ? 'green' : 'grey');
    const type = _agentType(a);

    const actions = [`<button onclick="openSendMsg('${attrEsc(a.name)}')">Message</button>`];
    if (a.has_schedules) actions.push(`<button onclick="triggerSchedule('${attrEsc(a.name)}')">Trigger</button>`);
    if (a.claude_running) {
        actions.push(`<button onclick="restartAgent('${attrEsc(a.name)}')">Restart</button>`);
        actions.push(`<button class="btn-danger" onclick="stopAgent('${attrEsc(a.name)}')">Stop</button>`);
    } else {
        actions.push(`<button class="btn-accent" onclick="startAgent('${attrEsc(a.name)}')">Start</button>`);
    }

    const rows = [
        ['Window', escHtml(a.window_id || '—')],
        ['Type', escHtml(type)],
        ['Claude', a.claude_running ? 'running' : 'stopped'],
        ['Status', escHtml(a.session_status || (a.claude_running ? 'running' : 'offline'))],
        ['Liveness', escHtml(a.liveness || '—')],
        ['CWD', escHtml(a.cwd || '—')],
    ];
    if (a.schedule_next_run) rows.push(['Next run', shortTime(a.schedule_next_run)]);
    if (a.schedule_last_run) rows.push(['Last run', shortTime(a.schedule_last_run)]);

    let pr = '';
    if (a.pr && a.pr.url) {
        const label = a.pr.number ? `PR #${a.pr.number}` : 'PR';
        pr = ` <a class="pr-badge" href="${attrEsc(a.pr.url)}" target="_blank" rel="noopener noreferrer">${escHtml(label)}</a>`;
    }

    let recap = '';
    if (a.recap) {
        const age = a.recap_ts ? ` · ${ageStr((Date.now() - new Date(a.recap_ts)) / 1000)}` : '';
        recap = `<h4 style="margin-top:16px;">Recap${age}</h4><div class="detail-recap">${escHtml(a.recap)}</div>`;
    }

    host.innerHTML = `
        <div class="detail-head">
            <span class="detail-back" onclick="selectView('agents')">← Agents</span>
            <span class="health-dot ${dot}"></span>
            <span class="detail-title">${escHtml(a.name)}</span>${pr}
        </div>
        <div class="canvas-toolbar">${actions.join('')}</div>
        <div class="detail-grid">
            ${rows.map(([k, v]) => `<div class="k">${k}</div><div class="v">${v}</div>`).join('')}
        </div>
        <div class="context-bar-wrap" id="ctx-${attrEsc(a.name)}">
            <div class="context-bar"><div class="context-bar-fill" style="width:0%"></div></div>
            <span class="context-label">Context: --</span>
        </div>
        ${recap}`;
}

// --- Settings drawer -------------------------------------------------------

function toggleSettings(focus) {
    const drawer = document.getElementById('settings-drawer');
    const scrim = document.getElementById('drawer-scrim');
    if (!drawer) return;
    const open = !drawer.classList.contains('open');
    drawer.classList.toggle('open', open);
    if (scrim) scrim.classList.toggle('open', open);
    if (open) renderSettings(focus);
}

function renderSettings(focus) {
    const body = document.getElementById('drawer-body');
    if (!body) return;
    const theme = localStorage.getItem('chela_theme') || 'dark';
    body.innerHTML = `
        <h4>Needs-input notifications</h4>
        <div class="drawer-field">
            <div class="k">Fires a one-shot ping when an agent's pane enters
            <code>waiting</code> (blocked on a prompt or question).</div>
        </div>
        <div class="drawer-field">
            <div class="k">Set on the daemon (env), then restart <code>chela run</code>:</div>
            <div class="v"><code>CHELA_NOTIFY_URL</code> — ntfy / Telegram / webhook (auto-detected)</div>
        </div>
        <div class="drawer-field">
            <div class="v" style="color:var(--text-dim); font-size:11px;">
            ntfy: <code>https://ntfy.sh/your-topic</code><br>
            Telegram: <code>https://api.telegram.org/bot&lt;token&gt;/sendMessage?chat_id=&lt;id&gt;</code><br>
            webhook: any URL (receives JSON <code>{title,message,event}</code>)
            </div>
        </div>

        <h4>Remote access</h4>
        <div class="drawer-field">
            <div class="k">Zero built-in auth — the dashboard binds <code>127.0.0.1</code>.
            Put it behind a tailnet or SSH tunnel; that is the trust boundary.</div>
        </div>
        <div class="drawer-field">
            <div class="v"><code>tailscale serve 5001</code> &nbsp;·&nbsp; or
            <code>ssh -L 5001:127.0.0.1:5001 host</code></div>
        </div>
        <div class="drawer-field">
            <div class="k">Phone: SSH/Mosh in (Blink / Termius), then
            <code>tmux attach</code> for the live panes.</div>
        </div>

        <h4>Theme</h4>
        <div class="drawer-field">
            <select id="theme-select" onchange="setTheme(this.value)">
                <option value="dark"${theme === 'dark' ? ' selected' : ''}>Dark</option>
                <option value="dim"${theme === 'dim' ? ' selected' : ''}>Dim</option>
            </select>
        </div>

        <h4>Terminal wall</h4>
        <div class="drawer-field">
            <div class="k">The embedded ttyd wall streams live and is ${TERMINALS_ON ? 'enabled' : 'off by default'}.
            Toggle with <code>CHELA_TERMINALS_ENABLED</code>.</div>
        </div>`;
    if (focus === 'notify') body.scrollTop = 0;
}

function setTheme(t) {
    localStorage.setItem('chela_theme', t);
    document.body.dataset.theme = t;
}

// --- "+ new" popover -------------------------------------------------------

function openNewMenu(ev) {
    if (ev) ev.stopPropagation();
    const m = document.getElementById('new-menu');
    if (!m) return;
    const anchor = (ev && ev.currentTarget) || document.getElementById('btn-new');
    const r = anchor.getBoundingClientRect();
    m.style.top = (r.bottom + 4) + 'px';
    m.style.left = Math.max(8, r.right - 160) + 'px';
    m.style.display = 'block';
    setTimeout(() => document.addEventListener('click', hideNewMenu, { once: true }), 0);
}

function hideNewMenu() {
    const m = document.getElementById('new-menu');
    if (m) m.style.display = 'none';
}

// Spawn a plain shell window. The backend spawn endpoint is currently behind
// the terminals feature flag (it was the wall's spawner); until a non-gated
// endpoint lands this surfaces the API's response rather than failing silently.
async function newShellWindow() {
    try {
        const res = await api('/api/agents/spawn', { method: 'POST' });
        if (res && res.ok) { _agentsCache = []; refreshSidebar(); }
        else alert((res && res.error) || 'Could not spawn a window (enable terminals, or use tmux directly).');
    } catch (e) {
        alert('Could not spawn a window (enable terminals, or use tmux directly).');
    }
}

// Apply the saved theme immediately on load.
document.body.dataset.theme = localStorage.getItem('chela_theme') || 'dark';
