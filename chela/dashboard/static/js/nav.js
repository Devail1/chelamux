// --- Stage 0: ES-module imports ---
import { $$, TERMINALS_ON, _agentsCache, agentDotColor, api, attrEsc, escHtml, lucideIcon, setCurrentTab, updateTabSignal, wantsHuman } from './util';
import { onOrchestratorChange, orchestratorState } from '../../frontend/src/shared/orchestrator';
import { refreshSummary } from '../../frontend/src/features/primarymenu';
import { detailAgent, redrawAgentDetail, setAgentDetailHost } from '../../frontend/src/features/agentdetail';
import { showAddSchedule } from '../../frontend/src/features/schedules';
import { openNewTask } from '../../frontend/src/features/newtask';
import { _displayLabel, adoptSidebarDefault, _renderedWids, focusPaneByWid, isWallVisible, minimizePane, setTermMode } from './terminals';
import { minimizedPanes, orderedWids } from '../../frontend/src/shared/store/wall';
import { launchProject, launcher, newShellWindow, refreshLauncher, setLauncherHost } from '../../frontend/src/features/launcher';
import { setApprovalNotify } from '../../frontend/src/features/approvals/model';
import { VIEWS } from './views';
import { findView, navViews, otherViews, paletteViews, panelId } from './viewreg';
import { refresh } from '../../frontend/src/shared/scheduler';
import { refreshCostTab } from './usage';
import { resolveWindowId } from './windowid';
import { followUpdate } from '../../frontend/src/features/update/flow';
import { memNote, stepPayload, workflowNote } from './concurrencymodel';
import { isShared, shareSession, stopShare } from '../../frontend/src/features/share';
import { closeSidebar, configureSidebar, refreshSidebar, renderSidebarAgents, setActiveRow, setNav } from '../../frontend/src/features/sidebar/controller';

// ---------------------------------------------------------------------------
// Sidebar + canvas navigation (replaces the old tab bar)
//
// The canvas is a set of .panel elements (one per view) kept from the tab
// layout, so every existing renderer (renderKanban -> #kanban-board,
// renderTerminals -> the wall, ...) works unchanged. `currentTab` (declared in
// util.js) is still the active-view variable, so the scheduler and SSE handlers keep
// dispatching on it; only the *chrome* that sets it changed from a tab bar to
// this sidebar.
//
// The set of views is NOT declared here. It is views.js — the registry — and this
// file reads it: renderNav() builds the .side-item rows from it, and selectView
// takes each view's enter/exit hooks from it instead of the per-view if/else
// chain that used to live below. Same for the command palette (which carried a
// third hardcoded copy of the view list).
// ---------------------------------------------------------------------------

// --- The sidebar (CMX-278) --------------------------------------------------
// The sidebar is the React island's (frontend/src/features/sidebar): its state and commands
// are controller.ts's. What it needs from the rest of the page, it reaches through this.
configureSidebar({
    label: _agentLabel,
    onWall: () => _renderedWids.filter(w => !minimizedPanes.get().has(w)),
    orchWid: () => orchestratorState().wid,
    wants: wantsHuman,
    tabSignal: agents => updateTabSignal(agents),
    adoptDefault: () => adoptSidebarDefault(),
    openPane: _openPane,
    launchProject: (cwd, opts) => launchProject(cwd, opts),
});

// The orchestrator slot changes independently of the agent list poll (a pane's toggle, the
// decisions dropdown): redraw off the cached list, no refetch to move one badge.
onOrchestratorChange(() => {
    renderSidebarAgents(_agentsCache || []);
    redrawAgentDetail();   // its Role row reads the slot too
});

// --- The nav, rendered from the registry ------------------------------------
// One nav row per registered, enabled, non-virtual view, including its badge slots
// (the island draws them). Adding a view to views.js puts it there; there is no second list.

function _viewCtx() { return { terminalsOn: TERMINALS_ON }; }

function renderNav() {
    setNav({ items: navViews(VIEWS, _viewCtx()).map(({ id, label, lucide, badges }) => ({ id, label, lucide, badges })) });
}

// --- View switching --------------------------------------------------------

