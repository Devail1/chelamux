// --- Stage 0: ES-module imports ---
import { $, $$, TERMINALS_ON, _agentProject, _agentsCache, ageStr, agentDotColor, api, attrEsc, currentTab, escHtml, lucideIcon, setAgentsCache, setCurrentTab, shortTime, updateTabSignal, wantsHuman } from './util.js';
import { refreshSummary } from './header.js';
import { checkContext } from './agents.js';
import { showAddSchedule } from './schedules.js';
import { _displayLabel, _sharedWids, _stopShare, focusPaneByWid, shareBtnClick } from './terminals.js';
import { _isFav, _launcherData, launchProject, refreshLauncher } from './launcher.js';
import { VIEWS } from './views.js';
import { findView, navViews, otherViews, paletteViews, panelId } from './viewreg.js';
import { refresh } from './main.js';

// ---------------------------------------------------------------------------
// Sidebar + canvas navigation (replaces the old tab bar)
//
// The canvas is a set of .panel elements (one per view) kept from the tab
// layout, so every existing renderer (refreshAgents -> #agent-grid,
// renderKanban -> #kanban-board, ...) works unchanged. `currentTab` (declared in
// util.js) is still the active-view variable, so main.js and sse.js keep
// dispatching on it; only the *chrome* that sets it changed from a tab bar to
// this sidebar.
//
// The set of views is NOT declared here. It is views.js — the registry — and this
// file reads it: renderNav() builds the .side-item rows from it, and selectView
// takes each view's enter/exit hooks from it instead of the per-view if/else
// chain that used to live below. Same for the command palette (which carried a
// third hardcoded copy of the view list).
// ---------------------------------------------------------------------------

let _detailAgent = null;    // window name focused in the agent-detail view

// --- Sidebar: one control, two behaviours ----------------------------------
// PHONE (≤768px): the 264px sidebar is off-canvas (see the @media block in
// style.css); the topbar hamburger slides it in over a scrim.
// DESKTOP: the sidebar is a static grid column, so there is nothing to slide —
// the SAME control collapses it to an icon rail instead, handing the width to the
// canvas. The state is persisted (a collapse that forgets itself on reload is an
// annoyance, not a feature); the rail is pure CSS off a body class, so no row is
// re-rendered and nothing the wall caches on can see it.
const SIDEBAR_COLLAPSED_KEY = 'chela_sidebar_collapsed';

function _isPhoneWidth() { return window.matchMedia('(max-width: 768px)').matches; }

function _setSidebarCollapsed(collapsed) {
    document.body.classList.toggle('sidebar-collapsed', collapsed);
    localStorage.setItem(SIDEBAR_COLLAPSED_KEY, collapsed ? '1' : '0');
    const btn = document.getElementById('btn-menu');
    if (btn) btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    // The canvas just changed width without a window RESIZE, so the listeners that
    // re-fit the terminal wall never fired — poke them. The rail snaps (there is no
    // width transition to wait out); the delay is only to let the grid settle after
    // the reflow, and the wall debounces the event anyway. This is a RE-FIT, not a
    // rebuild: buildWall's cache key (_termSig) is the live wid set — sidebar state
    // never enters it — so the iframes stay put and no terminal reloads. That
    // property is held by a real-DOM test (tests/wall.test.mjs), not by this comment.
    setTimeout(() => window.dispatchEvent(new Event('resize')), 220);
}

// force: true = "show the sidebar" (drawer open / rail expanded), false = hide it.
function toggleSidebar(force) {
    if (!_isPhoneWidth()) {
        const collapsed = (force === undefined)
            ? !document.body.classList.contains('sidebar-collapsed')
            : !force;
        _setSidebarCollapsed(collapsed);
        return;
    }
    const sb = document.querySelector('.sidebar');
    const scrim = document.getElementById('sidebar-scrim');
    if (!sb) return;
    const open = (force === undefined) ? !sb.classList.contains('open') : !!force;
    sb.classList.toggle('open', open);
    if (scrim) scrim.classList.toggle('open', open);
}

