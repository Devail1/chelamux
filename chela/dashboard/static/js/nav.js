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

// --- Mobile sidebar drawer -------------------------------------------------
// On phone widths the 264px sidebar is off-canvas (see the @media block in
// style.css); a hamburger in the topbar slides it in over a scrim. No-op on
// desktop, where the sidebar is a static grid column and `.open` does nothing.
function toggleSidebar(force) {
    const sb = document.querySelector('.sidebar');
    const scrim = document.getElementById('sidebar-scrim');
    if (!sb) return;
    const open = (force === undefined) ? !sb.classList.contains('open') : !!force;
    sb.classList.toggle('open', open);
    if (scrim) scrim.classList.toggle('open', open);
}
function closeSidebar() { toggleSidebar(false); }

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
    // Entering Knowledge from the nav lands on the glance overview, not whatever
    // concept was last open.
    if (view === 'knowledge' && typeof knBackToGlance === 'function' && _kn.tree) knBackToGlance();

    closeSidebar();   // navigating dismisses the mobile drawer (no-op on desktop)
    refresh();
}

// Clicking an agent (sidebar row or command palette) lands you ON its live
// pane — switch to the wall, restore/scroll/flash it (focusPaneByWid). The
// metadata "detail" card is the fallback only when there's no wall to land on
// (terminals off) or the window isn't resolved yet.
function selectAgent(name) {
    if (TERMINALS_ON && typeof focusPaneByWid === 'function') {
        const a = (_agentsCache || []).find(x => x.name === name);
        if (a && a.window_id) { focusPaneByWid(a.window_id); return; }
    }
    showAgentDetail(name);
}

// Focus a single agent in the canvas (metadata detail / transcript).
function showAgentDetail(name) {
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
    closeSidebar();   // navigating dismisses the mobile drawer (no-op on desktop)
}

function _syncSidebarActive(view, agentName) {
    // The agent-detail view has no nav item of its own; keep the Agents nav item
    // lit while drilled into a single agent so the sidebar still shows where you
    // are.
    const navView = view === 'agent-detail' ? 'agents' : view;
    $$('.side-item').forEach(el => el.classList.toggle('active', el.dataset.view === navView));
    $$('.agent-row').forEach(el => el.classList.toggle('active', el.dataset.agent === agentName));
}

// --- Window type (icon + filter) -------------------------------------------
// window_type is authoritative once the backend provides it; until then fall
// back to claude_running.
function _agentType(a) {
    return a.window_type || (a.claude_running ? 'claude' : 'shell');
}
// --- Sidebar agent list ----------------------------------------------------

function setAgentFilter(f) {
    _agentFilter = f;
    $$('#agent-filter button').forEach(b => b.classList.toggle('active', b.dataset.filter === f));
    renderSidebarAgents(_agentsCache || []);
}

// Per-window context cache (used_pct etc.), fed by both the sidebar refresh and
// the wall tick so rows can show ctx% even when the wall isn't open.
let _ctxByWid = {};
function updateCtxCache(ctx) {
    if (!Array.isArray(ctx)) return;
    const m = {};
    ctx.forEach(c => { if (c && c.window_id) m[c.window_id] = c; });
    _ctxByWid = m;
}

// Project key for grouping = basename of the session's cwd. Shells / sessions
// with no resolved cwd collect under one "other" group.
function _agentProject(a) {
    if (!a || !a.cwd) return null;
    const parts = String(a.cwd).replace(/\/+$/, '').split('/');
    return parts[parts.length - 1] || null;
}

// Friendly label, shared with the wall panes (_displayLabel): a custom rename
// wins, else a generic shell-N is relabelled to its repo, else the raw name —
// so a session reads identically in the sidebar and on its pane title. Falls
// back to the raw name when terminals.js isn't loaded.
function _agentLabel(a) {
    if (typeof _displayLabel === 'function' && a && a.window_id) return _displayLabel(a.window_id);
    return a ? a.name : '';
}