function selectView(view) {
    const v = findView(VIEWS, view);
    setCurrentTab(view);
    detailAgent.set(null);

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

// Clicking an agent (sidebar row or command palette) acts relative to the
// wall (CMX-139), three cases:
//   1. wall already visible AND this pane is open on it (rendered, not
//      minimized) -> minimize it. A click on an already-summoned pane hides
//      it, mirroring the on-wall ring cue (CMX-137, _agentRowHtml).
//   2. not on the wall at all (another tab, or single-terminal mode) ->
//      switch to wall mode and focus/restore the pane there.
//   3. wall visible but the pane is minimized -> restore + focus (the old
//      always-focus behavior).
// The metadata "detail" card is the fallback only when there's no wall to
// land on (terminals off) or the window isn't resolved yet.
function selectAgent(name) {
    if (TERMINALS_ON && typeof focusPaneByWid === 'function') {
        const a = (_agentsCache || []).find(x => x.name === name);
        if (a && a.window_id) {
            const wid = a.window_id;
            const openOnWall = _renderedWids.includes(wid) && !minimizedPanes.get().has(wid);
            if (isWallVisible() && openOnWall) {
                minimizePane(wid);
            } else {
                if (!isWallVisible()) setTermMode('wall');   // leaving single-terminal mode / another tab
                focusPaneByWid(wid);
            }
            return;
        }
    }
    showAgentDetail(name);
}
// Open (never toggle) a window's pane: unlike a row click (selectAgent), which
// minimizes a pane that is already open, a menu's "Open … pane" and Alt+N always land on it.
function _openPane(w) {
    if (!w) return;
    if (TERMINALS_ON && w.window_id && typeof focusPaneByWid === 'function') {
        if (!isWallVisible()) setTermMode('wall');
        focusPaneByWid(w.window_id);
        return;
    }
    showAgentDetail(w.name);
}

// Focus a single agent in the canvas (metadata detail / transcript).
function showAgentDetail(name) {
    setCurrentTab('agent-detail');
    detailAgent.set(name);   // the agent-detail island (features/agentdetail) draws it
    $$('.panel').forEach(p => p.classList.remove('active'));
    const panel = document.getElementById(panelId('agent-detail'));
    if (panel) panel.classList.add('active');
    _syncSidebarActive('agent-detail', name);
    // Drilling in is leaving every other view — same registry-driven teardown as
    // selectView (agent-detail is a registered, virtual view).
    otherViews(VIEWS, 'agent-detail').forEach(o => { if (o.exit) o.exit(); });
    refreshSummary();
    closeSidebar();   // navigating dismisses the mobile drawer (no-op on desktop)
}

function _syncSidebarActive(view, agentName) {
    // The agent-detail view has no nav item of its own (it is reached from the
    // always-visible sidebar Sessions list), so no nav row lights up while drilled in.
    setNav({ active: view === 'agent-detail' ? null : view });
    setActiveRow(agentName || null);
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

// --- Session role (per-row cue) ----------------------------------------------
// CMX-300: three roles, mutually exclusive. 'orchestrator' = this window holds
// the single decisions-inbox slot (orchestrator.js / chela.inbox.register — the
// SAME fact terminals.js's pane toggle and decisions.js's owner chip already
// render, just read here too). 'dispatched' = the dispatcher spawned and owns
// this window (API-provided `a.dispatched`, see api_agents in app.py). Anything
// else is 'plain' — a session a human opened by hand — and gets no badge at all,
// since it is the common case and a badge on every row would be noise, not signal.
// Orchestrator wins if a window is somehow both (subscribing a dispatched worker
// is not the normal flow, but the inbox slot is a fact about the window, not
// about how it was launched).
function _agentRole(a) {
    if (a && a.window_id && orchestratorState().wid === a.window_id) return 'orchestrator';
    if (a && a.dispatched) return 'dispatched';
    return 'plain';
}

const _ROLE_LABEL = { orchestrator: 'Orchestrator', dispatched: 'Dispatched', plain: 'Plain session' };


// Friendly label, shared with the wall panes (_displayLabel): a manual rename
// wins, else Claude's session title, else the window name (CMX-62) — so a
// session reads identically in the sidebar and on its pane title. Falls back to
// the raw name when terminals.js isn't loaded.
function _agentLabel(a) {
    if (typeof _displayLabel === 'function' && a && a.window_id) return _displayLabel(a.window_id);
    return a ? a.name : '';
}

// Status colour → human word, for the dot's tooltip.
const _AGENT_STATUS_WORD = { green: 'working', yellow: 'waiting', grey: 'idle' };
// Status colour → the pane dot's CSS state class, so the sidebar dot pulses
// identically to the wall's .term-status-dot (working/waiting/idle).
const _SIDEBAR_DOT_CLASS = { green: 'working', yellow: 'waiting', grey: 'idle' };

// --- Agent detail view -----------------------------------------------------
// The agent-detail island (frontend/src/features/agentdetail, CMX-219) draws it; what only
// this module knows (the view switch, the role, the attention dot) it reads through its host.
setAgentDetailHost({ selectView, roleLabel: a => _ROLE_LABEL[_agentRole(a)], dotColor: agentDotColor });
// A launch lands on the Wall and the sidebar the sidebar controller owns (features/launcher, CMX-219).
setLauncherHost({ focusPaneByWid, selectView, refreshSidebar });

// --- Settings modal (CMX-287: tabbed, replaces the old off-canvas drawer) --
//
// Liav: "should we make the settings sidebar a tab system modal? it would
// make settings easier to navigate, and will allow to show cost and maybe
// other things that are not related to the main views?" — confirmed both
// calls: revive cost.js (deleted as a standalone nav view by CMX-279, backend
// route untouched) as a tab here, and the modal REPLACES the drawer rather
// than wrapping it.
//
// Same #settings-drawer / #drawer-body / #drawer-scrim ids as the old drawer
// (only the CSS class + internal structure changed) — every existing test and
// caller (toggleSettings, the popover's "Settings"/"Notifications" rows) keeps
// working unchanged. What's new is #settings-tabs (the tab rail) and wrapping
// each settings-section group in a `.settings-tabpanel` that _selectSettingsTab
// shows/hides; every individual section's ids (#dispatch-rows, #cost-table,
// ...) are untouched, so the per-section loaders below are the same functions
// the old drawer called.
const SETTINGS_TABS = [
    { id: 'general', label: 'General' },
    { id: 'timing', label: 'Timing' },
    { id: 'dispatch', label: 'Dispatch' },
    { id: 'notifications', label: 'Notifications' },
    { id: 'cost', label: 'Cost' },
    { id: 'appearance', label: 'Appearance' },
    { id: 'collaboration', label: 'Collaboration' },
];
let _settingsTab = 'general';

function toggleSettings(focus) {
    const modal = document.getElementById('settings-drawer');
    const scrim = document.getElementById('drawer-scrim');
    if (!modal) return;
    const open = !modal.classList.contains('open');
    modal.classList.toggle('open', open);
    if (scrim) scrim.classList.toggle('open', open);
    if (open) {
        // renderSettings → selectSettingsTab drops any query left from the last
        // visit: a stale filter would read as settings having vanished.
        renderSettings(focus);
        _focusSettingsSearch();
    }
}

// focus: 'notify' opens straight to the Notifications tab (the popover's
// "Notifications" row uses this) — otherwise the modal reopens on whichever
// tab was last selected, defaulting to General on first open.
function selectSettingsTab(tab) {
    if (!SETTINGS_TABS.some(t => t.id === tab)) return;
    // Picking a tab mid-search means "take me there": drop the query first.
    if (_settingsQuery) clearSettingsSearch();
    _settingsTab = tab;
    _paintSettingsTab(tab);
    if (tab === 'cost') refreshCostTab();
}

function _paintSettingsTab(tab) {
    $$('.settings-tab').forEach(el => el.classList.toggle('active', el.dataset.tab === tab));
    $$('.settings-tabpanel').forEach(el => el.classList.toggle('active', el.dataset.tab === tab));
    _revealSettingsTab(tab);
}

// CMX-57: at phone width the tab strip is a horizontal scroller — and "Cost · A…"
// running off the edge gave no sign there was more. The strip scrolls on its own
// (never the modal); these keep its edge fades honest (.fade-start / .fade-end =
// "more tabs that way", style.css) and bring the selected tab into view.
function _syncSettingsTabFade() {
    const strip = document.getElementById('settings-tabs');
    if (!strip) return;
    const max = strip.scrollWidth - strip.clientWidth;
    strip.classList.toggle('fade-start', max > 1 && strip.scrollLeft > 1);
    strip.classList.toggle('fade-end', max > 1 && strip.scrollLeft < max - 1);
}

function _revealSettingsTab(tab) {
    const strip = document.getElementById('settings-tabs');
    const el = strip && strip.querySelector(`.settings-tab[data-tab="${tab}"]`);
    if (el && strip.scrollWidth > strip.clientWidth) {
        // Not scrollIntoView: that also scrolls every scrollable ANCESTOR, page included.
        const s = strip.getBoundingClientRect(), r = el.getBoundingClientRect();
        if (r.left < s.left) strip.scrollLeft -= s.left - r.left + 24;
        else if (r.right > s.right) strip.scrollLeft += r.right - s.right + 24;
    }
    _syncSettingsTabFade();
}

// --- Settings search (CMX-396) ---------------------------------------------
//
// Liav: "add a search box to the Settings drawer: type to filter every setting
// across all tabs, VS Code style". Filtering is purely ADDITIVE classes on the
// rows renderSettings already drew — nothing is re-rendered or moved — so an
// empty query is the drawer exactly as it was, and clearing one strips every
// mark it made and repaints the tab the user was on (_settingsTab never changes
// while searching).
//
// The unit is a labelled `.s-row` (one knob). A section whose OWN text — its
// heading, help text (.s-desc / .s-examples) or `data-keywords` — matches shows
// whole; otherwise it shows only the labelled rows whose label or row
// `data-keywords` match. Unlabelled rows (Save buttons, the update button) stay
// with any section that shows, so a filtered Timing row can still be saved.
let _settingsQuery = '';
const _S_HIDE = 's-search-hide';
const _S_HIT = 's-search-hit';

function _sNorm(s) { return String(s || '').replace(/\s+/g, ' ').toLowerCase(); }

function _sMatches(text, terms) {
    const t = _sNorm(text);
    return terms.every(q => t.includes(q));
}

function _sSectionText(sec) {
    const h = sec.querySelector('h4');
    const own = [...sec.querySelectorAll('.s-desc, .s-examples')].map(el => el.textContent);
    return [h ? h.textContent : '', sec.dataset.keywords || '', ...own].join(' ');
}

function _sRowText(row) {
    const label = row.querySelector('.s-rowlabel');
    return (label ? label.textContent : '') + ' ' + (row.dataset.keywords || '');
}

function _sLabelledRows(sec) {
    return [...sec.querySelectorAll('.s-row')].filter(r => r.querySelector('.s-rowlabel'));
}

function _applySettingsSearch() {
    const body = document.getElementById('drawer-body');
    const modal = document.getElementById('settings-drawer');
    if (!body) return;
    body.querySelectorAll('.' + _S_HIDE).forEach(el => el.classList.remove(_S_HIDE));
    body.querySelectorAll('.' + _S_HIT).forEach(el => el.classList.remove(_S_HIT));
    body.querySelectorAll('.settings-search-group').forEach(el => el.remove());
    const terms = _sNorm(_settingsQuery).trim().split(' ').filter(Boolean);
    const empty = document.getElementById('settings-search-empty');
    const status = document.getElementById('settings-search-status');
    if (!terms.length) {
        if (modal) modal.classList.remove('searching');
        _paintSettingsTab(_settingsTab);
        if (empty) { empty.hidden = true; empty.textContent = ''; }
        if (status) status.textContent = '';
        return;
    }
    if (modal) modal.classList.add('searching');
    // No tab is "the" tab while results span all of them.
    $$('.settings-tab').forEach(el => el.classList.remove('active'));
    let count = 0;
    body.querySelectorAll('.settings-tabpanel').forEach(panel => {
        let hits = 0;
        panel.querySelectorAll('.settings-section').forEach(sec => {
            const rows = _sLabelledRows(sec);
            if (_sMatches(_sSectionText(sec), terms)) { hits += Math.max(rows.length, 1); return; }
            const rowHits = rows.filter(r => _sMatches(_sRowText(r), terms));
            if (!rowHits.length) { sec.classList.add(_S_HIDE); return; }
            rows.forEach(r => { if (!rowHits.includes(r)) r.classList.add(_S_HIDE); });
            hits += rowHits.length;
        });
        if (!hits) return;
        panel.classList.add(_S_HIT);
        const tab = SETTINGS_TABS.find(t => t.id === panel.dataset.tab);
        const head = document.createElement('h3');
        head.className = 'settings-search-group';
        head.textContent = tab ? tab.label : panel.dataset.tab;
        panel.prepend(head);
        count += hits;
    });
    if (empty) {
        empty.hidden = count > 0;
        empty.textContent = count ? '' : `No settings match '${_settingsQuery.trim()}'`;
    }
    if (status) {
        status.textContent = count
            ? `${count} setting${count === 1 ? '' : 's'}`
            : `No settings match '${_settingsQuery.trim()}'`;
    }
}

// oninput of #settings-search.
function settingsSearch(q) {
    _settingsQuery = String(q || '');
    _applySettingsSearch();
}

function clearSettingsSearch() {
    const inp = document.getElementById('settings-search');
    if (inp) inp.value = '';
    settingsSearch('');
}

// Ctrl/⌘+, (either wire) lands in the search box, like VS Code. Not on a touch
// screen: focusing would throw the on-screen keyboard over the drawer on open.
function _focusSettingsSearch() {
    const inp = document.getElementById('settings-search');
    if (!inp) return;
    const coarse = typeof window.matchMedia === 'function' && window.matchMedia('(pointer: coarse)').matches;
    if (!coarse) inp.focus();
}

function renderSettings(focus) {
    const body = document.getElementById('drawer-body');
    const tabsHost = document.getElementById('settings-tabs');
    if (!body) return;
    if (tabsHost) {
        tabsHost.innerHTML = SETTINGS_TABS.map(t =>
            `<div class="settings-tab" data-tab="${t.id}" onclick="chela.selectSettingsTab(this.dataset.tab)">${escHtml(t.label)}</div>`
        ).join('');
        if (!tabsHost.dataset.fadeWired) {
            tabsHost.dataset.fadeWired = '1';
            tabsHost.addEventListener('scroll', _syncSettingsTabFade, { passive: true });
            window.addEventListener('resize', _syncSettingsTabFade);
        }
    }
    const theme = localStorage.getItem('chela_theme') || 'dark';
    const termLatin = localStorage.getItem('chela_term_latin') || 'jetbrains';
    const termFont = localStorage.getItem('chela_term_font') || 'miriam';
    const termSize = localStorage.getItem('chela_term_fontsize') || '14';
    const collabName = localStorage.getItem('chela_collab_name') || '';
    const collabAuto = localStorage.getItem('chela_collab_autoname') || 'auto-assigned';
    const runToastsMuted = localStorage.getItem('chela_mute_run_toasts') === '1';
    const approvalNotify = localStorage.getItem('chela_approval_notify') === '1';
    body.innerHTML = `
        <div class="settings-tabpanel" data-tab="general">
        <section class="settings-section" id="settings-status" data-keywords="health connected daemon telegram services">
            <h4>Connections &amp; Status</h4>
            <div class="s-status-list"><div class="s-desc">Loading…</div></div>
        </section>

        <section class="settings-section" id="settings-update" data-keywords="upgrade version git pull restart pm2">
            <h4>Update</h4>
            <div class="s-status-row" id="update-status-row">
                <span class="s-status-badge off"><span class="s-status-dot" aria-hidden="true">○</span>Checking…</span>
                <span class="s-status-detail" id="update-status-detail"></span>
            </div>
            <p class="s-desc">Pulls this checkout, re-syncs deps, and restarts every running
            <code>chela-*</code> PM2 service (including this dashboard), the same as running
            <code>chela update</code> from the CLI. A dirty working tree or a branch diverged
            from its upstream refuses rather than clobbering anything.</p>
            <div class="s-row">
                <button class="btn-accent" id="update-apply-btn" onclick="chela.applyUpdate()" disabled hidden>Update now</button>
            </div>
            <div id="update-apply-msg" class="s-savemsg"></div>
        </section>

        <section class="settings-section" data-keywords="repos repositories directory path launcher">
            <h4>Projects folder</h4>
            <p class="s-desc">Scanned for git repos to suggest in the <strong>+</strong> launch
            menu. Defaults to <code>~/projects</code> (or the <code>CHELA_PROJECTS_DIR</code>
            env var). Takes effect immediately, no restart.</p>
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
            Applies to the <strong>next</strong> dispatch. An agent already running keeps the
            mode it started with. Only the mode is settable; the rest of the launch command is
            fixed in code.</p>
            <div class="s-row" data-keywords="bypass approve prompts auto">
                <span class="s-rowlabel">Permission mode</span>
                <select id="agent-mode-select" class="s-select"
                        onchange="chela.setAgentPermissionMode(this.value)">
                    <option value="">Loading…</option>
                </select>
            </div>
            <div id="agent-mode-msg" class="s-savemsg"></div>
            <p class="s-desc" id="agent-mode-source"></p>
            <div class="s-row" data-keywords="sonnet opus haiku llm coding agent">
                <span class="s-rowlabel">Model</span>
                <select id="agent-model-select" class="s-select"
                        onchange="chela.setAgentModel(this.value)">
                    <option value="">Loading…</option>
                </select>
            </div>
            <div id="agent-model-msg" class="s-savemsg"></div>
            <p class="s-desc" id="agent-model-source"></p>
            <p class="s-desc">The <strong>coding</strong> model: cmx tasks rarely need Opus,
            so Sonnet is the default (cheaper/faster). The <strong>judge</strong> (the
            adversarial reviewer) always runs on a capable model and is not affected by this
            setting.</p>
        </section>

        <section class="settings-section" id="settings-remote-control">
            <h4>Remote Control</h4>
            <p class="s-desc">Launch sessions with Claude Code's <code>--remote-control</code> so
            they can be driven from claude.ai: windows opened from the dashboard, Telegram
            <code>/new</code>, and the orchestrator. Applies to <strong>new</strong> sessions
            only: a window already running keeps the flag it was launched with. Never applied
            to dispatcher agents or judges.</p>
            <div class="s-row" data-keywords="claude.ai remote phone mobile anywhere">
                <label class="s-rowlabel" for="remote-control-toggle">Remote Control: make new sessions reachable from claude.ai</label>
                <input id="remote-control-toggle" type="checkbox" role="switch" disabled
                       onchange="chela.setRemoteControl(this.checked)">
            </div>
            <div id="remote-control-msg" class="s-savemsg"></div>
            <p class="s-desc" id="remote-control-source"></p>
        </section>

        <section class="settings-section" id="settings-file-drop">
            <h4>File drop into terminals</h4>
            <p class="s-desc">Drop a file on a Wall terminal (or paste one, e.g. a screenshot)
            and it is saved to that session's <code>uploads/</code> folder and its
            <code>@uploads/&lt;name&gt;</code> is typed into the prompt (not sent). Works from
            your phone too. Never overwrites; <span id="file-drop-cap">25</span> MB per file.
            Share guests can never upload. A pane picks up a change when it reloads.</p>
            <div class="s-row" data-keywords="upload drag drop paste file image screenshot attach uploads phone">
                <label class="s-rowlabel" for="file-drop-toggle">File drop into terminals: save dropped or pasted files into the session</label>
                <input id="file-drop-toggle" type="checkbox" role="switch" disabled
                       onchange="chela.setFileDrop(this.checked)">
            </div>
            <div id="file-drop-msg" class="s-savemsg"></div>
            <p class="s-desc" id="file-drop-source"></p>
        </section>

        <section class="settings-section" id="settings-telegram-topics">
            <h4>Telegram topics</h4>
            <p class="s-desc">A new window reopens the archived topic of its folder instead of
            creating another. Applies on the bridge's next tick, no restart.</p>
            <div class="s-row" data-keywords="telegram topic forum delete archive close cleanup">
                <label class="s-rowlabel" for="tg-delete-toggle">Telegram: delete a topic when its window closes</label>
                <input id="tg-delete-toggle" type="checkbox" role="switch" disabled
                       onchange="chela.setTelegramDelete(this.checked)">
            </div>
            <div class="s-row">
                <button class="btn" id="tg-cleanup-btn" disabled onclick="chela.deleteArchivedTopics()">Delete archived topics</button>
            </div>
            <div id="tg-topics-msg" class="s-savemsg"></div>
        </section>

        <section class="settings-section" data-keywords="tailscale ssh tunnel vpn auth security">
            <h4>Remote access</h4>
            <p class="s-desc">Zero built-in auth: the dashboard binds <code>127.0.0.1</code>.
            Put it behind a tailnet or SSH tunnel; that is the trust boundary.</p>
            <div class="s-examples">
                <div class="s-ex"><span class="s-tag">tailnet</span><code>tailscale serve 5001</code></div>
                <div class="s-ex"><span class="s-tag">tunnel</span><code>ssh -L 5001:127.0.0.1:5001 host</code></div>
            </div>
            <p class="s-desc">Phone: SSH/Mosh in (Blink / Termius), then
            <code>tmux attach</code> for the live panes.</p>
        </section>
        </div>

        <div class="settings-tabpanel" data-tab="timing">
        <section class="settings-section" id="settings-timing" data-keywords="interval cadence tick poll seconds daemon">
            <h4>Timing</h4>
            <p class="s-desc">Daemon and dispatcher cadences. Blank a field to fall back to
            its <code>CHELA_*</code> env var (or the built-in default, shown as its
            placeholder). Takes effect on the <strong>next tick</strong>, no restart, except
            the status-feed timeout/TTL pair (marked below), which the dashboard/daemon
            process reads once at startup. A knob whose env var is currently set is disabled
            here. <strong>env always wins</strong>, so an edit would be silently discarded.</p>
            <div id="timing-rows" class="s-timing-rows"><div class="s-desc">Loading…</div></div>
            <div class="s-row">
                <button class="btn-accent" onclick="chela.saveTiming()">Save</button>
            </div>
            <div id="timing-msg" class="s-savemsg"></div>
        </section>
        </div>

        <div class="settings-tabpanel" data-tab="dispatch">
        <section class="settings-section" id="settings-concurrency" data-keywords="agents judges at once concurrency parallel memory">
            <h4>Concurrency</h4>
            <p class="s-desc">How many agents and judges run at once. Applies on the dispatcher's
            next tick, no restart. Lowering never stops a running agent or judge: it only holds
            new claims until the count drops under the limit.</p>
            <div id="concurrency-rows" class="s-dispatch-rows"><div class="s-desc">Loading…</div></div>
            <div id="concurrency-msg" class="s-savemsg"></div>
        </section>
        <section class="settings-section" id="settings-dispatch" data-keywords="judge critic merge workflow concurrency retries">
            <h4>Dispatch</h4>
            <p class="s-desc">Dispatcher, judge, and critic policy. Blank a field to fall back
            to its <code>CHELA_*</code> env var (or the built-in default, shown as its
            placeholder). Four of these (<strong>Dispatch workflows</strong>,
            <strong>Judge</strong>, <strong>Critic</strong>, and <strong>Merge base</strong>,
            marked <span class="s-badge off" style="display:inline-block">restart</span>
            below) are read once when the daemon/dashboard starts, so a save there takes
            effect on the <strong>next restart</strong>, not immediately. The rest apply on the
            next tick. A knob whose env var is currently set is disabled here.
            <strong>env always wins</strong>, so an edit would be silently discarded.</p>
            <div id="dispatch-rows" class="s-dispatch-rows"><div class="s-desc">Loading…</div></div>
            <div class="s-row">
                <button class="btn-accent" onclick="chela.saveDispatch()">Save</button>
            </div>
            <div id="dispatch-msg" class="s-savemsg"></div>
        </section>
        </div>

        <div class="settings-tabpanel" data-tab="notifications">
        <section class="settings-section" data-keywords="ntfy telegram webhook push alert ping">
            <h4>Needs-input notifications</h4>
            <p class="s-desc">Fires a one-shot ping when an agent's pane enters
            <code>waiting</code> (blocked on a prompt or question).</p>
            <div class="s-row" data-keywords="notifications popup alert mute awaiting_review">
                <span class="s-rowlabel">Review toasts</span>
                <select id="run-toasts-select" class="s-select" onchange="chela.setRunToastsMuted(this.value)">
                    <option value="show"${runToastsMuted ? '' : ' selected'}>Show</option>
                    <option value="muted"${runToastsMuted ? ' selected' : ''}>Muted</option>
                </select>
            </div>
            <p class="s-desc">Pop a dashboard toast when a dispatcher run turns
            <code>awaiting_review</code> (or done / failed), so you learn a run
            needs review without watching the board.</p>
            <div class="s-row" data-keywords="notifications browser approval override release pending">
                <span class="s-rowlabel">Approval requests</span>
                <select id="approval-notify-select" class="s-select" onchange="chela.setApprovalNotify(this.value).then(on => { this.value = on ? 'on' : 'off'; })">
                    <option value="off"${approvalNotify ? '' : ' selected'}>Off</option>
                    <option value="on"${approvalNotify ? ' selected' : ''}>Browser notification</option>
                </select>
            </div>
            <p class="s-desc">A browser notification when a new override or release request
            waits for your approval (the ⚖️ pill shows it either way). Off by default; your
            browser asks for permission once.</p>
            <p class="s-desc">Set on the daemon (env), then restart <code>chela run</code>:</p>
            <div class="s-kv"><code>CHELA_NOTIFY_URL</code><span>ntfy / Telegram / webhook (auto-detected)</span></div>
            <div class="s-examples">
                <div class="s-ex"><span class="s-tag">ntfy</span><code>https://ntfy.sh/your-topic</code></div>
                <div class="s-ex"><span class="s-tag">Telegram</span><code>https://api.telegram.org/bot&lt;token&gt;/sendMessage?chat_id=&lt;id&gt;</code></div>
                <div class="s-ex"><span class="s-tag">webhook</span><span class="s-exnote">any URL, receives JSON <code>{title,message,event}</code></span></div>
            </div>
        </section>
        </div>

        <div class="settings-tabpanel" data-tab="cost">
        <section class="settings-section" id="settings-cost" data-keywords="spend money usd dollars budget billing tokens usage limit rate cache">
            <h4>Cost</h4>
            <div class="work-toolbar">
                <div class="work-seg" id="cost-view" role="group" aria-label="Cost or usage">
                    <button type="button" class="work-seg-btn cost-view-btn" data-view="cost" aria-pressed="true"
                            onclick="chela.setCostView('cost')">Cost</button>
                    <button type="button" class="work-seg-btn cost-view-btn" data-view="usage" aria-pressed="false"
                            onclick="chela.setCostView('usage')">Usage</button>
                </div>
            </div>
            <div id="cost-pane">
            <p class="s-desc">Fleet spend from the cost each agent's statusLine hook already
            reports (<code>cost.total_cost_usd</code>), with no separate accounting, just a read
            over data chela ingests anyway. Grouped by project, same convention the sidebar
            uses for "what project is this agent".</p>
            <div class="work-toolbar">
                <div class="work-seg" id="cost-window" role="group" aria-label="Cost window">
                    <button type="button" class="work-seg-btn cost-window-btn" data-win="live" aria-pressed="true"
                            onclick="chela.setCostWindow('live')">Live</button>
                    <button type="button" class="work-seg-btn cost-window-btn" data-win="today" aria-pressed="false"
                            onclick="chela.setCostWindow('today')">Today</button>
                    <button type="button" class="work-seg-btn cost-window-btn" data-win="7d" aria-pressed="false"
                            onclick="chela.setCostWindow('7d')">7d</button>
                    <button type="button" class="work-seg-btn cost-window-btn" data-win="30d" aria-pressed="false"
                            onclick="chela.setCostWindow('30d')">30d</button>
                </div>
            </div>
            <div id="cost-table"><div class="s-desc">Loading…</div></div>
            </div>
            <div id="usage-pane" hidden>
            <p class="s-desc">Tokens, not dollars, read from EVERY Claude Code transcript:
            judges, subagents, dispatched agents, background sessions, and any extra root
            listed below, so a session with no statusLine still shows up. Limits come from the
            freshest statusLine <code>rate_limits</code>.</p>
            <div class="usage-limits" id="usage-limits"></div>
            <div class="work-toolbar">
                <div class="work-seg" id="usage-window" role="group" aria-label="Usage window">
                    <button type="button" class="work-seg-btn usage-window-btn" data-win="30m" aria-pressed="true"
                            onclick="chela.setUsageWindow('30m')">Last 30 min</button>
                    <button type="button" class="work-seg-btn usage-window-btn" data-win="today" aria-pressed="false"
                            onclick="chela.setUsageWindow('today')">Today (UTC)</button>
                </div>
            </div>
            <div id="usage-table"><div class="s-desc">Loading…</div></div>
            <p class="s-desc">Transcript roots: this host's own Claude Code projects dir plus
            the extra roots set by <code>usage_extra_roots</code> in <code>~/.chela/config.json</code>
            or <code>CHELA_USAGE_EXTRA_ROOTS</code> (absolute globs; <code>[]</code> scans none).</p>
            <dl class="usage-roots" id="usage-roots"></dl>
            </div>
        </section>
        </div>

        <div class="settings-tabpanel" data-tab="appearance">
        <section class="settings-section">
            <h4>Theme</h4>
            <div class="s-row" data-keywords="theme dark light colour color mode">
                <span class="s-rowlabel">Appearance</span>
                <select id="theme-select" class="s-select" onchange="chela.setTheme(this.value)">
                    ${THEMES
                        .map(t => `<option value="${t}"${theme === t ? ' selected' : ''}>${THEME_LABELS[t]}</option>`)
                        .join('')}
                </select>
            </div>
        </section>

        <section class="settings-section">
            <h4>Terminal font</h4>
            <p class="s-desc">Applies live to every open terminal, saved per browser.
            Pick the <strong>English</strong> (monospace) and <strong>Hebrew</strong> faces
            independently. Only <strong>Miriam Mono</strong> keeps Hebrew on the grid; the
            other Hebrew faces are proportional: nicer letters, slight drift in the fixed cells.</p>
            <div class="s-row" data-keywords="terminal typeface monospace latin">
                <span class="s-rowlabel">English font</span>
                <select id="term-latin-select" class="s-select" onchange="chela.setTermLatin(this.value)">
                    ${Object.keys(TERM_LATIN_LABELS)
                        .map(k => `<option value="${k}"${termLatin === k ? ' selected' : ''}>${TERM_LATIN_LABELS[k]}</option>`)
                        .join('')}
                </select>
            </div>
            <div class="s-row" data-keywords="terminal typeface rtl">
                <span class="s-rowlabel">Hebrew font</span>
                <select id="term-font-select" class="s-select" onchange="chela.setTermFont(this.value)">
                    ${Object.keys(TERM_FONT_LABELS)
                        .map(k => `<option value="${k}"${termFont === k ? ' selected' : ''}>${TERM_FONT_LABELS[k]}</option>`)
                        .join('')}
                </select>
            </div>
            <div class="s-row" data-keywords="terminal font size zoom px">
                <span class="s-rowlabel">Size</span>
                <select id="term-size-select" class="s-select" onchange="chela.setTermSize(this.value)">
                    ${['8', '10', '11', '12', '13', '14', '15', '16', '18']
                        .map(s => `<option value="${s}"${termSize === s ? ' selected' : ''}>${s}px</option>`)
                        .join('')}
                </select>
            </div>
        </section>
        </div>

        <div class="settings-tabpanel" data-tab="collaboration">
        <section class="settings-section">
            <h4>Collaboration</h4>
            <p class="s-desc">Your display name in shared terminals (presence pills +
            the pane facepile). Leave blank for a stable auto-name. Saved per browser,
            applies live.</p>
            <div class="s-row" data-keywords="nickname username presence share identity">
                <span class="s-rowlabel">Display name</span>
                <input id="collab-name" class="s-input" type="text" maxlength="24"
                       autocomplete="off" placeholder="${escHtml(collabAuto)}"
                       value="${attrEsc(collabName)}" oninput="chela.setCollabName(this.value)">
            </div>
            <div class="s-row" data-keywords="e2e encrypted server worker">
                <span class="s-rowlabel">Relay</span>
                <code id="collab-relay" style="overflow-wrap:anywhere;font-size:11px">…</code>
            </div>
            <p class="s-desc">End-to-end encrypted. The relay (<code>CHELA_COLLAB_RELAY</code>)
            is a zero-knowledge fan-out that only ever sees ciphertext (keys are derived in your
            browser from the pairing code). It does see room names + traffic timing (metadata), so
            run your own relay for full metadata privacy.</p>
        </section>
        <section class="settings-section" id="settings-share-typing">
            <h4>Guest typing</h4>
            <p class="s-desc">Shares are <strong>view only</strong> unless this is on. With it on,
            a share may let the guest type, but only into a <strong>sandboxed session</strong>
            (New session → Sandboxed: a container that sees only the project), checked live on
            every keystroke. Turning it off stops typing on live shares at once. See
            <code>docs/SHARE_SANDBOX.md</code> and run its checklist first.</p>
            <div class="s-row" data-keywords="share typing keyboard input write guest sandbox sandboxed container view only unsandboxed full access">
                <label class="s-rowlabel" for="share-typing-toggle">Guest typing: let share guests type into sandboxed sessions</label>
                <input id="share-typing-toggle" type="checkbox" role="switch" disabled
                       onchange="chela.setShareTyping(this.checked)">
            </div>
            <div id="share-typing-msg" class="s-savemsg"></div>
            <p class="s-desc" id="share-typing-source"></p>
        </section>
        </div>`;
    selectSettingsTab(focus === 'notify' ? 'notifications' : _settingsTab);
    _loadProjectsSetting();
    _loadCollabSetting();
    _loadAgentModeSetting();
    _loadAgentModelSetting();
    _loadRemoteControlSetting();
    _loadShareTypingSetting();
    _loadFileDropSetting();
    _loadTimingSettings();
    _loadDispatchSettings();
    _loadConcurrencySettings();
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
    let html = `In effect: <code>claude --permission-mode ${escHtml(eff)}</code> (from ${src}).`;
    if (overrides.length) {
        html += ' <strong>Overridden</strong> for ' + overrides.map(o =>
            `<code>${escHtml(o.workflow)}</code> (<code>${escHtml(o.cmd)}</code>)`).join(', ') +
            ' (a workflow that pins <code>agent.cmd</code> wins over this setting.)';
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
    } catch (e) { setMsg('err', 'Save failed. Mode unchanged.'); return; }
    // api() resolves on a 4xx too, so the server's rejection arrives as a body,
    // not a throw. Fail closed: report it and re-read the mode that IS stored.
    if (!cfg || cfg.error) {
        setMsg('err', 'Rejected. Mode unchanged.');
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
    el.innerHTML = `Coding agents launch with <code>--model ${escHtml(eff)}</code> (from ${src}).`;
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
    } catch (e) { setMsg('err', 'Save failed. Model unchanged.'); return; }
    // api() resolves on a 4xx too, so a server rejection arrives as a body, not
    // a throw. Fail closed: report it and re-read the model that IS stored.
    if (!cfg || cfg.error) {
        setMsg('err', 'Rejected. Model unchanged.');
        _loadAgentModelSetting();
        return;
    }
    setMsg('ok', 'Saved · next dispatch launches with --model ' + (cfg.agent_model_effective || v));
    _renderAgentModelSource(cfg);
}

// Remote Control (CMX-382): `--remote-control` on NEW human-facing sessions. Same
// env-wins presentation as the Timing tab — when CHELA_REMOTE_CONTROL is set, the
// server resolves to it regardless of config.json, so the switch shows the effective
// value but is disabled rather than offering an edit that would be silently discarded.
function _renderRemoteControl(cfg) {
    const box = document.getElementById('remote-control-toggle');
    const src = document.getElementById('remote-control-source');
    if (!box) return;
    const on = !!(cfg && cfg.remote_control);
    const locked = !!(cfg && cfg.remote_control_env_locked);
    box.checked = on;
    box.disabled = locked;
    if (src) {
        const env = escHtml((cfg && cfg.remote_control_env) || 'CHELA_REMOTE_CONTROL');
        const from = locked ? `set by <code>${env}</code>; env wins, unset it to edit here`
            : (cfg && cfg.remote_control_source === 'dashboard') ? 'this setting'
            : 'the built-in default';
        src.innerHTML = `In effect: <strong>${on ? 'On' : 'Off'}</strong> (${from}).`;
    }
}

async function _loadRemoteControlSetting() {
    const box = document.getElementById('remote-control-toggle');
    if (!box) return;
    let cfg;
    try {
        cfg = await api('/api/config');
    } catch (e) { box.disabled = true; return; }
    _renderRemoteControl(cfg);
}

async function setRemoteControl(on) {
    const msg = document.getElementById('remote-control-msg');
    const setMsg = (cls, t) => { if (msg) { msg.className = 's-savemsg ' + cls; msg.textContent = t; } };
    setMsg('', 'Saving…');
    let cfg;
    try {
        cfg = await api('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ remote_control: !!on }),
        });
    } catch (e) { setMsg('err', 'Save failed. Unchanged.'); _loadRemoteControlSetting(); return; }
    // api() resolves on a 4xx too — fail closed: report it and re-read what IS stored.
    if (!cfg || cfg.error) {
        setMsg('err', 'Rejected. Unchanged.');
        _loadRemoteControlSetting();
        return;
    }
    setMsg('ok', 'Saved · new sessions launch with Remote Control ' + (cfg.remote_control ? 'on' : 'off'));
    _renderRemoteControl(cfg);
}

// Guest typing (CMX-403): the `share_typing` switch. Same env-wins presentation as
// Remote Control above — CHELA_SHARE_TYPING set ⇒ shown, but disabled.
function _renderShareTyping(cfg) {
    const box = document.getElementById('share-typing-toggle');
    const src = document.getElementById('share-typing-source');
    if (!box) return;
    const on = !!(cfg && cfg.share_typing);
    const locked = !!(cfg && cfg.share_typing_env_locked);
    box.checked = on;
    box.disabled = locked;
    if (src) {
        const env = escHtml((cfg && cfg.share_typing_env) || 'CHELA_SHARE_TYPING');
        const from = locked ? `set by <code>${env}</code>; env wins, unset it to edit here`
            : (cfg && cfg.share_typing_source === 'dashboard') ? 'this setting'
            : 'the built-in default';
        src.innerHTML = `In effect: <strong>${on ? 'On' : 'Off'}</strong> (${from}).`;
    }
}

async function _loadShareTypingSetting() {
    const box = document.getElementById('share-typing-toggle');
    if (!box) return;
    let cfg;
    try {
        cfg = await api('/api/config');
    } catch (e) { box.disabled = true; return; }
    _renderShareTyping(cfg);
}

async function setShareTyping(on) {
    const msg = document.getElementById('share-typing-msg');
    const setMsg = (cls, t) => { if (msg) { msg.className = 's-savemsg ' + cls; msg.textContent = t; } };
    setMsg('', 'Saving…');
    let cfg;
    try {
        cfg = await api('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ share_typing: !!on }),
        });
    } catch (e) { setMsg('err', 'Save failed. Unchanged.'); _loadShareTypingSetting(); return; }
    if (!cfg || cfg.error) {
        setMsg('err', 'Rejected. Unchanged.');
        _loadShareTypingSetting();
        return;
    }
    setMsg('ok', 'Saved · guest typing ' + (cfg.share_typing ? 'allowed into sandboxed sessions' : 'off, every share is view only'));
    _renderShareTyping(cfg);
}