// Navigating dismisses the mobile drawer. It must NOT collapse the desktop rail —
// selectView() calls this on every click, and a sidebar that folds itself away
// whenever you use it is not a sidebar.
function closeSidebar() { if (_isPhoneWidth()) toggleSidebar(false); }

// Restore the persisted desktop state before first paint (mobile CSS ignores the
// class, so a phone that inherits it from a desktop session is unaffected).
if (localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === '1') {
    document.body.classList.add('sidebar-collapsed');
    const _menuBtn = document.getElementById('btn-menu');
    if (_menuBtn) _menuBtn.setAttribute('aria-expanded', 'false');
}

// --- The nav, rendered from the registry ------------------------------------
// One .side-item per registered, enabled, non-virtual view — including its badge
// slots. Adding a view to views.js puts it here; deleting it from views.js takes
// it out of here. There is no second list.

function _viewCtx() { return { terminalsOn: TERMINALS_ON }; }

function _navItemHtml(v) {
    const badges = (v.badges || []).map(b =>
        `<span class="badge ${b.cls || ''}" id="${attrEsc(b.id)}" title="${attrEsc(b.title || '')}">${escHtml(b.text || '')}</span>`
    ).join('');
    // The title is the label — it is the only thing left to read once the sidebar
    // is collapsed to its icon rail.
    return `<div class="side-item" data-view="${attrEsc(v.id)}" title="${attrEsc(v.label || v.id)}"
        onclick="chela.selectView(this.dataset.view)">
        <span class="side-item-icon">${v.lucide ? lucideIcon(v.lucide) : escHtml(v.icon || '')}</span>
        <span class="side-item-label">${escHtml(v.label || v.id)}</span>
        ${badges ? `<span class="side-badges">${badges}</span>` : ''}
    </div>`;
}

function renderNav() {
    const host = document.getElementById('side-nav');
    if (!host) return;
    host.innerHTML = navViews(VIEWS, _viewCtx()).map(_navItemHtml).join('');
}

// --- View switching --------------------------------------------------------