// Collapsed-group state persists in localStorage — the list rebuilds on every
// tick, so DOM-only state would be lost.
function _collapsedGroups() {
    try { return new Set(JSON.parse(localStorage.getItem('chela_grp_collapsed') || '[]')); }
    catch { return new Set(); }
}
function toggleGroup(name) {
    const s = _collapsedGroups();
    if (s.has(name)) s.delete(name); else s.add(name);
    localStorage.setItem('chela_grp_collapsed', JSON.stringify([...s]));
    renderSidebarAgents(_agentsCache || []);
}

// One richer agent row: status dot + name + ctx% chip, plus a sub-line with the
// live state word, age of the last update, and a recap snippet — so you can read
// what a session is doing without opening it. onclick reads data-agent (handler
// is on the row, so `this` is the row no matter which child was clicked).
function _agentRowHtml(a) {
    const dot = agentDotColor(a);
    const active = a.name === _detailAgent ? ' active' : '';
    const stWord = _AGENT_STATUS_WORD[dot] || 'idle';
    const stCls = _SIDEBAR_DOT_CLASS[dot] || 'idle';
    const label = _agentLabel(a);

    const c = a.window_id ? _ctxByWid[a.window_id] : null;
    let ctxChip = '';
    if (c && c.used_pct != null) {
        const p = Math.round(c.used_pct);
        const cls = p > 80 ? 'danger' : p > 60 ? 'warn' : '';
        ctxChip = `<span class="ar-ctx ${cls}" title="context ${p}%">${p}%</span>`;
    }

    let age = '';
    if (a.recap_ts) age = ageStr((Date.now() - new Date(a.recap_ts)) / 1000).replace(' ago', '');
    const recap = a.recap ? `<span class="ar-recap" title="${attrEsc(a.recap)}">${escHtml(a.recap)}</span>` : '';

    const sub = `<span class="ar-state ${stCls}">${stWord}</span>`
        + (age ? `<span class="ar-age">· ${age}</span>` : '')
        + (recap ? ` ${recap}` : '');

    const canPin = TERMINALS_ON && a.cwd;
    const faved = canPin && typeof _isFav === 'function' && _isFav(a.cwd);
    const pin = canPin
        ? `<button class="agent-pin${faved ? ' pinned' : ''}" data-cwd="${attrEsc(a.cwd)}"
             title="${faved ? 'Unpin from Launch favorites' : 'Pin this directory to Launch favorites'}"
             onclick="event.stopPropagation(); toggleFavCwd(this.dataset.cwd)">${faved ? '&#9733;' : '&#9734;'}</button>`
        : '';

    return `<div class="agent-row rich${active}" data-agent="${attrEsc(a.name)}" onclick="selectAgent(this.dataset.agent)">
        <span class="term-status-dot ${stCls}" title="${attrEsc(_agentType(a))} · ${stWord}"></span>
        <div class="ar-main">
            <div class="ar-top">
                <span class="agent-row-name" title="${attrEsc(label)}">${escHtml(label)}</span>
                ${ctxChip}
            </div>
            <div class="ar-sub">${sub}</div>
        </div>
        ${pin}
    </div>`;
}