// File drop into terminals (CMX-412): the `file_drop` switch. ON by default; same
// env-wins presentation as Remote Control — CHELA_FILE_DROP set ⇒ shown, but disabled.
function _renderFileDrop(cfg) {
    const box = document.getElementById('file-drop-toggle');
    const src = document.getElementById('file-drop-source');
    const cap = document.getElementById('file-drop-cap');
    if (!box) return;
    const on = !!(cfg && cfg.file_drop);
    const locked = !!(cfg && cfg.file_drop_env_locked);
    box.checked = on;
    box.disabled = locked;
    if (cap && cfg && cfg.upload_max_mb) cap.textContent = String(cfg.upload_max_mb);
    if (src) {
        const env = escHtml((cfg && cfg.file_drop_env) || 'CHELA_FILE_DROP');
        const from = locked ? `set by <code>${env}</code>; env wins, unset it to edit here`
            : (cfg && cfg.file_drop_source === 'dashboard') ? 'this setting'
            : 'the built-in default';
        src.innerHTML = `In effect: <strong>${on ? 'On' : 'Off'}</strong> (${from}).`;
    }
}

async function _loadFileDropSetting() {
    const box = document.getElementById('file-drop-toggle');
    if (!box) return;
    let cfg;
    try {
        cfg = await api('/api/config');
    } catch (e) { box.disabled = true; return; }
    _renderFileDrop(cfg);
    _renderTelegramTopics(cfg);
}