function selectView(view) {
    const v = findView(VIEWS, view);
    setCurrentTab(view);
    _detailAgent = null;

    $$('.panel').forEach(p => p.classList.remove('active'));
    const panel = document.getElementById(panelId(view));
    if (panel) panel.classList.add('active');

    _syncSidebarActive(view, null);

    // Per-view lifecycle, from the registry. Every OTHER view is told to let go
    // (stop its timer) and the one being entered gets its enter hook — so a new
    // view is one registry entry, not an extra branch in an if/else chain here.
    otherViews(VIEWS, view).forEach(o => { if (o.exit) o.exit(); });
    if (v && v.enter) v.enter();

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
    setCurrentTab('agent-detail');
    _detailAgent = name;
    $$('.panel').forEach(p => p.classList.remove('active'));
    const panel = document.getElementById(panelId('agent-detail'));
    if (panel) panel.classList.add('active');
    _syncSidebarActive('agent-detail', name);
    // Drilling in is leaving every other view — same registry-driven teardown as
    // selectView (agent-detail is a registered, virtual view).
    otherViews(VIEWS, 'agent-detail').forEach(o => { if (o.exit) o.exit(); });
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

// --- Window type (per-row cue) ---------------------------------------------
// window_type is authoritative once the backend provides it; until then fall
// back to claude_running.
//
// The type used to be a 4-chip filter row above the list. It isn't one any more:
// a live fleet is a handful of windows that always fit the viewport, so filtering
// hid nothing and cost a permanent row (and ⌘K is the real jump-to). The type
// survives as a CUE on the row itself.
//
// That cue is a GLYPH first — C / $ / ⚙ — with colour only reinforcing it, from
// the Okabe-Ito colourblind-safe palette. Three coloured dots would encode the
// type in hue alone, which is unreadable for a red-weak (deuteranomalous) viewer
// and invisible in greyscale. Read the row with the colour taken away and it
// still says which kind of window this is.
function _agentType(a) {
    return a.window_type || (a.claude_running ? 'claude' : 'shell');
}

const _TYPE_GLYPH = { claude: 'C', shell: '$', server: '⚙' };
function _typeGlyph(t) { return _TYPE_GLYPH[t] || (t ? t[0].toUpperCase() : '?'); }

// --- Sidebar agent list ----------------------------------------------------

// Per-window context cache (used_pct etc.), fed by both the sidebar refresh and
// the wall tick so rows can show ctx% even when the wall isn't open.
let _ctxByWid = {};
function updateCtxCache(ctx) {
    if (!Array.isArray(ctx)) return;
    const m = {};
    ctx.forEach(c => { if (c && c.window_id) m[c.window_id] = c; });
    _ctxByWid = m;
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
             onclick="event.stopPropagation(); chela.toggleFavCwd(this.dataset.cwd)">${faved ? '&#9733;' : '&#9734;'}</button>`
        : '';

    const type = _agentType(a);

    return `<div class="agent-row rich${active}" data-agent="${attrEsc(a.name)}" onclick="chela.selectAgent(this.dataset.agent)">
        <span class="term-status-dot ${stCls}" title="${attrEsc(type)} · ${stWord}"></span>
        <span class="ar-type ${attrEsc(type)}" title="${attrEsc(type)} window">${escHtml(_typeGlyph(type))}</span>
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
    // Keep the tab title/favicon in lockstep with the agent list.
    updateTabSignal(agents);
    const host = document.getElementById('sidebar-agents');
    if (!host) return;
    const rows = agents || [];
    if (!rows.length) {
        host.innerHTML = '<div class="side-empty">No agents</div>';
        return;
    }

    // Triage: agents waiting on input float into a "Needs you" cluster above the
    // project groups. Each agent shows in exactly one place — lifted out of its
    // group while it's blocked, like a starred item.
    const waiting = rows.filter(wantsHuman)
        .sort((a, b) => a.name.localeCompare(b.name));
    const rest = rows.filter(a => !wantsHuman(a));

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
            <div class="group-head" data-g="${attrEsc(e.key)}" onclick="chela.toggleGroup(this.dataset.g)">
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
        setAgentsCache(agents || []);
        if (ctx) updateCtxCache(ctx);
        renderSidebarAgents(_agentsCache);
    } catch (e) {
        // transient — keep the last render; the next tick retries.
    }
}

// The WORK badges used to be a THIRD independent poller of /api/dispatcher, right
// here — fetching the same payload the Dispatch and Kanban views were each already
// fetching on their own timers. They are now filled by work.js's single poll (the
// slots themselves are declared on the Work view in views.js).

// --- Agent detail view -----------------------------------------------------