function renderSidebarAgents(agents) {
    // Keep the tab title/favicon in lockstep with the agent list, off the full
    // set (not the type-filtered rows) so the "needs you" count is global.
    updateTabSignal(agents);
    const host = document.getElementById('sidebar-agents');
    if (!host) return;
    const rows = (agents || [])
        .filter(a => _agentFilter === 'all' || _agentType(a) === _agentFilter);
    if (!rows.length) {
        host.innerHTML = '<div class="side-empty">No agents</div>';
        return;
    }

    // Triage: agents waiting on input float into a "Needs you" cluster above the
    // project groups. Each agent shows in exactly one place — lifted out of its
    // group while it's blocked, like a starred item.
    const waiting = rows.filter(a => a.session_status === 'waiting')
        .sort((a, b) => a.name.localeCompare(b.name));
    const rest = rows.filter(a => a.session_status !== 'waiting');

    let html = '';
    if (waiting.length) {
        html += `<div class="side-triage">
            <div class="triage-head">Needs you <span class="triage-count">${waiting.length}</span></div>
            ${waiting.map(_agentRowHtml).join('')}
        </div>`;
    }

    // Partition the rest by project (cwd basename). A project earns a collapsible
    // group header only when 2+ sessions share it; lone sessions render as plain
    // rows (their label is already the repo name, so a header would just repeat
    // it). Entries — single rows and groups alike — interleave alphabetically by
    // project so the order is stable as sessions come and go.
    const byProj = {};
    rest.forEach(a => { const k = _agentProject(a) || '~other'; (byProj[k] = byProj[k] || []).push(a); });
    const collapsed = _collapsedGroups();

    const entries = Object.keys(byProj).map(k => {
        const list = byProj[k].sort((a, b) => _agentLabel(a).localeCompare(_agentLabel(b)));
        // ~other holds cwd-less shells; never collapse them into a "other" group —
        // render each as its own row.
        const grouped = list.length >= 2 && k !== '~other';
        return { key: k, list, grouped, sortKey: grouped ? k : _agentLabel(list[0]) };
    });
    // Explode the ~other bucket into individual single-row entries.
    const flatEntries = [];
    for (const e of entries) {
        if (e.key === '~other') e.list.forEach(a => flatEntries.push({ list: [a], grouped: false, sortKey: _agentLabel(a) }));
        else flatEntries.push(e);
    }
    flatEntries.sort((a, b) => a.sortKey.localeCompare(b.sortKey));

    for (const e of flatEntries) {
        if (!e.grouped) { html += e.list.map(_agentRowHtml).join(''); continue; }
        const isColl = collapsed.has(e.key);
        const working = e.list.filter(a => agentDotColor(a) === 'green').length;
        html += `<div class="side-group${isColl ? ' collapsed' : ''}">
            <div class="group-head" data-g="${attrEsc(e.key)}" onclick="toggleGroup(this.dataset.g)">
                <span class="group-caret">▾</span>
                <span class="group-name" title="${attrEsc(e.key)}">${escHtml(e.key)}</span>
                ${working ? `<span class="group-dot working" title="${working} working"></span>` : ''}
                <span class="group-count">${e.list.length}</span>
            </div>
            <div class="group-rows">${e.list.map(_agentRowHtml).join('')}</div>
        </div>`;
    }

    host.innerHTML = html;
}

// Status colour → human word, for the dot's tooltip.
const _AGENT_STATUS_WORD = { green: 'working', yellow: 'waiting', grey: 'idle' };
// Status colour → the pane dot's CSS state class, so the sidebar dot pulses
// identically to the wall's .term-status-dot (working/waiting/idle).
const _SIDEBAR_DOT_CLASS = { green: 'working', yellow: 'waiting', grey: 'idle' };