// Telegram topics (CMX-204): the delete-on-close switch and the one-shot cleanup. The
// bridge makes the Telegram calls on its next tick; the dashboard only stores the ask.
function _renderTelegramTopics(cfg) {
    const box = document.getElementById('tg-delete-toggle');
    const btn = document.getElementById('tg-cleanup-btn');
    box.checked = !!cfg.telegram_delete_on_close;
    box.disabled = false;
    const n = cfg.telegram_archived_topics || 0;
    btn.textContent = cfg.telegram_cleanup_pending ? 'Deleting archived topics…' : `Delete archived topics (${n})`;
    btn.disabled = !n || !!cfg.telegram_cleanup_pending;
}

async function _postTelegramTopics(payload, okText) {
    const msg = document.getElementById('tg-topics-msg');
    try {
        const cfg = await api('/api/config', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!cfg || cfg.error) { msg.textContent = 'Rejected. Unchanged.'; return; }
        msg.textContent = okText;
        _renderTelegramTopics(cfg);
    } catch (e) { msg.textContent = 'Save failed. Unchanged.'; }
}

function setTelegramDelete(on) {
    return _postTelegramTopics({ telegram_delete_on_close: !!on }, 'Saved · delete on close ' + (on ? 'on' : 'off'));
}

function deleteArchivedTopics() {
    if (!confirm('Delete every archived Telegram topic chela remembers? This deletes their messages too and cannot be undone.')) return;
    return _postTelegramTopics({ telegram_cleanup: true }, 'Requested. The Telegram bridge deletes them on its next tick.');
}