function renderAgentDetail() {
    const host = document.getElementById('agent-detail');
    if (!host) return;
    const a = (_agentsCache || []).find(x => x.name === _detailAgent);
    if (!a) {
        host.innerHTML = `<div class="detail-head">
            <span class="detail-back" onclick="chela.selectView('agents')">← Agents</span>
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
        actions.push(`<button onclick="chela.openSendMsg('${attrEsc(a.name)}')">Message</button>`);
    }
    if (a.has_schedules) actions.push(`<button onclick="chela.triggerSchedule('${attrEsc(a.name)}')">Trigger</button>`);
    if (a.claude_running) {
        actions.push(`<button onclick="chela.restartAgent('${attrEsc(a.name)}')">Restart</button>`);
        actions.push(`<button class="btn-danger" onclick="chela.stopAgent('${attrEsc(a.name)}')">Stop</button>`);
    } else {
        actions.push(`<button class="btn-accent" onclick="chela.startAgent('${attrEsc(a.name)}')">Start</button>`);
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
            <span class="detail-back" onclick="chela.selectView('agents')">← Agents</span>
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
    const collabName = localStorage.getItem('chela_collab_name') || '';
    const collabAuto = localStorage.getItem('chela_collab_autoname') || 'auto-assigned';
    const runToastsMuted = localStorage.getItem('chela_mute_run_toasts') === '1';
    body.innerHTML = `
        <section class="settings-section" id="settings-status">
            <h4>Connections &amp; Status</h4>
            <div class="s-status-list"><div class="s-desc">Loading…</div></div>
        </section>

        <section class="settings-section">
            <h4>Projects folder</h4>
            <p class="s-desc">Scanned for git repos to suggest in the <strong>+</strong> launch
            menu. Defaults to <code>~/projects</code> (or the <code>CHELA_PROJECTS_DIR</code>
            env var). Takes effect immediately — no restart.</p>
            <div class="s-row">
                <input id="cfg-projects-dir" class="s-input" type="text"
                       placeholder="~/projects" autocomplete="off"
                       onkeydown="if(event.key==='Enter')chela.saveProjectsDir()">
                <button class="btn-accent" onclick="chela.saveProjectsDir()">Save</button>
            </div>
            <div id="cfg-projects-msg" class="s-savemsg"></div>
        </section>

        <section class="settings-section" id="settings-agentmode">
            <h4>Dispatcher agent mode</h4>
            <p class="s-desc">Permission mode for agents the <strong>dispatcher</strong> spawns.
            Applies to the <strong>next</strong> dispatch — an agent already running keeps the
            mode it started with. Only the mode is settable; the rest of the launch command is
            fixed in code.</p>
            <div class="s-row">
                <span class="s-rowlabel">Permission mode</span>
                <select id="agent-mode-select" class="s-select"
                        onchange="chela.setAgentPermissionMode(this.value)">
                    <option value="">Loading…</option>
                </select>
            </div>
            <div id="agent-mode-msg" class="s-savemsg"></div>
            <p class="s-desc" id="agent-mode-source"></p>
            <div class="s-row">
                <span class="s-rowlabel">Model</span>
                <select id="agent-model-select" class="s-select"
                        onchange="chela.setAgentModel(this.value)">
                    <option value="">Loading…</option>
                </select>
            </div>
            <div id="agent-model-msg" class="s-savemsg"></div>
            <p class="s-desc" id="agent-model-source"></p>
            <p class="s-desc">The <strong>coding</strong> model — cmx tasks rarely need Opus,
            so Sonnet is the default (cheaper/faster). The <strong>judge</strong> (the
            adversarial reviewer) always runs on a capable model and is not affected by this
            setting.</p>
        </section>

        <section class="settings-section">
            <h4>Needs-input notifications</h4>
            <p class="s-desc">Fires a one-shot ping when an agent's pane enters
            <code>waiting</code> (blocked on a prompt or question).</p>
            <div class="s-row">
                <span class="s-rowlabel">Review toasts</span>
                <select id="run-toasts-select" class="s-select" onchange="chela.setRunToastsMuted(this.value)">
                    <option value="show"${runToastsMuted ? '' : ' selected'}>Show</option>
                    <option value="muted"${runToastsMuted ? ' selected' : ''}>Muted</option>
                </select>
            </div>
            <p class="s-desc">Pop a dashboard toast when a dispatcher run turns
            <code>awaiting_review</code> (or done / failed) — so you learn a run
            needs review without watching the board.</p>
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
                <select id="theme-select" class="s-select" onchange="chela.setTheme(this.value)">
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
                <select id="term-latin-select" class="s-select" onchange="chela.setTermLatin(this.value)">
                    ${Object.keys(TERM_LATIN_LABELS)
                        .map(k => `<option value="${k}"${termLatin === k ? ' selected' : ''}>${TERM_LATIN_LABELS[k]}</option>`)
                        .join('')}
                </select>
            </div>
            <div class="s-row">
                <span class="s-rowlabel">Hebrew font</span>
                <select id="term-font-select" class="s-select" onchange="chela.setTermFont(this.value)">
                    ${Object.keys(TERM_FONT_LABELS)
                        .map(k => `<option value="${k}"${termFont === k ? ' selected' : ''}>${TERM_FONT_LABELS[k]}</option>`)
                        .join('')}
                </select>
            </div>
            <div class="s-row">
                <span class="s-rowlabel">Size</span>
                <select id="term-size-select" class="s-select" onchange="chela.setTermSize(this.value)">
                    ${['12', '13', '14', '15', '16', '18']
                        .map(s => `<option value="${s}"${termSize === s ? ' selected' : ''}>${s}px</option>`)
                        .join('')}
                </select>
            </div>
        </section>

        <section class="settings-section">
            <h4>Collaboration</h4>
            <p class="s-desc">Your display name in shared terminals (presence pills +
            the pane facepile). Leave blank for a stable auto-name. Saved per browser,
            applies live.</p>
            <div class="s-row">
                <span class="s-rowlabel">Display name</span>
                <input id="collab-name" class="s-input" type="text" maxlength="24"
                       autocomplete="off" placeholder="${escHtml(collabAuto)}"
                       value="${attrEsc(collabName)}" oninput="chela.setCollabName(this.value)">
            </div>
            <div class="s-row">
                <span class="s-rowlabel">Relay</span>
                <code id="collab-relay" style="word-break:break-all;font-size:11px">…</code>
            </div>
            <p class="s-desc">End-to-end encrypted — the relay (<code>CHELA_COLLAB_RELAY</code>)
            is a zero-knowledge fan-out that only ever sees ciphertext (keys are derived in your
            browser from the pairing code). It does see room names + traffic timing (metadata) —
            run your own relay for full metadata privacy.</p>
        </section>`;
    if (focus === 'notify') body.scrollTop = 0;
    _loadProjectsSetting();
    _loadCollabSetting();
    _loadAgentModeSetting();
    _loadAgentModelSetting();
    _loadSettingsStatus();
}

// Dispatcher agent permission mode. The <select> is populated from the server's
// enum (/api/config → agent_permission_modes) rather than a hardcoded list here,
// so the UI can never offer a mode the server would reject — and the server
// re-validates anyway (the gate is there, not here). Annotations are only given
// for the modes whose behaviour is documented; the rest show the raw CLI name.
const AGENT_MODE_NOTES = {
    auto: 'safe ops auto-approved, risky ones gated',
    bypassPermissions: '⚠ no prompts at all',
};

function _agentModeLabel(m, dflt) {
    const note = AGENT_MODE_NOTES[m];
    return m + (m === dflt ? ' · built-in default' : '') + (note ? ' · ' + note : '');
}

// Renders "which source is winning" honestly: a WORKFLOW.md that pins agent.cmd
// SHADOWS this setting for that workflow (dispatcher.resolve_agent_cmd), so say
// so rather than letting the drawer imply the mode always applies.
function _renderAgentModeSource(cfg) {
    const el = document.getElementById('agent-mode-source');
    if (!el) return;
    const overrides = (cfg && cfg.agent_cmd_overrides) || [];
    const eff = (cfg && cfg.agent_permission_mode_effective) || '';
    const stored = (cfg && cfg.agent_permission_mode) || '';
    const src = stored ? 'this setting' : 'the built-in default';
    let html = `In effect: <code>claude --permission-mode ${escHtml(eff)}</code> — from ${src}.`;
    if (overrides.length) {
        html += ' <strong>Overridden</strong> for ' + overrides.map(o =>
            `<code>${escHtml(o.workflow)}</code> (<code>${escHtml(o.cmd)}</code>)`).join(', ') +
            ' — a workflow that pins <code>agent.cmd</code> wins over this setting.';
    }
    el.innerHTML = html;
}

async function _loadAgentModeSetting() {
    const sel = document.getElementById('agent-mode-select');
    if (!sel) return;
    let cfg;
    try {
        cfg = await api('/api/config');
    } catch (e) { sel.innerHTML = '<option value="">(unavailable)</option>'; return; }
    const modes = (cfg && cfg.agent_permission_modes) || [];
    const dflt = (cfg && cfg.agent_permission_mode_default) || '';
    const stored = (cfg && cfg.agent_permission_mode) || '';
    sel.innerHTML = modes.map(m =>
        `<option value="${attrEsc(m)}"${stored === m ? ' selected' : ''}>${escHtml(_agentModeLabel(m, dflt))}</option>`
    ).join('');
    // Unset reads as the built-in default — select it without storing anything.
    if (!stored && dflt) sel.value = dflt;
    _renderAgentModeSource(cfg);
}

async function setAgentPermissionMode(v) {
    const msg = document.getElementById('agent-mode-msg');
    const setMsg = (cls, t) => { if (msg) { msg.className = 's-savemsg ' + cls; msg.textContent = t; } };
    setMsg('', 'Saving…');
    let cfg;
    try {
        cfg = await api('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ agent_permission_mode: v }),
        });
    } catch (e) { setMsg('err', 'Save failed — mode unchanged.'); return; }
    // api() resolves on a 4xx too, so the server's rejection arrives as a body,
    // not a throw. Fail closed: report it and re-read the mode that IS stored.
    if (!cfg || cfg.error) {
        setMsg('err', 'Rejected — mode unchanged.');
        _loadAgentModeSetting();
        return;
    }
    setMsg('ok', 'Saved · next dispatch launches in ' + (cfg.agent_permission_mode_effective || v));
    _renderAgentModeSource(cfg);
}

// Coding-agent model. Same rails as the permission mode: the <select> is
// populated from the server's enum (/api/config → agent_models) so it can never
// offer a value the server would reject, and the server re-validates anyway. The
// JUDGE's model is a fixed capable default, decoupled from this — not surfaced.
function _agentModelLabel(m, dflt) {
    return m + (m === dflt ? ' · default' : '');
}

// The model rides on the permission-mode command, so a WORKFLOW.md that pins
// agent.cmd shadows it too — the mode-source line already says which workflows
// override, so here we only state the effective coding model.
function _renderAgentModelSource(cfg) {
    const el = document.getElementById('agent-model-source');
    if (!el) return;
    const eff = (cfg && cfg.agent_model_effective) || '';
    const stored = (cfg && cfg.agent_model) || '';
    const src = stored ? 'this setting' : 'the built-in default';
    el.innerHTML = `Coding agents launch with <code>--model ${escHtml(eff)}</code> — from ${src}.`;
}

async function _loadAgentModelSetting() {
    const sel = document.getElementById('agent-model-select');
    if (!sel) return;
    let cfg;
    try {
        cfg = await api('/api/config');
    } catch (e) { sel.innerHTML = '<option value="">(unavailable)</option>'; return; }
    const models = (cfg && cfg.agent_models) || [];
    const dflt = (cfg && cfg.agent_model_default) || '';
    const stored = (cfg && cfg.agent_model) || '';
    sel.innerHTML = models.map(m =>
        `<option value="${attrEsc(m)}"${stored === m ? ' selected' : ''}>${escHtml(_agentModelLabel(m, dflt))}</option>`
    ).join('');
    // Unset reads as the built-in default — select it without storing anything.
    if (!stored && dflt) sel.value = dflt;
    _renderAgentModelSource(cfg);
}

async function setAgentModel(v) {
    const msg = document.getElementById('agent-model-msg');
    const setMsg = (cls, t) => { if (msg) { msg.className = 's-savemsg ' + cls; msg.textContent = t; } };
    setMsg('', 'Saving…');
    let cfg;
    try {
        cfg = await api('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ agent_model: v }),
        });
    } catch (e) { setMsg('err', 'Save failed — model unchanged.'); return; }
    // api() resolves on a 4xx too, so a server rejection arrives as a body, not
    // a throw. Fail closed: report it and re-read the model that IS stored.
    if (!cfg || cfg.error) {
        setMsg('err', 'Rejected — model unchanged.');
        _loadAgentModelSetting();
        return;
    }
    setMsg('ok', 'Saved · next dispatch launches with --model ' + (cfg.agent_model_effective || v));
    _renderAgentModelSource(cfg);
}

// Live "Connections & Status" surface (READ-ONLY). Fetches /api/settings and
// renders each section's items as rows with a colorblind-safe status badge:
// ●/○ SHAPE + a text label ("Connected" / "Off"), never colour alone — Liav is
// red-weak, so the glyph and word carry the state and colour is only a hint.
async function _loadSettingsStatus() {
    const host = document.querySelector('#settings-status .s-status-list');
    if (!host) return;
    let data;
    try {
        data = await api('/api/settings');
    } catch (e) {
        host.innerHTML = '<div class="s-desc">Status unavailable.</div>';
        return;
    }
    const sections = (data && data.sections) || [];
    if (!sections.length) { host.innerHTML = '<div class="s-desc">No status.</div>'; return; }
    host.innerHTML = sections.map(sec => `
        <div class="s-status-group">
            <div class="s-status-grouphead">${escHtml(sec.title || '')}</div>
            ${(sec.items || []).map(_statusRowHtml).join('')}
        </div>`).join('');
}

function _statusRowHtml(it) {
    const on = !!it.on;
    // ●/○ shape carries the on/off state independently of colour (Liav red-weak).
    const glyph = on ? '●' : '○';
    const detail = it.detail ? `<span class="s-status-detail" title="${attrEsc(it.detail)}">${escHtml(it.detail)}</span>` : '';
    return `<div class="s-status-row">
        <span class="s-status-badge ${on ? 'on' : 'off'}">
            <span class="s-status-dot" aria-hidden="true">${glyph}</span>${escHtml(it.state || '')}
        </span>
        <span class="s-status-label">${escHtml(it.label || '')}</span>
        ${detail}
    </div>`;
}

// Persistent collab display name (per browser). Empty → clear → presence.js falls
// back to the persisted auto-name. The same-origin `storage` event delivers the
// change to each ttyd iframe's presence.js (which re-broadcasts) — no direct call.
function setCollabName(v) {
    v = (v || '').trim();
    if (v) localStorage.setItem('chela_collab_name', v);
    else localStorage.removeItem('chela_collab_name');
}

async function _loadCollabSetting() {
    const el = document.getElementById('collab-relay');
    if (!el) return;
    try {
        const cfg = await api('/api/config');
        el.textContent = (cfg && cfg.collab_relay) || '(default)';
    } catch (e) { el.textContent = '(unavailable)'; }
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
    // Refresh the launch menu so new suggestions appear right away.
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

// Mute / unmute the dispatcher run-state review toasts (sse.js reads this key).
function setRunToastsMuted(v) {
    if (v === 'muted') localStorage.setItem('chela_mute_run_toasts', '1');
    else localStorage.removeItem('chela_mute_run_toasts');
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

// The "+" menu is also the LAUNCH menu: Favorites + Recent live in it (launcher.js
// fills #new-menu-launch). Re-render on open so a pin/launch from anywhere else is
// already reflected when it appears.
function openNewMenu(ev) {
    if (ev) ev.stopPropagation();
    const m = document.getElementById('new-menu');
    if (!m) return;
    if (typeof refreshLauncher === 'function') refreshLauncher();
    const anchor = (ev && ev.currentTarget) || document.getElementById('btn-new');
    // Show it BEFORE measuring: a display:none element has no offsetWidth.
    m.style.display = 'block';
    const r = anchor.getBoundingClientRect();
    m.style.top = (r.bottom + 4) + 'px';
    // Right-align to the button off the MEASURED width, and clamp so it never runs
    // off the left edge. A hardcoded width here (it used to be 160, from the old
    // popover) silently sends the menu off the RIGHT edge the moment the CSS gets
    // wider than the guess — which .launch-menu's 232px min-width did, on a button
    // that sits ~55px from the viewport edge.
    m.style.left = Math.max(8, r.right - m.offsetWidth) + 'px';
    setTimeout(() => document.addEventListener('click', hideNewMenu, { once: true }), 0);
}

function hideNewMenu() {
    const m = document.getElementById('new-menu');
    if (m) m.style.display = 'none';
}

// Topbar overflow menu (Lucide more-vertical): consolidates the secondary actions
// — Share current, Notifications, Settings — plus the usage/updated readouts, so
// the bar stays to its primaries (jump · New · overflow) on both desktop and
// mobile. The safety kill-switch (#btn-shares) is deliberately NOT in here — it
// stays visible whenever a share is live. Anchored + light-dismiss like #new-menu.
function openOverflowMenu(ev) {
    if (ev) ev.stopPropagation();
    const m = document.getElementById('overflow-menu');
    if (!m) return;
    const anchor = (ev && ev.currentTarget) || document.getElementById('btn-overflow');
    m.style.display = 'block';
    const r = anchor.getBoundingClientRect();
    m.style.top = (r.bottom + 6) + 'px';
    // Right-align to the button; clamp so it never runs off the left edge.
    m.style.left = Math.max(8, r.right - m.offsetWidth) + 'px';
    setTimeout(() => document.addEventListener('click', hideOverflowMenu, { once: true }), 0);
}

function hideOverflowMenu() {
    const m = document.getElementById('overflow-menu');
    if (m) m.style.display = 'none';
}

// Spawn a plain shell window. The backend spawn endpoint is currently behind
// the terminals feature flag (it was the wall's spawner); until a non-gated
// endpoint lands this surfaces the API's response rather than failing silently.
async function newShellWindow() {
    try {
        const res = await api('/api/agents/spawn', { method: 'POST' });
        if (res && res.ok) { setAgentsCache([]); refreshSidebar(); }
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
    // The palette's own hardcoded copy of the view list is gone — it reads the
    // registry, so a view added or removed there is added or removed here.
    paletteViews(VIEWS, _viewCtx()).forEach(v => items.push({
        icon: lucideIcon('layout-grid'), title: v.label, sub: 'view', run: () => selectView(v.id),
    }));

    (_agentsCache || []).forEach(a => {
        const word = _AGENT_STATUS_WORD[agentDotColor(a)] || 'idle';
        items.push({ dot: _SIDEBAR_DOT_CLASS[agentDotColor(a)] || 'idle', title: _agentLabel(a),
                     sub: 'session · ' + word, run: () => selectAgent(a.name) });
    });

    // Share / Stop sharing per live session (same server flag as the pane button).
    if (TERMINALS_ON) {
        (_agentsCache || []).forEach(a => {
            if (!a.window_id) return;
            const shared = typeof _sharedWids !== 'undefined' && _sharedWids.has(a.window_id);
            items.push({ icon: shared ? lucideIcon('x') : lucideIcon('share-2'),
                         title: (shared ? 'Stop sharing ' : 'Share ') + _agentLabel(a),
                         sub: shared ? 'shared session' : 'session',
                         run: () => {
                             const sel = '.gs-share-btn[data-wid="' + (window.CSS && CSS.escape ? CSS.escape(a.window_id) : a.window_id) + '"]';
                             const btn = document.querySelector(sel);
                             if (shared) _stopShare(a.window_id); else shareBtnClick(btn, a.window_id);
                         } });
        });
    }

    if (TERMINALS_ON && typeof _launcherData !== 'undefined' && _launcherData) {
        const seen = new Set();
        [...(_launcherData.favorites || []), ...(_launcherData.recent || [])].forEach(e => {
            if (!e || seen.has(e.path)) return;
            seen.add(e.path);
            items.push({ icon: lucideIcon('play'), title: 'Launch ' + (e.label || e.path), sub: 'project',
                         run: () => launchProject(e.path) });
        });
    }

    items.push({ icon: lucideIcon('terminal'), title: 'New shell window', sub: 'action', run: () => newShellWindow() });
    items.push({ icon: lucideIcon('clock'), title: 'Add scheduled task', sub: 'action',
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
                  onmouseenter="_palHover(${i})" onclick="chela._palRun(${i})">
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

// --- Stage 0: ES-module exports ---
export { openPalette, refreshSidebar, renderAgentDetail, renderNav, renderSidebarAgents, selectView, updateCtxCache };

// --- Stage 0: window.chela — surface reachable from inline HTML handlers ---
window.chela = window.chela || {};
Object.assign(window.chela, { _palRun, _renderPalette, closePalette, closeSidebar, hideNewMenu, hideOverflowMenu, newShellWindow, openNewMenu, openOverflowMenu, openPalette, saveProjectsDir, selectAgent, selectView, setAgentModel, setAgentPermissionMode, setCollabName, setRunToastsMuted, setTermFont, setTermLatin, setTermSize, setTheme, toggleGroup, toggleSettings, toggleSidebar });