// Single source of the always-visible sidebar agent list. Owns the /api/agents
// fetch that also primes _agentsCache (schedule dropdown, detail view, etc.).
async function refreshSidebar() {
    try {
        // Fetch context alongside agents so rows can show ctx% even when the wall
        // (which owns the 4s context poll) isn't the active view. Best-effort:
        // a context failure must not blank the agent list.
        const [agents, ctx] = await Promise.all([
            api('/api/agents'),
            TERMINALS_ON ? api('/api/agents/context').catch(() => null) : Promise.resolve(null),
        ]);
        _agentsCache = agents || [];
        if (ctx) updateCtxCache(ctx);
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
    const dot = agentDotColor(a);
    const type = _agentType(a);

    // "Message" only when there's no wall to type into directly (terminals off).
    const actions = [];
    if (!(typeof TERMINALS_ON !== 'undefined' && TERMINALS_ON)) {
        actions.push(`<button onclick="openSendMsg('${attrEsc(a.name)}')">Message</button>`);
    }
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
    const termLatin = localStorage.getItem('chela_term_latin') || 'jetbrains';
    const termFont = localStorage.getItem('chela_term_font') || 'miriam';
    const termSize = localStorage.getItem('chela_term_fontsize') || '14';
    body.innerHTML = `
        <section class="settings-section">
            <h4>Projects folder</h4>
            <p class="s-desc">Scanned for git repos to suggest in the <strong>Launch</strong>
            sidebar. Defaults to <code>~/projects</code> (or the <code>CHELA_PROJECTS_DIR</code>
            env var). Takes effect immediately — no restart.</p>
            <div class="s-row">
                <input id="cfg-projects-dir" class="s-input" type="text"
                       placeholder="~/projects" autocomplete="off"
                       onkeydown="if(event.key==='Enter')saveProjectsDir()">
                <button class="btn-accent" onclick="saveProjectsDir()">Save</button>
            </div>
            <div id="cfg-projects-msg" class="s-savemsg"></div>
        </section>

        <section class="settings-section">
            <h4>Needs-input notifications</h4>
            <p class="s-desc">Fires a one-shot ping when an agent's pane enters
            <code>waiting</code> (blocked on a prompt or question).</p>
            <p class="s-desc">Set on the daemon (env), then restart <code>chela run</code>:</p>
            <div class="s-kv"><code>CHELA_NOTIFY_URL</code><span>ntfy / Telegram / webhook (auto-detected)</span></div>
            <div class="s-examples">
                <div class="s-ex"><span class="s-tag">ntfy</span><code>https://ntfy.sh/your-topic</code></div>
                <div class="s-ex"><span class="s-tag">Telegram</span><code>https://api.telegram.org/bot&lt;token&gt;/sendMessage?chat_id=&lt;id&gt;</code></div>
                <div class="s-ex"><span class="s-tag">webhook</span><span class="s-exnote">any URL — receives JSON <code>{title,message,event}</code></span></div>
            </div>
        </section>

        <section class="settings-section">
            <h4>Remote access</h4>
            <p class="s-desc">Zero built-in auth — the dashboard binds <code>127.0.0.1</code>.
            Put it behind a tailnet or SSH tunnel; that is the trust boundary.</p>
            <div class="s-examples">
                <div class="s-ex"><span class="s-tag">tailnet</span><code>tailscale serve 5001</code></div>
                <div class="s-ex"><span class="s-tag">tunnel</span><code>ssh -L 5001:127.0.0.1:5001 host</code></div>
            </div>
            <p class="s-desc">Phone: SSH/Mosh in (Blink / Termius), then
            <code>tmux attach</code> for the live panes.</p>
        </section>

        <section class="settings-section">
            <h4>Theme</h4>
            <div class="s-row">
                <span class="s-rowlabel">Appearance</span>
                <select id="theme-select" class="s-select" onchange="setTheme(this.value)">
                    ${['dark','dim','midnight','nord','gruvbox','solarized','rose']
                        .map(t => `<option value="${t}"${theme === t ? ' selected' : ''}>${THEME_LABELS[t]}</option>`)
                        .join('')}
                </select>
            </div>
        </section>

        <section class="settings-section">
            <h4>Terminal font</h4>
            <p class="s-desc">Applies live to every open terminal, saved per browser.
            Pick the <strong>English</strong> (monospace) and <strong>Hebrew</strong> faces
            independently. Only <strong>Miriam Mono</strong> keeps Hebrew on the grid — the
            other Hebrew faces are proportional: nicer letters, slight drift in the fixed cells.</p>
            <div class="s-row">
                <span class="s-rowlabel">English font</span>
                <select id="term-latin-select" class="s-select" onchange="setTermLatin(this.value)">
                    ${Object.keys(TERM_LATIN_LABELS)
                        .map(k => `<option value="${k}"${termLatin === k ? ' selected' : ''}>${TERM_LATIN_LABELS[k]}</option>`)
                        .join('')}
                </select>
            </div>
            <div class="s-row">
                <span class="s-rowlabel">Hebrew font</span>
                <select id="term-font-select" class="s-select" onchange="setTermFont(this.value)">
                    ${Object.keys(TERM_FONT_LABELS)
                        .map(k => `<option value="${k}"${termFont === k ? ' selected' : ''}>${TERM_FONT_LABELS[k]}</option>`)
                        .join('')}
                </select>
            </div>
            <div class="s-row">
                <span class="s-rowlabel">Size</span>
                <select id="term-size-select" class="s-select" onchange="setTermSize(this.value)">
                    ${['12', '13', '14', '15', '16', '18']
                        .map(s => `<option value="${s}"${termSize === s ? ' selected' : ''}>${s}px</option>`)
                        .join('')}
                </select>
            </div>
        </section>

        <section class="settings-section">
            <h4>Terminal wall</h4>
            <div class="s-row">
                <span class="s-rowlabel">Embedded ttyd wall</span>
                <span class="s-badge ${TERMINALS_ON ? 'on' : 'off'}">${TERMINALS_ON ? 'Enabled' : 'Off'}</span>
            </div>
            <p class="s-desc">Streams live when on. Toggle with <code>CHELA_TERMINALS_ENABLED</code>.</p>
        </section>`;
    if (focus === 'notify') body.scrollTop = 0;
    _loadProjectsSetting();
}

// Fill the projects-dir input from /api/config: the stored value goes in the
// field, the effective (env/default-resolved) dir becomes the placeholder so an
// unset field still shows what's actually scanned.
async function _loadProjectsSetting() {
    const inp = document.getElementById('cfg-projects-dir');
    if (!inp) return;
    try {
        const cfg = await api('/api/config');
        if (!cfg) return;
        inp.value = cfg.projects_dir || '';
        if (cfg.projects_dir_effective) inp.placeholder = cfg.projects_dir_effective;
    } catch (e) { /* keep the default placeholder */ }
}

async function saveProjectsDir() {
    const inp = document.getElementById('cfg-projects-dir');
    const msg = document.getElementById('cfg-projects-msg');
    if (!inp) return;
    const setMsg = (cls, t) => { if (msg) { msg.className = 's-savemsg ' + cls; msg.textContent = t; } };
    setMsg('', 'Saving…');
    let cfg;
    try {
        cfg = await api('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ projects_dir: inp.value.trim() }),
        });
    } catch (e) { setMsg('err', 'Save failed.'); return; }
    if (cfg && cfg.projects_dir_effective) inp.placeholder = cfg.projects_dir_effective;
    setMsg('ok', 'Saved · scanning ' + ((cfg && cfg.projects_dir_effective) || inp.value.trim()));
    // Refresh the Launch sidebar so new suggestions appear right away.
    if (typeof refreshLauncher === 'function') refreshLauncher();
}