async function setFileDrop(on) {
    const msg = document.getElementById('file-drop-msg');
    const setMsg = (cls, t) => { if (msg) { msg.className = 's-savemsg ' + cls; msg.textContent = t; } };
    setMsg('', 'Saving…');
    let cfg;
    try {
        cfg = await api('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_drop: !!on }),
        });
    } catch (e) { setMsg('err', 'Save failed. Unchanged.'); _loadFileDropSetting(); return; }
    if (!cfg || cfg.error) {
        setMsg('err', 'Rejected. Unchanged.');
        _loadFileDropSetting();
        return;
    }
    setMsg('ok', 'Saved · file drop into terminals ' + (cfg.file_drop ? 'on' : 'off'));
    _renderFileDrop(cfg);
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
        _renderUpdateStatus(null);
        return;
    }
    const sections = (data && data.sections) || [];
    if (!sections.length) { host.innerHTML = '<div class="s-desc">No status.</div>'; }
    else {
        host.innerHTML = sections.map(sec => `
            <div class="s-status-group">
                <div class="s-status-grouphead">${escHtml(sec.title || '')}</div>
                ${(sec.items || []).map(_statusRowHtml).join('')}
            </div>`).join('');
    }
    _renderUpdateStatus(data && data.update);
}

// The "Update" section — CMX-199. `chela doctor` (repo.upstream_synced) and the daemon's
// hourly notify edge could both SAY the checkout fell behind; neither gave an operator
// anywhere to click, which is exactly how five merged PRs sat unpulled for a full day
// with every chela-* service still serving what it last loaded. This is that control.
function _renderUpdateStatus(upd) {
    const row = document.getElementById('update-status-row');
    const btn = document.getElementById('update-apply-btn');
    if (!row) return;
    if (!upd || !upd.ok) {
        const detail = upd && (upd.error || upd.note) ? escHtml(upd.error || upd.note) : 'unavailable';
        row.innerHTML = `<span class="s-status-badge off"><span class="s-status-dot" aria-hidden="true">○</span>Unknown</span>
            <span class="s-status-detail">${detail}</span>`;
        if (btn) { btn.disabled = true; btn.hidden = true; }
        return;
    }
    const behind = upd.behind || 0;
    const stale = upd.stale_services || [];
    const unknown = upd.unknown_services || [];
    // CMX-56: a service whose start commit / code set couldn't be read is neither stale
    // nor fresh — say so instead of folding it into either.
    const unknownNote = unknown.length
        ? ` · freshness unknown for ${escHtml(unknown.join(', '))}` : '';
    if (btn) { btn.dataset.mode = ''; btn.hidden = false; }
    if (behind > 0) {
        row.innerHTML = `<span class="s-status-badge off"><span class="s-status-dot" aria-hidden="true">○</span>${behind} behind</span>
            <span class="s-status-detail">branch ${escHtml(upd.branch || '')}: ${behind} commit(s) unpulled; running services are still serving what they last loaded</span>`;
        if (btn) { btn.disabled = false; btn.textContent = 'Update now'; btn.dataset.mode = 'update'; }
    } else if (stale.length) {
        // The checkout itself is fully synced (nothing to pull) — but a bare `git pull`
        // run by hand, bypassing this control, can leave running services on the OLD
        // code. Stale is import-aware (CMX-56): only a service whose OWN code changed
        // since it started is listed. The button then restarts exactly these.
        row.innerHTML = `<span class="s-status-badge off"><span class="s-status-dot" aria-hidden="true">○</span>${stale.length} stale</span>
            <span class="s-status-detail">branch ${escHtml(upd.branch || '')}: nothing to pull, but code that ${escHtml(stale.join(', '))} ${stale.length === 1 ? 'runs' : 'run'} changed after ${stale.length === 1 ? 'it' : 'they'} started (or <code class="s-cmd">pm2 restart ${stale.map(n => `<span class="s-cmd-arg">${escHtml(n)}</span>`).join(' ')}</code>)${unknownNote}</span>`;
        if (btn) {
            btn.disabled = false;
            btn.textContent = `Restart stale services (${stale.length})`;
            btn.dataset.mode = 'restart';
        }
    } else if (upd.stale_ok === false) {
        // CMX-95: which services run old code could not be read — Unknown, never "Up to date".
        row.innerHTML = `<span class="s-status-badge off"><span class="s-status-dot" aria-hidden="true">○</span>Unknown</span>
            <span class="s-status-detail">branch ${escHtml(upd.branch || '')}: nothing to pull, but could not read which services run old code (check <code class="s-cmd">pm2 ls</code>)</span>`;
        if (btn) { btn.disabled = true; btn.hidden = true; }
    } else {
        row.innerHTML = `<span class="s-status-badge on"><span class="s-status-dot" aria-hidden="true">●</span>Up to date</span>
            <span class="s-status-detail">branch ${escHtml(upd.branch || '')}: nothing to pull${unknownNote}</span>`;
        // CMX-95: nothing to pull and nothing stale — offer no button at all.
        if (btn) { btn.disabled = true; btn.hidden = true; }
    }
}

