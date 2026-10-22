// --- Stage 0: ES-module imports ---
import { $$, TERMINALS_ON, _agentsCache, agentDotColor, setCurrentTab, updateTabSignal, wantsHuman } from './util';
import { onOrchestratorChange, orchestratorState } from '../../frontend/src/shared/orchestrator';
import { refreshSummary } from '../../frontend/src/features/primarymenu';
import { detailAgent, redrawAgentDetail, setAgentDetailHost } from '../../frontend/src/features/agentdetail';
import { _displayLabel, adoptSidebarDefault, _minimized, _orderedWids, _renderedWids, focusPaneByWid, isWallVisible, minimizePane, setTermMode } from './terminals';
import { launchProject, setLauncherHost } from '../../frontend/src/features/launcher';
import { setPaletteHost } from '../../frontend/src/features/palette';
import { VIEWS } from './views';
import { findView, navViews, otherViews, paletteViews, panelId } from './viewreg';
import { refresh } from '../../frontend/src/shared/scheduler';
import { resolveWindowId } from './windowid';
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
    onWall: () => _renderedWids.filter(w => !_minimized.has(w)),
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
            const openOnWall = _renderedWids.includes(wid) && !_minimized.has(wid);
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

// --- Agent detail view -----------------------------------------------------
// The agent-detail island (frontend/src/features/agentdetail, CMX-219) draws it; what only
// this module knows (the view switch, the role, the attention dot) it reads through its host.
setAgentDetailHost({ selectView, roleLabel: a => _ROLE_LABEL[_agentRole(a)], dotColor: agentDotColor });
// A launch lands on the Wall and the sidebar the sidebar controller owns (features/launcher, CMX-219).
setLauncherHost({ focusPaneByWid, selectView, refreshSidebar });

// --- Command palette (CMX-221: frontend/src/features/palette) ---------------------------
// What only this module and the wall know, the palette reads through its host.
setPaletteHost({
    views: () => paletteViews(VIEWS, _viewCtx()),
    label: _agentLabel,
    dotColor: agentDotColor,
    selectView,
    selectAgent,
    showAgentDetail,
    resolveWid: resolveWindowId,
    wall: {
        rendered: () => _renderedWids,
        minimized: () => _minimized,
        ordered: _orderedWids,
        visible: isWallVisible,
        showWall: () => setTermMode('wall'),
        focus: focusPaneByWid,
    },
});

// --- Stage 0: ES-module exports ---
export { renderNav, selectView };

// --- Stage 0: window.chela — surface reachable from inline HTML handlers ---
window.chela = window.chela || {};
Object.assign(window.chela, { selectAgent, selectView });