const THEME_LABELS = {
    dark: 'Dark', dim: 'Dim', midnight: 'Midnight', nord: 'Nord',
    gruvbox: 'Gruvbox', solarized: 'Solarized', rose: 'Rosé Pine',
};

function setTheme(t) {
    localStorage.setItem('chela_theme', t);
    document.body.dataset.theme = t;
}

// Terminal font options. Keys are stored in localStorage and mapped to real
// family names by the shim injected into each ttyd page (app.py
// _TERM_FONT_PREF_SHIM) — keep the keys here in sync with LAT/HEB there.
// English (Latin) faces are all monospace; the Hebrew list has one monospace
// (Miriam) and the rest proportional (trade grid alignment for nicer letters).
const TERM_LATIN_LABELS = {
    jetbrains: 'JetBrains Mono',
    firacode: 'Fira Code · ligatures',
    plex: 'IBM Plex Mono',
    source: 'Source Code Pro',
    cascadia: 'Cascadia Code · ligatures',
};

const TERM_FONT_LABELS = {
    miriam: 'Miriam Mono · aligned',
    noto: 'Noto Sans Hebrew · modern',
    heebo: 'Heebo · rounded',
    assistant: 'Assistant · humanist',
    rubik: 'Rubik · rounded',
    frankruhl: 'Frank Ruhl · serif',
    david: 'David Libre · classic',
};

// Terminal font + size are per-viewer prefs (like the theme), stored in
// localStorage and applied live to every ttyd iframe. The iframes are
// same-origin, so writing localStorage fires a `storage` event inside each of
// them (the shim listens); we ALSO call into each iframe directly for instant
// feedback in the frame that made the change.
function setTermLatin(v) {
    localStorage.setItem('chela_term_latin', v);
    applyTermPrefsToIframes();
}