async function applyUpdate() {
    const btn = document.getElementById('update-apply-btn');
    const msg = document.getElementById('update-apply-msg');
    const setMsg = (cls, t) => { if (msg) { msg.className = 's-savemsg ' + cls; msg.textContent = t; } };
    const restartOnly = !!(btn && btn.dataset.mode === 'restart');
    const idleLabel = btn ? btn.textContent : 'Update now';
    if (!confirm(restartOnly
        ? 'Nothing to pull. Re-sync deps and restart only the stale chela-* services?'
        : 'Pull, re-sync, and restart every running chela-* service (including this dashboard)?')) return;
    if (btn) { btn.disabled = true; btn.textContent = restartOnly ? 'Restarting…' : 'Updating…'; }
    setMsg('', 'Starting…');
    let resp;
    try {
        resp = await api('/api/update/apply', { method: 'POST' });
    } catch (e) {
        setMsg('err', 'Request failed. The dashboard may have already restarted; refresh to check.');
        return;
    }
    if (!resp || resp.error || resp.ok === false) {
        setMsg('err', (resp && resp.error) || 'Update refused.');
        if (btn) { btn.disabled = false; btn.textContent = idleLabel; }
        return;
    }
    if (!resp.started) {
        setMsg('ok', resp.detail || 'Already up to date.');
        _loadSettingsStatus();
        return;
    }
    // CMX-56: every click ends in a message — the route's own `detail` names what it
    // started ("restarting chela-dashboard", "pulling 3 commit(s)…").
    const what = resp.detail ? resp.detail.charAt(0).toUpperCase() + resp.detail.slice(1) : 'Started';
    setMsg('ok', `${what}…`);
    // CMX-95: follow it to an outcome, not "refresh in a few seconds" forever (the dashboard
    // is usually NOT restarted, so nothing would reload). A new `boot` = it was; reload.
    const step = await followUpdate(resp, s => {
        if (s.state === 'polling' || s.state === 'reload') setMsg('', s.text);
    }, _updateFlowDeps);
    if (step.state === 'reload') return;
    setMsg(step.state === 'done' ? 'ok' : 'err', step.text);
    if (step.state === 'done') { _loadSettingsStatus(); return; }
    if (btn) { btn.disabled = false; btn.textContent = idleLabel; }
}

// Test seam: tests/settings_update.test.mjs swaps the poll's clock/sleep/fetch.
const _updateFlowDeps = {};

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

// Timing tab (CMX-217): the Daemon-loop-intervals knob group, the first proof
// of the general dashboard-setting precedence layer (chela.config.dashboard_setting
// / TIMING_KNOBS — env wins over the dashboard). One row per knob — stored value
// in the field, the effective (env/default-resolved) value as its placeholder,
// same idiom as projects-dir above — and a single Save button that POSTs every
// row in one batch, which /api/config/timing validates atomically (all-or-nothing)
// before applying any.
//
// A knob whose env var is currently winning (`k.source === 'env'`) renders its
// field DISABLED, showing the effective value rather than an editable one — env
// always wins server-side, so an editable field here would silently discard
// whatever the user typed. `saveTiming()` below excludes disabled fields from
// the POST for the same reason: without that, every Save would re-persist the
// env value into config.json as a "dashboard" value nobody chose, which would
// then surface the moment the env var was later unset.
function _renderTimingRows(knobs) {
    const box = document.getElementById('timing-rows');
    if (!box) return;
    box.innerHTML = (knobs || []).map(k => {
        const isEnv = k.source === 'env';
        const badges =
            (isEnv ? ` <span class="s-badge on" title="Set by ${attrEsc(k.env)}; env wins over the dashboard.">env</span>` : '') +
            (k.restart_required ? ' <span class="s-badge off" title="Takes effect after a daemon/dashboard restart, not the next tick.">restart</span>' : '');
        const input = isEnv
            ? `<input class="s-input s-timing-input" type="number" step="any"
                   data-timing-key="${attrEsc(k.key)}" value="${attrEsc(String(k.effective))}" disabled
                   title="Overridden by ${attrEsc(k.env)}; dashboard edits are ignored while it's set.">`
            : `<input class="s-input s-timing-input" type="number" step="any"
                   data-timing-key="${attrEsc(k.key)}"
                   placeholder="${attrEsc(String(k.default))}"
                   value="${k.stored !== '' && k.stored !== undefined ? attrEsc(String(k.stored)) : ''}">`;
        return `
        <div class="s-row">
            <span class="s-rowlabel">${escHtml(k.label)}${badges}</span>
            ${input}
            <span class="s-rowunit">${escHtml(k.unit || '')}</span>
        </div>`;
    }).join('');
    if (_settingsQuery) _applySettingsSearch();
}

async function _loadTimingSettings() {
    const box = document.getElementById('timing-rows');
    if (!box) return;
    let body;
    try {
        body = await api('/api/config/timing');
    } catch (e) { box.innerHTML = '<div class="s-desc">(unavailable)</div>'; return; }
    if (!body || !body.knobs) { box.innerHTML = '<div class="s-desc">(unavailable)</div>'; return; }
    _renderTimingRows(body.knobs);
}

async function saveTiming() {
    const msg = document.getElementById('timing-msg');
    const setMsg = (cls, t) => { if (msg) { msg.className = 's-savemsg ' + cls; msg.textContent = t; } };
    const inputs = document.querySelectorAll('#timing-rows .s-timing-input');
    const payload = {};
    // Disabled = env-overridden (see _renderTimingRows) — never post those, or
    // every Save would re-persist the env value into config.json unasked.
    inputs.forEach(inp => { if (!inp.disabled) payload[inp.dataset.timingKey] = inp.value.trim(); });
    setMsg('', 'Saving…');
    let body;
    try {
        body = await api('/api/config/timing', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
    } catch (e) { setMsg('err', 'Save failed. Nothing changed.'); return; }
    // api() resolves on a 4xx too (the rejection arrives as a body, not a throw) —
    // the whole batch is atomic server-side, so an error here means NONE of the
    // fields changed, not just the bad one.
    if (!body || body.error) {
        const detail = body && body.errors
            ? Object.entries(body.errors).map(([k, v]) => k + ': ' + v).join('; ')
            : 'rejected';
        setMsg('err', 'Nothing saved: ' + detail);
        _loadTimingSettings();     // re-show what's actually stored
        return;
    }
    setMsg('ok', 'Saved.');
    _renderTimingRows(body.knobs);
}

// Dispatch tab (CMX-220): docs/SETTINGS_UI_INVENTORY.md's second group
// ("Dispatch / judge / critic policy"). Same batch-save idiom as Timing above,
// but the knobs are NOT all plain positive numbers — `k.kind` picks the control:
// "bool" -> a 3-way select (Default/On/Off, since blank must mean "fall back to
// env/default", not "off"), "text"/"size" -> a text input, "number" -> a numeric
// input, same as Timing's rows.
function _renderDispatchRows(knobs) {
    const box = document.getElementById('dispatch-rows');
    if (!box) return;
    box.innerHTML = (knobs || []).map(k => {
        const isEnv = k.source === 'env';
        const badges =
            (isEnv ? ` <span class="s-badge on" title="Set by ${attrEsc(k.env)}; env wins over the dashboard.">env</span>` : '') +
            (k.restart_required ? ' <span class="s-badge off" title="Takes effect after a daemon/dashboard restart, not the next tick.">restart</span>' : '');
        const envTitle = isEnv ? `title="Overridden by ${attrEsc(k.env)}; dashboard edits are ignored while it's set."` : '';
        let input;
        if (k.kind === 'bool') {
            const stored = k.stored !== '' && k.stored !== undefined ? !!k.stored : null;
            input = `<select class="s-select s-dispatch-input" data-dispatch-key="${attrEsc(k.key)}"
                        ${isEnv ? 'disabled' : ''} ${envTitle}>
                     <option value=""${stored === null ? ' selected' : ''}>Default (${k.default ? 'on' : 'off'})</option>
                     <option value="true"${(isEnv ? k.effective === true : stored === true) ? ' selected' : ''}>On</option>
                     <option value="false"${(isEnv ? k.effective === false : stored === false) ? ' selected' : ''}>Off</option>
                     </select>`;
        } else {
            const type = k.kind === 'number' ? 'number' : 'text';
            const step = type === 'number' ? ' step="any"' : '';
            const numCls = k.kind === 'number' ? ' s-num' : '';
            input = isEnv
                ? `<input class="s-input s-dispatch-input${numCls}" type="${type}"${step}
                       data-dispatch-key="${attrEsc(k.key)}" value="${attrEsc(String(k.effective))}" disabled ${envTitle}>`
                : `<input class="s-input s-dispatch-input${numCls}" type="${type}"${step}
                       data-dispatch-key="${attrEsc(k.key)}"
                       placeholder="${attrEsc(String(k.default))}"
                       value="${k.stored !== '' && k.stored !== undefined ? attrEsc(String(k.stored)) : ''}">`;
        }
        return `
        <div class="s-row">
            <span class="s-rowlabel">${escHtml(k.label)}${badges}</span>
            ${input}
            <span class="s-rowunit">${escHtml(k.unit || '')}</span>
        </div>`;
    }).join('');
    if (_settingsQuery) _applySettingsSearch();
}

// Concurrency rows (CMX-164): "Agents at once" per workflow + "Judges at once". Each
// stepper click POSTs at once; the server validates (1 to 8) and logs the change.
function _concurrencyRow(label, value, note, args, resettable) {
    return `
        <div class="s-row">
            <span class="s-rowlabel">${escHtml(label)}</span>
            <button class="btn" onclick="chela.setConcurrency(${args}, -1)" aria-label="Decrease">−</button>
            <span class="s-num">${escHtml(String(value))}</span>
            <button class="btn" onclick="chela.setConcurrency(${args}, 1)" aria-label="Increase">+</button>
            ${resettable ? `<button class="btn" onclick="chela.setConcurrency(${args}, 0)">Reset</button>` : ''}
        </div>
        <div class="s-desc">${escHtml(note)}</div>`;
}

let _concurrency = null;
function _renderConcurrency(body) {
    _concurrency = body;
    const j = body.judges;
    document.getElementById('concurrency-rows').innerHTML = body.workflows.map((w, i) => _concurrencyRow(
        `Agents at once: ${w.name}`, w.effective, workflowNote(body, w), `'agents', ${i}`, w.override != null)).join('')
        + _concurrencyRow('Judges at once', j.effective, `Source: ${j.source}. ` + memNote(body, j.effective),
            `'judges', 0`, j.source === 'settings');
    if (_settingsQuery) _applySettingsSearch();
}

async function _loadConcurrencySettings() {
    try { _renderConcurrency(await api('/api/config/concurrency')); }
    catch (e) { document.getElementById('concurrency-rows').innerHTML = '<div class="s-desc">(unavailable)</div>'; }
}

async function setConcurrency(kind, idx, delta) {
    const msg = document.getElementById('concurrency-msg');
    try {
        const body = await api('/api/config/concurrency', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(stepPayload(_concurrency, kind, idx, delta)),
        });
        if (body.error) { msg.textContent = 'Not saved: ' + body.error; return; }
        msg.textContent = '';
        _renderConcurrency(body);
    } catch (e) { msg.textContent = 'Save failed. Nothing changed.'; }
}

async function _loadDispatchSettings() {
    const box = document.getElementById('dispatch-rows');
    if (!box) return;
    let body;
    try {
        body = await api('/api/config/dispatch');
    } catch (e) { box.innerHTML = '<div class="s-desc">(unavailable)</div>'; return; }
    if (!body || !body.knobs) { box.innerHTML = '<div class="s-desc">(unavailable)</div>'; return; }
    _renderDispatchRows(body.knobs);
}