function setTermFont(v) {
    localStorage.setItem('chela_term_font', v);
    applyTermPrefsToIframes();
}

function setTermSize(v) {
    localStorage.setItem('chela_term_fontsize', v);
    applyTermPrefsToIframes();
}

function applyTermPrefsToIframes() {
    document.querySelectorAll('iframe').forEach(f => {
        try {
            const w = f.contentWindow;
            if (w && typeof w.chelaApplyTermPrefs === 'function') w.chelaApplyTermPrefs();
        } catch (e) { /* not-yet-loaded — the storage event covers it */ }
    });
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

// Touch-friendly tooltips. Native `title` only surfaces on hover, so on a
// phone the rate-limit pills (and any other titled pill) have no tooltip at
// all. Tapping a titled element pops a floating bubble; tapping elsewhere or
// after a few seconds dismisses it. Desktop hover still uses the native title.
(function () {
    let tipEl = null, hideTimer = null;

    function hideTip() {
        if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
        if (tipEl) { tipEl.remove(); tipEl = null; }
    }

    function showTip(target, text) {
        hideTip();
        tipEl = document.createElement('div');
        tipEl.className = 'tap-tip';
        tipEl.textContent = text;
        document.body.appendChild(tipEl);
        const r = target.getBoundingClientRect();
        // Centre under the target, clamped to the viewport.
        const tw = tipEl.offsetWidth;
        let left = r.left + r.width / 2 - tw / 2;
        left = Math.max(6, Math.min(left, window.innerWidth - tw - 6));
        tipEl.style.left = left + 'px';
        tipEl.style.top = (r.bottom + 6) + 'px';
        hideTimer = setTimeout(hideTip, 4000);
    }

    // Only react to touch — desktop keeps the native hover tooltip. CRITICAL:
    // never touch interactive controls. preventDefault() on touchend suppresses
    // the follow-up click, so hijacking a tap on a button/link/onclick row (the
    // hamburger, bell, gear, sidebar rows…) would silently kill its action. Bail
    // on anything actionable and let the tap through; only non-interactive titled
    // elements (e.g. the rate-limit pills) get the tap-tooltip.
    document.addEventListener('touchend', function (e) {
        if (e.target.closest('button, a, input, select, textarea, label, [onclick], [role="button"]')) {
            hideTip();
            return;
        }
        const el = e.target.closest('[title], [data-tip]');
        if (!el) { hideTip(); return; }
        const text = el.getAttribute('data-tip') || el.getAttribute('title');
        if (!text) { hideTip(); return; }
        e.preventDefault();
        showTip(el, text);
    }, { passive: false });

    window.addEventListener('scroll', hideTip, true);
})();

// --- Command palette (⌘K / Ctrl-K) ----------------------------------------
// One fuzzy jump-to for everything: agents (→ detail), views, project launches,
// and a couple of global actions. The fastest way to navigate once you're past a
// handful of sessions. Built fresh each open from live caches.
let _palItems = [], _palSel = 0;

function _paletteItems() {
    const items = [];
    const views = [];
    if (TERMINALS_ON) views.push(['terminals', 'Wall']);
    views.push(['agents', 'Agents'], ['dispatcher', 'Dispatch'], ['kanban', 'Kanban'], ['schedules', 'Schedules'], ['knowledge', 'Knowledge']);
    views.forEach(([v, label]) => items.push({ icon: '▦', title: label, sub: 'view', run: () => selectView(v) }));

    (_agentsCache || []).forEach(a => {
        const word = _AGENT_STATUS_WORD[agentDotColor(a)] || 'idle';
        items.push({ dot: _SIDEBAR_DOT_CLASS[agentDotColor(a)] || 'idle', title: _agentLabel(a),
                     sub: 'session · ' + word, run: () => selectAgent(a.name) });
    });

    if (TERMINALS_ON && typeof _launcherData !== 'undefined' && _launcherData) {
        const seen = new Set();
        [...(_launcherData.favorites || []), ...(_launcherData.recent || [])].forEach(e => {
            if (!e || seen.has(e.path)) return;
            seen.add(e.path);
            items.push({ icon: '▸', title: 'Launch ' + (e.label || e.path), sub: 'project',
                         run: () => launchProject(e.path) });
        });
    }

    items.push({ icon: '+', title: 'New shell window', sub: 'action', run: () => newShellWindow() });
    items.push({ icon: '◷', title: 'Add scheduled task', sub: 'action',
                 run: () => { if (typeof showAddSchedule === 'function') showAddSchedule(); } });
    return items;
}

// Subsequence fuzzy score with a word-boundary bonus; -1 = no match.
function _fuzzyScore(q, s) {
    q = q.toLowerCase(); s = s.toLowerCase();
    if (!q) return 0;
    let si = 0, score = 0, run = 0, first = -1;
    for (const c of q) {
        let found = -1;
        for (; si < s.length; si++) { if (s[si] === c) { found = si; break; } }
        if (found < 0) return -1;
        if (first < 0) first = found;
        run = (found === 0 || s[found - 1] === ' ') ? run + 2 : 1;
        score += run; si = found + 1;
    }
    return score - first * 0.1;
}

function _renderPalette(q) {
    const list = document.getElementById('palette-list');
    if (!list) return;
    let items = _paletteItems();
    if (q) {
        items = items
            .map(it => ({ it, sc: _fuzzyScore(q, it.title + ' ' + it.sub) }))
            .filter(x => x.sc >= 0)
            .sort((a, b) => b.sc - a.sc)
            .map(x => x.it);
    }
    _palItems = items;
    if (_palSel >= items.length) _palSel = 0;
    list.innerHTML = items.map((it, i) => {
        const icon = it.dot
            ? `<span class="term-status-dot ${it.dot}"></span>`
            : `<span class="pi-glyph">${it.icon || ''}</span>`;
        return `<div class="palette-item${i === _palSel ? ' sel' : ''}" data-i="${i}"
                  onmouseenter="_palHover(${i})" onclick="_palRun(${i})">
            <span class="pi-icon">${icon}</span>
            <span class="pi-title">${escHtml(it.title)}</span>
            <span class="pi-sub">${escHtml(it.sub)}</span>
        </div>`;
    }).join('') || '<div class="palette-empty">No matches</div>';
}

function openPalette() {
    const ov = document.getElementById('palette');
    if (!ov) return;
    ov.classList.add('open');
    _palSel = 0;
    const inp = document.getElementById('palette-input');
    if (inp) inp.value = '';
    _renderPalette('');
    setTimeout(() => inp && inp.focus(), 0);
}
function closePalette() { const ov = document.getElementById('palette'); if (ov) ov.classList.remove('open'); }
function _palHover(i) { _palSel = i; _palPaint(); }
function _palPaint() {
    document.querySelectorAll('#palette-list .palette-item').forEach((el, i) => el.classList.toggle('sel', i === _palSel));
}
function _palRun(i) { const it = _palItems[i]; if (!it) return; closePalette(); try { it.run(); } catch (e) { /* no-op */ } }
function _palMove(d) {
    if (!_palItems.length) return;
    _palSel = (_palSel + d + _palItems.length) % _palItems.length;
    _palPaint();
    const el = document.querySelector('#palette-list .palette-item.sel');
    if (el) el.scrollIntoView({ block: 'nearest' });
}

document.addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        const ov = document.getElementById('palette');
        (ov && ov.classList.contains('open')) ? closePalette() : openPalette();
        return;
    }
    const ov = document.getElementById('palette');
    if (!ov || !ov.classList.contains('open')) return;
    if (e.key === 'Escape') { e.preventDefault(); closePalette(); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); _palMove(1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); _palMove(-1); }
    else if (e.key === 'Enter') { e.preventDefault(); _palRun(_palSel); }
});

// Apply the saved theme immediately on load.
document.body.dataset.theme = localStorage.getItem('chela_theme') || 'dark';