async function saveDispatch() {
    const msg = document.getElementById('dispatch-msg');
    const setMsg = (cls, t) => { if (msg) { msg.className = 's-savemsg ' + cls; msg.textContent = t; } };
    const inputs = document.querySelectorAll('#dispatch-rows .s-dispatch-input');
    const payload = {};
    // Disabled = env-overridden — never post those, same reasoning as saveTiming().
    inputs.forEach(inp => { if (!inp.disabled) payload[inp.dataset.dispatchKey] = inp.value.trim(); });
    setMsg('', 'Saving…');
    let body;
    try {
        body = await api('/api/config/dispatch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
    } catch (e) { setMsg('err', 'Save failed. Nothing changed.'); return; }
    if (!body || body.error) {
        const detail = body && body.errors
            ? Object.entries(body.errors).map(([k, v]) => k + ': ' + v).join('; ')
            : 'rejected';
        setMsg('err', 'Nothing saved: ' + detail);
        _loadDispatchSettings();     // re-show what's actually stored
        return;
    }
    setMsg('ok', 'Saved.');
    _renderDispatchRows(body.knobs);
}

// The Settings > Appearance picker, in order. Each key needs a style.css
// `body[data-theme=…]` block (except `dark`, the :root default) AND a terminal
// palette in chela/dashboard/term_themes.py — tests/test_term_bg_sync.py fails
// if the three lists drift.
const THEME_LABELS = {
    dark: 'Dark', dim: 'Dim', midnight: 'Midnight', nord: 'Nord',
    gruvbox: 'Gruvbox', solarized: 'Solarized', rose: 'Rosé Pine', warm: 'Warm',
};
const THEMES = Object.keys(THEME_LABELS);

// CMX-381: a theme covers the terminals too. Each ttyd iframe carries every
// theme's xterm palette (app.py _term_theme_shim) and reads `chela_theme` from
// the same-origin localStorage. Writing it fires a `storage` event in each
// iframe (the shim listens), but we ALSO poke every open terminal directly for
// instant feedback, same as the font prefs below — live, no ttyd restart.
function setTheme(t) {
    localStorage.setItem('chela_theme', t);
    document.body.dataset.theme = t;
    applyTermThemeToIframes();
}

function applyTermThemeToIframes() {
    document.querySelectorAll('iframe').forEach(f => {
        try {
            const w = f.contentWindow;
            if (w && typeof w.chelaApplyTermTheme === 'function') w.chelaApplyTermTheme();
        } catch (e) { /* not-yet-loaded — its shim reads chela_theme on load */ }
    });
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

// Mute / unmute the dispatcher run-state review toasts (work.js reads this key).
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

// --- Command palette (⌘K / Ctrl-K) ----------------------------------------
// One fuzzy jump-to for everything: agents (→ detail), views, project launches,
// and a couple of global actions. The fastest way to navigate once you're past a
// handful of sessions. Built fresh each open from live caches.
let _palItems = [], _palSel = 0;

// skipWids (optional): sessions to leave out of the "session ·" rows below —
// used with an EMPTY query so a pane already floated to the top (CMX-116's
// `_openPaneItems`) doesn't also show up a second time further down the list.
function _paletteItems(skipWids) {
    const items = [];
    // The palette's own hardcoded copy of the view list is gone — it reads the
    // registry, so a view added or removed there is added or removed here.
    paletteViews(VIEWS, _viewCtx()).forEach(v => items.push({
        icon: lucideIcon('layout-grid'), title: v.label, sub: 'view', run: () => selectView(v.id),
    }));

    (_agentsCache || []).forEach(a => {
        if (skipWids && a.window_id && skipWids.has(a.window_id)) return;
        const word = _AGENT_STATUS_WORD[agentDotColor(a)] || 'idle';
        // CMX-417: the window id rides the sub-line, so it shows on the row, and
        // `wid` is scored on its own in _renderPalette ("@3" lists @3, @31, @32…
        // however long the session's name is).
        items.push({ wid: a.window_id, dot: _SIDEBAR_DOT_CLASS[agentDotColor(a)] || 'idle', title: _agentLabel(a),
                     sub: 'session · ' + word + (a.window_id ? ' · ' + a.window_id : ''),
                     run: () => selectAgent(a.name) });
    });

    // Share / Stop sharing per live session (same server flag as the pane button).
    if (TERMINALS_ON) {
        (_agentsCache || []).forEach(a => {
            if (!a.window_id) return;
            const shared = isShared(a.window_id);
            items.push({ icon: shared ? lucideIcon('x') : lucideIcon('share-2'),
                         title: (shared ? 'Stop sharing ' : 'Share ') + _agentLabel(a),
                         sub: shared ? 'shared session' : 'session',
                         run: () => { if (shared) stopShare(a.window_id); else shareSession(a.window_id); } });
        });
    }

    const _launcherData = launcher.get();
    if (TERMINALS_ON && _launcherData) {
        const seen = new Set();
        [...(_launcherData.favorites || []), ...(_launcherData.recent || [])].forEach(e => {
            if (!e || seen.has(e.path)) return;
            seen.add(e.path);
            items.push({ icon: lucideIcon('play'), title: 'Launch ' + (e.label || e.path), sub: 'project',
                         run: () => launchProject(e.path) });
        });
    }

    items.push({ icon: lucideIcon('terminal'), title: 'New shell window', sub: 'action', run: () => newShellWindow() });
    // CMX-6: create a Linear issue (the New-task dialog, frontend/src/features/newtask).
    items.push({ icon: lucideIcon('plus'), title: 'New task', sub: 'action · Linear issue', run: () => openNewTask() });
    items.push({ icon: lucideIcon('clock'), title: 'Add scheduled task', sub: 'action', run: () => showAddSchedule() });
    // CMX-121: the injected keybinds (Alt+1..9, ⌘K) have no other discovery path —
    // this is the ONLY entry point (palette-only, deliberately no dedicated global
    // keybind — see index.html's #shortcuts-overlay comment).
    items.push({ icon: lucideIcon('keyboard'), title: 'Keyboard shortcuts', sub: 'help', run: () => openShortcuts() });
    // CMX-401: on a phone the sidebar's "Jump to session" box is the palette, and
    // there is no Ctrl+, to reach Settings — this row is the way in. Open-only:
    // toggleSettings() on an already-open drawer would close it.
    items.push({ icon: lucideIcon('settings'), title: 'Settings', sub: 'action', run: () => {
        const d = document.getElementById('settings-drawer');
        if (!(d && d.classList.contains('open'))) toggleSettings();
    } });
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

// CMX-116: palette-first pane switcher. On an EMPTY query, float every live,
// unminimized wall pane to the TOP — wall order (shared/store/wall.ts `orderedWids`), with a pane
// wanting attention (waiting > working > idle) bubbling ahead of its peers,
// same status word the sidebar uses — so ⌘K instantly answers "what's open,
// what needs me". Once the user types, this section is gone: `_paletteItems()`
// alone (agents already included there) drives the usual fuzzy match over
// everything, so a query never traps you in "open panes only".
const _PANE_ATTENTION_RANK = { yellow: 0, green: 1, grey: 2 };

function _openPaneItems() {
    if (!TERMINALS_ON || typeof _renderedWids === 'undefined') return [];
    const live = _renderedWids.filter(w => !minimizedPanes.get().has(w));
    if (!live.length) return [];
    const wallOrder = orderedWids(live);
    const rankOf = wid => {
        const a = (_agentsCache || []).find(x => x.window_id === wid);
        return _PANE_ATTENTION_RANK[a ? agentDotColor(a) : 'grey'] ?? 2;
    };
    // Array.prototype.sort is stable — same-rank panes keep wall order.
    const byAttention = wallOrder.slice().sort((a, b) => rankOf(a) - rankOf(b));
    return byAttention.map(wid => {
        const a = (_agentsCache || []).find(x => x.window_id === wid);
        const word = a ? (_AGENT_STATUS_WORD[agentDotColor(a)] || 'idle') : 'open';
        return {
            wid, dot: a ? (_SIDEBAR_DOT_CLASS[agentDotColor(a)] || 'idle') : 'idle',
            title: a ? _agentLabel(a) : wid,
            sub: 'open pane · ' + word,
            run: () => focusPaneByWid(wid),
        };
    });
}

// CMX-417: a query that is exactly an open window's id ("@32") → one row that
// JUMPS to that window, pinned above the fuzzy matches (which would otherwise
// rank @320 alongside it). Jumps, never toggles: selectAgent minimizes a pane
// that is already open, which is the wrong answer to "take me to @32". Any open
// window counts — a plain shell has no /api/agents row but is still `@N`.
function _windowIdItem(q) {
    const known = (_agentsCache || []).map(a => a.window_id).filter(Boolean);
    const rendered = (TERMINALS_ON && typeof _renderedWids !== 'undefined') ? _renderedWids : [];
    const wid = resolveWindowId(q, known.concat(rendered));
    if (!wid) return null;
    const a = (_agentsCache || []).find(x => x.window_id === wid);
    return {
        wid, dot: a ? (_SIDEBAR_DOT_CLASS[agentDotColor(a)] || 'idle') : 'idle',
        title: a ? _agentLabel(a) : wid,
        sub: 'window · ' + wid,
        run: () => {
            if (TERMINALS_ON && typeof focusPaneByWid === 'function') {
                if (!isWallVisible()) setTermMode('wall');
                focusPaneByWid(wid);
            } else if (a) {
                showAgentDetail(a.name);
            }
        },
    };
}

function _renderPalette(q) {
    const list = document.getElementById('palette-list');
    if (!list) return;
    let items, paneCount = 0;
    if (q) {
        // CMX-417: a row's window id is scored on its own too — inside the full
        // "title sub" haystack, a long name's first-match penalty would push an
        // "@3" match below zero and drop it.
        const exact = _windowIdItem(q);
        items = _paletteItems()
            .filter(it => !(exact && it.wid === exact.wid))   // the exact row below replaces it
            .map(it => ({ it, sc: Math.max(_fuzzyScore(q, it.title + ' ' + it.sub), it.wid ? _fuzzyScore(q, it.wid) : -1) }))
            .filter(x => x.sc >= 0)
            .sort((a, b) => b.sc - a.sc)
            .map(x => x.it);
        if (exact) items.unshift(exact);
    } else {
        const panes = _openPaneItems();
        paneCount = panes.length;
        items = panes.concat(_paletteItems(new Set(panes.map(p => p.wid))));
    }
    _palItems = items;
    if (_palSel >= items.length) _palSel = 0;
    list.innerHTML = items.map((it, i) => {
        const divider = (paneCount > 0 && i === paneCount)
            ? '<div class="palette-divider" role="separator"></div>' : '';
        const icon = it.dot
            ? `<span class="term-status-dot ${it.dot}"></span>`
            : `<span class="pi-glyph">${it.icon || ''}</span>`;
        return divider + `<div class="palette-item${i === _palSel ? ' sel' : ''}" data-i="${i}"
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

// CMX-377: the sidebar's own "Jump to session" input (index.html, ahead of nav —
// the mockup's own placement). Reuses the EXISTING fuzzy-jump machinery
// (#palette/_renderPalette/_palItems) rather than a second implementation —
// this is deliberately NOT openPalette(): that clears #palette-input and steals
// focus into it on every call, which would fight the sidebar input for focus on
// every keystroke. Instead this opens the SAME overlay in place (idempotent —
// only forces it open, never re-clears an already-open one) and renders
// straight off the sidebar input's own live value; the global keydown listener
// below (ArrowUp/ArrowDown/Enter/Escape) is gated on `#palette.open`, not on
// which element has focus, so navigating the results needs no extra wiring —
// typing stays in the sidebar field the whole time.
function sidebarJumpInput(el) {
    const ov = document.getElementById('palette');
    if (!ov) return;
    if (!ov.classList.contains('open')) { ov.classList.add('open'); _palSel = 0; }
    _renderPalette(el.value);
}
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

function _isSettingsKey(e) {
    return (e.metaKey || e.ctrlKey) && !e.shiftKey && !e.altKey && e.key === ',';
}
function _settingsOpen() {
    const modal = document.getElementById('settings-drawer');
    return !!(modal && modal.classList.contains('open'));
}

document.addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        const ov = document.getElementById('palette');
        (ov && ov.classList.contains('open')) ? closePalette() : openPalette();
        return;
    }
    // CMX-385: Ctrl/⌘+, toggles Settings, like VS Code. `e.key === ','` only, and
    // no Shift/Alt — Shift+Ctrl+, stays free. A focused pane's keydown never
    // reaches here; app.py's _TERM_PALETTE_KEY_SHIM catches it inside the iframe
    // and calls chela.toggleSettings() the same way.
    if (_isSettingsKey(e)) {
        e.preventDefault();
        toggleSettings();
        return;
    }
    // The shortcuts cheatsheet (CMX-121) is a plain, static overlay — Esc is its
    // only keyboard wire (no arrow-key selection, nothing to run). Checked before
    // the palette's own open-check below so Esc closes whichever overlay is on top.
    const scOv = document.getElementById('shortcuts-overlay');
    if (scOv && scOv.classList.contains('open')) {
        if (e.key === 'Escape') { e.preventDefault(); closeShortcuts(); }
        return;
    }
    const ov = document.getElementById('palette');
    if (!ov || !ov.classList.contains('open')) {
        // Esc closes Settings — only once the palette/cheatsheet (which can sit
        // on top of it) are out of the way, so one Esc closes one layer.
        // CMX-396: with a query in the search box, the first Esc clears it; only
        // an Esc on an empty query closes the drawer.
        if (e.key === 'Escape' && _settingsOpen()) {
            e.preventDefault();
            if (_settingsQuery) clearSettingsSearch();
            else toggleSettings();
        }
        return;
    }
    if (e.key === 'Escape') { e.preventDefault(); closePalette(); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); _palMove(1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); _palMove(-1); }
    else if (e.key === 'Enter') { e.preventDefault(); _palRun(_palSel); }
});

// --- Keyboard shortcuts cheatsheet (CMX-121) --------------------------------
// Palette-only (⌘K → "Keyboard shortcuts", wired in _paletteItems above) — no
// dedicated global keybind, see index.html's #shortcuts-overlay comment. Content
// is static markup in index.html (nothing here is config-driven), so open/close
// only toggle the overlay's visibility class.
function openShortcuts() {
    const ov = document.getElementById('shortcuts-overlay');
    if (ov) ov.classList.add('open');
}
function closeShortcuts() {
    const ov = document.getElementById('shortcuts-overlay');
    if (ov) ov.classList.remove('open');
}

// Apply the saved theme immediately on load.
document.body.dataset.theme = localStorage.getItem('chela_theme') || 'dark';

// --- Stage 0: ES-module exports ---
export { closeShortcuts, openPalette, openShortcuts, renderNav, selectView };

// --- Stage 0: window.chela — surface reachable from inline HTML handlers ---
window.chela = window.chela || {};
Object.assign(window.chela, { _updateFlowDeps, applyUpdate, clearSettingsSearch, closePalette, closeShortcuts, openPalette, openShortcuts, _palRun, _renderPalette, saveDispatch, saveProjectsDir, saveTiming, selectAgent, setConcurrency, setTelegramDelete, deleteArchivedTopics, selectSettingsTab, selectView, setAgentModel, setAgentPermissionMode, setCollabName, setFileDrop, setRemoteControl, setRunToastsMuted, setApprovalNotify, setShareTyping, setTermFont, setTermLatin, setTermSize, setTheme, settingsSearch, sidebarJumpInput, toggleSettings });
