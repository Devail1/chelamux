// --- Stage 0: ES-module imports ---
import { $, $$, TERMINALS_ON, _agentProject, _agentsCache, ageStr, agentDotColor, api, attrEsc, currentTab, escHtml, lucideIcon, setAgentsCache, setCurrentTab, shortTime, updateTabSignal, wantsHuman } from './util.js';
import { onOrchestratorChange, orchestratorState } from './orchestrator.js';
import { refreshSummary } from './header.js';
import { checkContext } from './agents.js';
import { showAddSchedule } from './schedules.js';
import { _displayLabel, _minimized, _orderedWids, _renderedWids, _sharedWids, _stopShare, focusPaneByWid, isWallVisible, minimizePane, setTermMode, shareBtnClick } from './terminals.js';
import { _launcherData, launchProject, refreshLauncher } from './launcher.js';
import { VIEWS } from './views.js';
import { findView, navViews, otherViews, paletteViews, panelId } from './viewreg.js';
import { refresh } from './main.js';
import { refreshCostTab } from './usage.js';
import { resolveWindowId } from './windowid.js';
import { DISPATCHED_KEY, ENV_KINDS, GROUP_MODES, SORTS, VIEW_ACTIVITY, VIEW_STATUS, groupSidebar, isArchivable, moveGroup, normalizeView, primaryWindow, runLabel, runState } from './sidebarmodel.js';

// ---------------------------------------------------------------------------
// Sidebar + canvas navigation (replaces the old tab bar)
//
// The canvas is a set of .panel elements (one per view) kept from the tab
// layout, so every existing renderer (renderKanban -> #kanban-board,
// renderTerminals -> the wall, ...) works unchanged. `currentTab` (declared in
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
// style.css); .mobile-menu-fab's hamburger slides it in over a scrim (CMX-377 —
// there is no topbar; the sidebar's OWN #btn-menu toggle is unreachable while
// the drawer is closed, which is exactly why the floating fab exists).
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
    // CMX-377: .mobile-menu-fab (the off-canvas drawer's phone-only opener,
    // since the sidebar's own toggle is unreachable while it's closed) hides
    // itself off this body class while the drawer is open, so the two
    // triggers never render on top of each other.
    document.body.classList.toggle('sidebar-open', open);
    const fab = document.getElementById('btn-menu-mobile');
    if (fab) fab.setAttribute('aria-expanded', open ? 'true' : 'false');
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
    // The agent-detail view has no nav item of its own (it is reached from the
    // always-visible sidebar Sessions list, not a nav tab), so nothing in
    // #side-nav lights up while drilled into one — `navView` never matches a
    // real data-view when `view` is 'agent-detail'.
    const navView = view === 'agent-detail' ? null : view;
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

// Friendly label, shared with the wall panes (_displayLabel): a manual rename
// wins, else Claude's session title, else the window name (CMX-62) — so a
// session reads identically in the sidebar and on its pane title. Falls back to
// the raw name when terminals.js isn't loaded.
function _agentLabel(a) {
    if (typeof _displayLabel === 'function' && a && a.window_id) return _displayLabel(a.window_id);
    return a ? a.name : '';
}

// --- Per-viewer sidebar state (CMX-35) ---------------------------------------
// Collapsed groups, the group order (Move up / Move down), the archived rows and the
// "show archived" toggle are this VIEWER's, so they live in localStorage — and every
// read and write is wrapped: a private window, blocked site data or a throwing
// accessor must leave a sidebar that still renders (just one that forgets).
const SB_COLLAPSED_KEY = 'chela_grp_collapsed';
const SB_ORDER_KEY = 'chela_sb_group_order';
const SB_ARCHIVED_KEY = 'chela_sb_archived';
// CMX-66: the VIEW menu's choices ({status, env, activity, groupBy, sort, showEmpty,
// showPR} — sidebarmodel.normalizeView) and the custom groups ({groups: [{id, name}],
// assign: {itemKey: id}}). Part 1's "show archived" toggle is now Status ▸ All.
const SB_VIEW_KEY = 'chela_sb_view';
const SB_CUSTOM_KEY = 'chela_sb_custom_groups';

// `mem` is the session-only stand-in, used ONLY while storage throws: the list
// rebuilds on every tick, so without it a click on a blocked-storage page would undo
// itself on the next render. When storage works, storage is the whole truth.
function _lsRead(key, mem) {
    try { return localStorage.getItem(key); }
    catch { return mem; }
}
function _lsList(key, mem) {
    const raw = _lsRead(key, mem == null ? null : JSON.stringify(mem));
    try {
        const v = JSON.parse(raw || '[]');
        return Array.isArray(v) ? v : [];
    } catch { return []; }
}
function _lsSet(key, value) {
    try { localStorage.setItem(key, typeof value === 'string' ? value : JSON.stringify(value)); }
    catch { /* storage unavailable — the in-memory copy carries it for this session */ }
}

let _memCollapsed = null;
function _collapsedGroups() { return new Set(_lsList(SB_COLLAPSED_KEY, _memCollapsed)); }
function _saveCollapsed(s) {
    _memCollapsed = [...s];
    _lsSet(SB_COLLAPSED_KEY, _memCollapsed);
}
function toggleGroup(key) {
    const s = _collapsedGroups();
    if (s.has(key)) s.delete(key); else s.add(key);
    _saveCollapsed(s);
    renderSidebarAgents(_agentsCache || []);
}

let _memOrder = null;
function _groupOrder() { return _lsList(SB_ORDER_KEY, _memOrder); }
let _memArchived = null;
function _archivedSet() { return new Set(_lsList(SB_ARCHIVED_KEY, _memArchived)); }
function _saveArchived(s) {
    _memArchived = [...s];
    _lsSet(SB_ARCHIVED_KEY, _memArchived);
}
function _lsObj(key, mem) {
    const raw = _lsRead(key, mem == null ? null : JSON.stringify(mem));
    try {
        const v = JSON.parse(raw || 'null');
        return v && typeof v === 'object' && !Array.isArray(v) ? v : null;
    } catch { return null; }
}

let _memView = null;
function _view() { return normalizeView(_lsObj(SB_VIEW_KEY, _memView)); }
function _saveView(v) {
    _memView = normalizeView(v);
    _lsSet(SB_VIEW_KEY, _memView);
}

let _memCustom = null;
function _custom() {
    const c = _lsObj(SB_CUSTOM_KEY, _memCustom) || {};
    const groups = Array.isArray(c.groups)
        ? c.groups.filter(g => g && typeof g.id === 'string' && typeof g.name === 'string') : [];
    const assign = c.assign && typeof c.assign === 'object' && !Array.isArray(c.assign) ? c.assign : {};
    return { groups, assign };
}
function _saveCustom(c) {
    _memCustom = { groups: c.groups, assign: c.assign };
    _lsSet(SB_CUSTOM_KEY, _memCustom);
}

// The last render's model — what the group/row menus act on (they open off a click on
// what is on screen, so they read the same grouping the screen was drawn from).
let _sb = { groups: [], needsYou: [], items: new Map() };

// An item's display label: a run reads `CMX-37 · <title>`; a window reads its CMX-62
// label (manual name > Claude's title > window name).
function _itemLabel(it) {
    return it.kind === 'run' ? runLabel(it) : _agentLabel(it.agent);
}

// One sidebar row. `a` is the window the row opens; `o` overrides what a plain window
// row would show — a run row passes its own label, state and tooltip, and a `badge`
// slot (empty here) is where later parts hang a PR / judge-progress badge (CMX-66/67).
//
// The row face is the desktop's: a status SHAPE, then ONE line — the title, truncated
// with an ellipsis — and the state word kept small and dim at the right. ctx% and the
// window id stay in the row (.ar-sub, CMX-230/CMX-417) but only show on hover, or when
// the context is running hot; the relative time moved into the tooltip.
function _agentRowHtml(a, o = {}) {
    const dot = agentDotColor(a);
    const active = a.name === _detailAgent ? ' active' : '';
    // `done` (issue #475): a REGULAR session that is idle AND has an assistant
    // turn since you last spoke — computed server-side (inbox.is_done) from the
    // transcript, not guessed here. `a.done` is only ever true when `dot` is
    // already 'grey' (app.py gates it on session_status !== 'busy' && !needs_human),
    // so this never fights the working/waiting colours.
    const done = isDone(a);
    const stWord = o.stWord || (done ? 'done' : (_AGENT_STATUS_WORD[dot] || 'idle'));
    const stCls = o.stCls || (done ? 'done' : (_SIDEBAR_DOT_CLASS[dot] || 'idle'));
    const label = o.label || _agentLabel(a);

    // Open-on-wall cue: a click on this row RESTORES a hidden pane vs merely
    // FOCUSES one already visible — worth knowing before you click. True only when
    // the pane is both rendered (on the wall, not just known to /api/agents) and not
    // minimized to the dock.
    const onWall = TERMINALS_ON && !!a.window_id
        && _renderedWids.includes(a.window_id) && !_minimized.has(a.window_id);
    const wallCls = onWall ? ' on-wall' : '';

    const c = a.window_id ? _ctxByWid[a.window_id] : null;
    let ctxChip = '';
    let ctxHot = false;
    if (c && c.used_pct != null) {
        const p = Math.round(c.used_pct);
        const cls = p > 80 ? 'danger' : p > 60 ? 'warn' : '';
        ctxHot = !!cls;
        ctxChip = `<span class="ar-ctx ${cls}" title="context ${p}%">${p}%</span>`;
    }

    // CMX-377: the row's relative time. recap_ts is the only per-agent timestamp the
    // API carries. Absent (no recap yet) -> no time shown, not a fabricated one.
    let age = '';
    if (a.recap_ts) age = ageStr((Date.now() - new Date(a.recap_ts)) / 1000).replace(' ago', '');
    const ago = age ? `<span class="ar-ago">${escHtml(age)}</span>` : '';

    // "<state> · <ctx%> ctx · @N" — .ar-state and .ar-ctx keep their exact markup
    // (tests/dashboard_scale_nav_a11y.test.mjs's non-hue-cue GUARD 3b/GUARD 4), and the
    // text reads the same as before; the ctx and id parts are wrapped only so the CSS
    // can keep them off the one-line row face until hover. CMX-417: the tmux window id
    // is the `@N` that `chela peek`, inbox notices and peer messages use.
    const sub = `<span class="ar-state ${stCls}">${stWord}</span>`
        + (ctxChip ? `<span class="ar-more ar-ctx-part${ctxHot ? ' hot' : ''}"> · ${ctxChip} ctx</span>` : '')
        + (a.window_id ? `<span class="ar-more"> · <span class="ar-wid">${escHtml(a.window_id)}</span></span>` : '');

    const type = _agentType(a);
    // CMX-146's ai_title / the away_summary recap ride the row's tooltip.
    const extra = [a.ai_title, a.recap].filter(Boolean).join(' — ');
    // CMX-62: when Claude's title leads the row, the short window NAME (the key
    // `chela peek/msg` and the bindings use) is the secondary label — on hover.
    const key = !o.label && label !== a.name ? a.name : '';
    const head = o.titleHead || [label + (key ? ` (${key})` : ''), a.window_id].filter(Boolean).join(' · ');
    const tail = [o.titleExtra || extra, age ? `${age} ago` : ''].filter(Boolean).join('\n');
    const rowTitle = tail ? `${head}\n${tail}` : head;

    const wallSuffix = onWall ? ' — open on the wall' : '';
    // Every row has a menu (right-click, or its ⋯ on touch): a run's opens its agent or
    // judge pane, and every row can be moved to a custom group (CMX-66). Keyed on the
    // row's item key — the window id / run id, never the label.
    const runAttr = (o.runId ? ` data-run="${attrEsc(o.runId)}"` : '')
        + (o.itemKey ? ` data-item="${attrEsc(o.itemKey)}" oncontextmenu="chela.openRowMenu(event, this.dataset.item)"` : '');
    const menuName = o.runId ? 'Run menu' : 'Row menu';
    const more = o.itemKey
        ? `<button class="row-more" title="${menuName}" aria-label="${menuName}" onclick="event.stopPropagation(); chela.openRowMenu(event, this.closest('.agent-row').dataset.item)">${lucideIcon('ellipsis', 14)}</button>`
        : '';
    return `<div class="agent-row rich${active}${wallCls}${o.cls ? ' ' + o.cls : ''}" data-agent="${attrEsc(a.name)}"${runAttr} title="${attrEsc(rowTitle + wallSuffix)}"
        onclick="chela.selectAgent(this.dataset.agent)">
        <span class="term-status-dot ${stCls}" title="${attrEsc(o.runId ? 'run' : type)} · ${stWord}"></span>
        <div class="ar-main">
            <span class="agent-row-name"${key ? ` data-key="${attrEsc(key)}" title="${attrEsc(`window: ${key}`)}"` : ''}>${escHtml(label)}</span>
            <div class="ar-sub">${sub}${o.badge || ''}</div>
        </div>
        ${ago}${more}
    </div>`;
}

// A dispatched RUN's one row (CMX-35): its agent window and its judge window are ONE
// row, labelled `CMX-37 · <title>`, showing the RUN's state (working / judging /
// rework / awaiting review / needs human). A click opens the agent pane (the judge's
// once the agent has finished); the row menu also offers the judge pane.
function _runRowHtml(it, extraCls, showPR) {
    const a = primaryWindow(it);
    const st = runState(it, wantsHuman);
    const label = runLabel(it);
    const wins = [it.agent && `agent ${it.agent.name} · ${it.agent.window_id}`,
        it.judge && `judge ${it.judge.name} · ${it.judge.window_id}`].filter(Boolean).join('\n');
    return _agentRowHtml(a, {
        label, stWord: st.word, stCls: st.shape, runId: it.run.task_id, itemKey: it.key,
        titleHead: `${label} — ${st.word}`, titleExtra: wins,
        cls: ['run-row', extraCls].filter(Boolean).join(' '),
        badge: showPR ? _prBadgeHtml(it) : '',
    });
}

// The PR badge on a dispatched run's row (View ▸ Show PR status, CMX-66): `#N` from the
// run's own pr_url, with its state (open / draft / merged / closed) when a window's
// transcript names the same PR (app.py's `a.pr`, already state-resolved). No PR yet → none.
const _PR_NUM_RE = /\/pull\/(\d+)/;
function _prBadgeHtml(it) {
    const url = it.run && it.run.pr_url;
    const m = url ? _PR_NUM_RE.exec(String(url)) : null;
    if (!m) return '';
    const pr = it.windows.map(w => w && w.pr).find(p => p && p.url === url) || null;
    const state = pr ? (pr.draft && pr.state === 'open' ? 'draft' : pr.state) : '';
    const known = ['open', 'draft', 'merged', 'closed'].includes(state);
    const title = `PR #${m[1]}${known ? ` · ${state}` : ''}`;
    return `<span class="ar-pr${known ? ' ' + state : ''}" data-pr="${attrEsc(m[1])}" title="${attrEsc(title)}">#${escHtml(m[1])}</span>`;
}

function _itemRowHtml(it, archived, showPR = true) {
    const cls = archived ? 'archived' : '';
    if (it.kind === 'run') return _runRowHtml(it, cls, showPR);
    return _agentRowHtml(it.agent, { itemKey: it.key, ...(cls ? { cls } : {}) });
}

// A folder group's header — the desktop's quiet header: dim small folder name, a `>`
// chevron, and at the right a ⋯ (the group menu, for touch: there is no right-click on
// a phone) and a `+` (a new session in THIS folder). Right-click opens the same menu.
function _groupHtml(g, collapsed, archivedKeys, showPR) {
    if (g.flat) {
        // Group by ▸ None: one flat list, no header.
        const flatRows = g.items.map(it => _itemRowHtml(it, archivedKeys.has(it.key), showPR)).join('');
        return `<div class="side-group flat" data-g="${attrEsc(g.key)}"><div class="group-rows">${flatRows}</div></div>`;
    }
    const isColl = collapsed.has(g.key);
    const n = g.items.length;
    const count = g.key === DISPATCHED_KEY ? `<span class="group-count" title="${n} run${n === 1 ? '' : 's'}">${n}</span>` : '';
    const add = g.cwd
        ? `<button class="group-add" data-g="${attrEsc(g.key)}" title="New session in ${attrEsc(g.label)}" aria-label="New session in ${attrEsc(g.label)}"
             onclick="event.stopPropagation(); chela.groupNewSession(this.dataset.g)">${lucideIcon('plus', 14)}</button>`
        : '';
    const rows = g.items.map(it => _itemRowHtml(it, archivedKeys.has(it.key), showPR)).join('');
    const empty = !g.items.length ? ' empty' : '';
    return `<div class="side-group${isColl ? ' collapsed' : ''}${empty}" data-g="${attrEsc(g.key)}">
        <div class="group-head" data-g="${attrEsc(g.key)}" aria-expanded="${isColl ? 'false' : 'true'}"
             onclick="chela.toggleGroup(this.dataset.g)" oncontextmenu="chela.openGroupMenu(event, this.dataset.g)">
            <span class="group-name" title="${attrEsc(g.cwd || g.label)}">${escHtml(g.label)}</span>${count}
            <span class="group-caret">${lucideIcon('chevron-right', 12)}</span>
            <span class="group-actions">
                <button class="group-more" data-g="${attrEsc(g.key)}" title="Group menu" aria-label="${attrEsc(g.label)} menu"
                    onclick="event.stopPropagation(); chela.openGroupMenu(event, this.dataset.g)">${lucideIcon('ellipsis', 14)}</button>${add}
            </span>
        </div>
        <div class="group-rows">${rows}</div>
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

    // CMX-35: grouped by PROJECT FOLDER, like the desktop app (sidebarmodel.js). One
    // cluster sits above the folders, because it is about YOU, not a folder: Needs you —
    // rows blocked on a human. The orchestrator is not pinned (CMX-72): it sits in its
    // folder group like any other session. Each row shows in exactly one place. A dispatched run is ONE row, in the "Dispatched" group.
    //
    // CMX-66: the VIEW menu (the sliders button on the Sessions header) picks what shows
    // (Status / Environment / Last activity), how it groups (Date / Folder / State /
    // Custom groups / None) and how each group sorts — per viewer, in localStorage.
    const orchWid = orchestratorState().wid;
    const archived = _archivedSet();
    const view = _view();
    const custom = _custom();
    const model = groupSidebar(rows, {
        mode: view.groupBy, wants: wantsHuman, orchWid, order: _groupOrder(),
        archived, status: view.status, env: view.env, activityDays: VIEW_ACTIVITY[view.activity],
        now: Date.now(), sort: view.sort, labelOf: _itemLabel, showEmpty: view.showEmpty, custom,
    });
    const showArchived = view.status !== 'active';

    // An archived row that came back to life (busy, waiting) or whose window is gone
    // leaves the archive — so it is not silently re-hidden the next time it settles,
    // and a recycled @N never inherits an old row's archived flag.
    const shownArchived = new Set(model.hidden);
    const prune = [...archived].filter(k => !shownArchived.has(k));
    if (prune.length) { prune.forEach(k => archived.delete(k)); _saveArchived(archived); }
    // A custom-group assignment for a WINDOW that is gone is dropped (a recycled @N must
    // not inherit it); a run's (`run:CMX-N`) is kept — a run outlives its windows.
    const liveKeys = new Set(rows.filter(Boolean).map(a => `w:${a.window_id || a.name}`));
    const goneW = Object.keys(custom.assign).filter(k => k.startsWith('w:') && !liveKeys.has(k));
    if (goneW.length) { goneW.forEach(k => delete custom.assign[k]); _saveCustom(custom); }

    const items = new Map();
    for (const it of [...model.needsYou, ...model.groups.flatMap(g => [...g.items, ...g.archived])]) {
        items.set(it.key, it);
    }
    _sb = { ...model, items };

    let html = '';
    if (model.needsYou.length) {
        html += `<div class="side-triage side-needs-you">
            <div class="triage-head">Needs you <span class="triage-count">${model.needsYou.length}</span></div>
            ${model.needsYou.map(it => _itemRowHtml(it, false, view.showPR)).join('')}
        </div>`;
    }
    const collapsed = _collapsedGroups();
    for (const g of model.groups) html += _groupHtml(g, collapsed, shownArchived, view.showPR);
    if (!html) html = '<div class="side-empty">No sessions match this view</div>';
    if (model.hidden.length || showArchived) {
        const n = model.hidden.length;
        html += `<button class="side-archived-toggle" onclick="chela.toggleShowArchived()">${
            showArchived ? 'Hide archived' : `Show archived (${n})`}</button>`;
    }
    host.innerHTML = html;
}

// --- The group menu (CMX-35) -------------------------------------------------
// Right-click a group header, or tap its ⋯ (phones have no right-click). Grouped by
// dividers like the desktop's: New session · Move up / Move down · Collapse all /
// Expand all · Archive all (N).
//
// "Archive all" HIDES rows, it never kills a window: it hides the group's FINISHED rows
// (a `done` session, a window with no Claude left in it, a settled run — see
// sidebarmodel.isArchivable) from the sidebar, reversibly ("Show archived" at the foot
// of the list, and "Unarchive" here). A row that is working, blocked on you, the
// orchestrator or a judge mid-battery is never archived, and an archived row that wakes
// up comes back on its own.

function _menuEl(id) {
    let m = document.getElementById(id);
    if (!m) {
        m = document.createElement('div');
        m.id = id;
        m.className = 'popover side-menu';
        m.setAttribute('role', 'menu');
        m.style.display = 'none';
        document.body.appendChild(m);
    }
    return m;
}

function _menuItem(label, action, { disabled = false } = {}) {
    return `<div class="popover-item${disabled ? ' disabled' : ''}" role="menuitem"
        ${disabled ? 'aria-disabled="true"' : `data-act="${attrEsc(action)}"`}>${escHtml(label)}</div>`;
}

const _SEP = '<div class="popover-sep"></div>';

function _openMenu(m, ev, html, onAct) {
    if (ev) { ev.preventDefault(); ev.stopPropagation(); }
    hideSideMenus();
    m.innerHTML = html;
    m.onclick = e => {
        const el = e.target.closest('[data-act]');
        e.stopPropagation();
        if (!el) return;
        hideSideMenus();
        onAct(el.dataset.act);
    };
    m.style.display = 'block';
    const anchor = (ev && ev.currentTarget && ev.currentTarget.getBoundingClientRect) ? ev.currentTarget : null;
    if (anchor) placePopover(m, anchor, { gap: 4 });
    setTimeout(() => document.addEventListener('click', hideSideMenus, { once: true }), 0);
}

function hideSideMenus() {
    for (const id of ['group-menu', 'row-menu', 'view-menu']) {
        const m = document.getElementById(id);
        if (m) m.style.display = 'none';
    }
}

function _archivableIn(g) {
    const orchWid = orchestratorState().wid;
    return g.items.filter(it => isArchivable(it, { wants: wantsHuman, orchWid }));
}

function openGroupMenu(ev, key) {
    const groups = _sb.groups;
    const i = groups.findIndex(g => g.key === key);
    if (i < 0) return;
    const g = groups[i];
    const archivedHere = new Set(g.archived.map(it => it.key));
    const n = _archivableIn(g).filter(it => !archivedHere.has(it.key)).length;
    let html = '';
    if (g.cwd) html += _menuItem('New session', 'new') + _SEP;
    html += _menuItem('Move up', 'up', { disabled: i === 0 })
        + _menuItem('Move down', 'down', { disabled: i === groups.length - 1 })
        + _SEP
        + _menuItem('Collapse all', 'collapse-all')
        + _menuItem('Expand all', 'expand-all')
        + _SEP
        + _menuItem(`Archive all (${n})`, 'archive', { disabled: n === 0 });
    if (g.archived.length) html += _menuItem(`Unarchive all (${g.archived.length})`, 'unarchive');
    _openMenu(_menuEl('group-menu'), ev, html, act => groupMenuAction(key, act));
}

// The menu's actions, callable directly (tests and keyboard paths use the same entry).
function groupMenuAction(key, act) {
    const groups = _sb.groups;
    const g = groups.find(x => x.key === key);
    if (!g) return;
    if (act === 'new') return groupNewSession(key);
    if (act === 'up' || act === 'down') {
        const keys = groups.map(x => x.key);
        const next = moveGroup(keys, _groupOrder(), key, act === 'up' ? -1 : 1);
        if (next) { _memOrder = next; _lsSet(SB_ORDER_KEY, next); }
    } else if (act === 'collapse-all') {
        _saveCollapsed(new Set([..._collapsedGroups(), ...groups.map(x => x.key)]));
    } else if (act === 'expand-all') {
        const keys = new Set(groups.map(x => x.key));
        _saveCollapsed(new Set([..._collapsedGroups()].filter(k => !keys.has(k))));
    } else if (act === 'archive') {
        const s = _archivedSet();
        _archivableIn(g).forEach(it => s.add(it.key));
        _saveArchived(s);
    } else if (act === 'unarchive') {
        const s = _archivedSet();
        g.archived.forEach(it => s.delete(it.key));
        _saveArchived(s);
    }
    renderSidebarAgents(_agentsCache || []);
}

// The foot link: "Show archived (N)" is View ▸ Status ▸ All, "Hide archived" is back
// to Active.
function toggleShowArchived() {
    const v = _view();
    _saveView({ ...v, status: v.status === 'active' ? 'all' : 'active' });
    renderSidebarAgents(_agentsCache || []);
}

// `+` on a group header: a NEW session in that group's folder, through the launcher's
// own spawn path (/api/agents/spawn — `chela spawn` semantics, remote control per
// Settings). `fresh` skips the launcher's focus-the-existing-agent dedup: the header's
// `+` exists precisely to open another session where one already runs.
function groupNewSession(key) {
    const g = _sb.groups.find(x => x.key === key);
    if (!g || !g.cwd || typeof launchProject !== 'function') return;
    return launchProject(g.cwd, { fresh: true });
}

// A row's menu: a run's opens its agent or judge pane; every row can be moved to a
// custom group (CMX-66). `key` is the row's item key (a bare run id still resolves).
function openRowMenu(ev, key) {
    const it = _sb.items.get(key) || _sb.items.get(`run:${key}`);
    if (!it) return;
    let html = '';
    if (it.kind === 'run') {
        if (it.agent) html += _menuItem('Open agent pane', 'agent');
        html += _menuItem('Open judge pane', 'judge', { disabled: !it.judge }) + _SEP;
    }
    html += _menuItem('Move to group…', 'move');
    const m = _menuEl('row-menu');
    _openMenu(m, ev, html, act => {
        if (act === 'move') return _openMoveMenu(m, it.key);
        _openPane(act === 'judge' ? it.judge : it.agent);
    });
}

// "Move to group…" — a second page of the same popover (tap-reachable: no hover
// flyout): every custom group, Ungrouped, and New group…. The current one is ✓.
function _openMoveMenu(m, itemKey) {
    const c = _custom();
    const cur = c.assign[itemKey];
    const has = cur != null && c.groups.some(g => g.id === cur);
    const html = _vmBack('Move to group')
        + c.groups.map(g => _vmChoice(g.name, `to:${g.id}`, g.id === cur)).join('')
        + _vmChoice('Ungrouped', 'to:', !has)
        + _SEP + _menuItem('New group…', 'new');
    _showMenuPage(m, html, act => {
        if (act === 'back') return openRowMenu(null, itemKey);
        hideSideMenus();
        if (act === 'new') {
            const g = newCustomGroup();
            if (g) moveToGroup(itemKey, g.id);
            return;
        }
        if (act.startsWith('to:')) moveToGroup(itemKey, act.slice(3) || null);
    });
}

// Assign a row (by its STABLE item key) to custom group `id`, or to Ungrouped (null).
function moveToGroup(itemKey, id) {
    const c = _custom();
    if (id && c.groups.some(g => g.id === id)) c.assign[itemKey] = id;
    else delete c.assign[itemKey];
    _saveCustom(c);
    renderSidebarAgents(_agentsCache || []);
}

function _askName(msg, dflt = '') {
    try { return (window.prompt(msg, dflt) || '').trim(); }
    catch { return ''; }
}

function newCustomGroup(name) {
    const n = name != null ? String(name).trim() : _askName('New group name:');
    if (!n) return null;
    const c = _custom();
    const g = { id: `g${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`, name: n };
    c.groups.push(g);
    _saveCustom(c);
    renderSidebarAgents(_agentsCache || []);
    return g;
}

function renameCustomGroup(id, name) {
    const c = _custom();
    const g = c.groups.find(x => x.id === id);
    if (!g) return;
    const n = name != null ? String(name).trim() : _askName('Rename group:', g.name);
    if (!n) return;
    g.name = n;
    _saveCustom(c);
    renderSidebarAgents(_agentsCache || []);
}

// Deleting a group moves its rows to Ungrouped — it never hides or closes them.
function deleteCustomGroup(id) {
    const c = _custom();
    c.groups = c.groups.filter(g => g.id !== id);
    for (const [k, v] of Object.entries(c.assign)) if (v === id) delete c.assign[k];
    _saveCustom(c);
    renderSidebarAgents(_agentsCache || []);
}

// --- The VIEW menu (CMX-66) ----------------------------------------------------------
// The sliders button on the Sessions header — the desktop's view menu: Status /
// Environment / Last activity / Group by / Sort by (each a submenu showing its current
// value at the right, behind a chevron), Custom groups, and the Show empty groups / Show
// PR status toggles. A submenu is a second PAGE of the same popover (‹ back at its top),
// so it is reachable by tap on a phone — there is no hover-only flyout. Choosing keeps
// the menu open, so several choices take one trip.

const _STATUS_LABEL = { active: 'Active', all: 'All', archived: 'Archived' };
const _ENV_LABEL = { interactive: 'Interactive', dispatched: 'Dispatched agents', judge: 'Judges', background: 'Background sessions' };
const _ACTIVITY_LABEL = { '1d': '1d', '7d': '7d', '30d': '30d', all: 'All' };
const _GROUP_LABEL = { date: 'Date', folder: 'Folder', state: 'State', custom: 'Custom groups', none: 'None' };
const _SORT_LABEL = { activity: 'Last activity', name: 'Name', created: 'Created' };

const _CHECK = '<span class="vm-check" aria-hidden="true">✓</span>';

function _vmSub(label, value, act) {
    return `<div class="popover-item vm-item" role="menuitem" aria-haspopup="menu" data-act="${attrEsc(act)}">`
        + `<span class="vm-label">${escHtml(label)}</span><span class="vm-val">${escHtml(value)}</span>`
        + `<span class="vm-chev">${lucideIcon('chevron-right', 12)}</span></div>`;
}
function _vmChoice(label, act, on, role = 'menuitemradio') {
    return `<div class="popover-item vm-item${on ? ' on' : ''}" role="${role}" aria-checked="${on ? 'true' : 'false'}" data-act="${attrEsc(act)}">`
        + `<span class="vm-label">${escHtml(label)}</span>${on ? _CHECK : ''}</div>`;
}
function _vmBack(title) {
    return `<div class="popover-item vm-back" role="menuitem" data-act="back">`
        + `<span class="vm-chev back">${lucideIcon('chevron-right', 12)}</span><span class="vm-label">${escHtml(title)}</span></div>` + _SEP;
}

// Swap the open popover's content for another page, keeping it open and anchored.
function _showMenuPage(m, html, onAct) {
    m.innerHTML = html;
    m.onclick = e => {
        const el = e.target.closest('[data-act]');
        e.stopPropagation();
        if (el) onAct(el.dataset.act);
    };
    const placed = _placedPopovers.get(m);
    if (placed && placed.anchor.isConnected) placePopover(m, placed.anchor, placed.opts);
}

function _viewPageHtml(page) {
    const v = _view();
    if (page === 'status') {
        return _vmBack('Status') + VIEW_STATUS.map(k => _vmChoice(_STATUS_LABEL[k], `status:${k}`, v.status === k)).join('');
    }
    if (page === 'env') {
        return _vmBack('Environment')
            + ENV_KINDS.map(k => _vmChoice(_ENV_LABEL[k], `env:${k}`, v.env.includes(k), 'menuitemcheckbox')).join('');
    }
    if (page === 'activity') {
        return _vmBack('Last activity')
            + Object.keys(VIEW_ACTIVITY).map(k => _vmChoice(_ACTIVITY_LABEL[k], `activity:${k}`, v.activity === k)).join('');
    }
    if (page === 'group') {
        // None sits apart, below a divider, as on the desktop
        return _vmBack('Group by')
            + GROUP_MODES.filter(k => k !== 'none').map(k => _vmChoice(_GROUP_LABEL[k], `group:${k}`, v.groupBy === k)).join('')
            + _SEP + _vmChoice(_GROUP_LABEL.none, 'group:none', v.groupBy === 'none');
    }
    if (page === 'sort') {
        return _vmBack('Sort by') + SORTS.map(k => _vmChoice(_SORT_LABEL[k], `sort:${k}`, v.sort === k)).join('');
    }
    if (page === 'custom') {
        const c = _custom();
        let html = _vmBack('Custom groups');
        for (const g of c.groups) {
            html += `<div class="popover-item vm-item vm-group" role="none"><span class="vm-label">${escHtml(g.name)}</span>`
                + `<button class="vm-mini" role="menuitem" data-act="rename:${attrEsc(g.id)}">Rename</button>`
                + `<button class="vm-mini" role="menuitem" data-act="delete:${attrEsc(g.id)}">Delete</button></div>`;
        }
        if (!c.groups.length) html += _menuItem('No custom groups yet', '', { disabled: true });
        return html + _SEP + _menuItem('New group…', 'custom-new');
    }
    const n = v.env.length;
    return _vmSub('Status', _STATUS_LABEL[v.status], 'page:status')
        + _vmSub('Environment', `${n} selected`, 'page:env')
        + _vmSub('Last activity', _ACTIVITY_LABEL[v.activity], 'page:activity')
        + _SEP
        + _vmSub('Group by', _GROUP_LABEL[v.groupBy], 'page:group')
        + _vmSub('Sort by', _SORT_LABEL[v.sort], 'page:sort')
        + _vmSub('Custom groups', String(_custom().groups.length), 'page:custom')
        + _SEP
        + _vmChoice('Show empty groups', 'toggle:showEmpty', v.showEmpty, 'menuitemcheckbox')
        + _vmChoice('Show PR status', 'toggle:showPR', v.showPR, 'menuitemcheckbox');
}

function openViewMenu(ev) {
    const m = _menuEl('view-menu');
    m.classList.add('view-menu');
    _openMenu(m, ev, _viewPageHtml('main'), () => {});
    _showViewPage(m, 'main');
}

function _showViewPage(m, page) {
    _showMenuPage(m, _viewPageHtml(page), act => {
        const back = page === 'main' ? 'main' : page;
        if (act === 'back') return _showViewPage(m, 'main');
        if (act.startsWith('page:')) return _showViewPage(m, act.slice(5));
        if (act === 'custom-new') { newCustomGroup(); return _showViewPage(m, 'custom'); }
        if (act.startsWith('rename:')) { renameCustomGroup(act.slice(7)); return _showViewPage(m, 'custom'); }
        if (act.startsWith('delete:')) { deleteCustomGroup(act.slice(7)); return _showViewPage(m, 'custom'); }
        viewMenuAction(act);
        // a single choice returns to the top page (its new value showing); a multi-select
        // or a toggle stays where it is
        _showViewPage(m, act.startsWith('env:') || act.startsWith('toggle:') ? back : 'main');
    });
}

// Apply one view-menu choice (`status:all`, `env:judge` (toggles), `activity:30d`,
// `group:state`, `sort:name`, `toggle:showEmpty`) and re-render. Callable directly.
function viewMenuAction(act) {
    const v = _view();
    const [k, val] = String(act).split(':');
    if (k === 'status') v.status = val;
    else if (k === 'activity') v.activity = val;
    else if (k === 'group') v.groupBy = val;
    else if (k === 'sort') v.sort = val;
    else if (k === 'env') v.env = v.env.includes(val) ? v.env.filter(x => x !== val) : [...v.env, val];
    else if (k === 'toggle' && (val === 'showEmpty' || val === 'showPR')) v[val] = !v[val];
    else return;
    _saveView(v);
    renderSidebarAgents(_agentsCache || []);
}

// Open (never toggle) a window's pane: unlike a row click (selectAgent), which
// minimizes a pane that is already open, a menu's "Open … pane" always lands on it.
function _openPane(w) {
    if (!w) return;
    if (TERMINALS_ON && w.window_id && typeof focusPaneByWid === 'function') {
        if (!isWallVisible()) setTermMode('wall');
        focusPaneByWid(w.window_id);
        return;
    }
    showAgentDetail(w.name);
}

// Status colour → human word, for the dot's tooltip.
const _AGENT_STATUS_WORD = { green: 'working', yellow: 'waiting', grey: 'idle' };
// Status colour → the pane dot's CSS state class, so the sidebar dot pulses
// identically to the wall's .term-status-dot (working/waiting/idle).
const _SIDEBAR_DOT_CLASS = { green: 'working', yellow: 'waiting', grey: 'idle' };

// The fourth sidebar state (issue #475): idle, but the agent said something
// since you last spoke. `a.done` is the server's word (inbox.is_done), not
// re-derived here — the wid-keyed transcript resolution it needs (CMX-191) can
// only be done server-side.
function isDone(a) {
    return !!(a && a.done);
}

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
    // Awaited (its own try/catch is inside refreshRecentSessions): a caller that
    // awaits refreshSidebar() — resumeSession() does, to know when it's safe to
    // re-read the section — must see BOTH halves settled, not just the agent list.
    await refreshRecentSessions();
}

// The orchestrator slot (who owns the decisions inbox) changes independently of
// the agent list poll — a click on ANY pane's toggle (terminals.js) or the
// decisions-panel dropdown (decisions.js) fires this for every listener. Redraw
// off the already-cached agent list; no need to refetch /api/agents just to move
// one badge.
onOrchestratorChange(() => {
    renderSidebarAgents(_agentsCache || []);
    if (_detailAgent) renderAgentDetail();
});

// The WORK badges used to be a THIRD independent poller of /api/dispatcher, right
// here — fetching the same payload the Dispatch and Kanban views were each already
// fetching on their own timers. They are now filled by work.js's single poll (the
// slots themselves are declared on the Work view in views.js).

// --- Recent (dead) sessions — one-click resume (CMX-208) --------------------
// A UI over `chela restore`'s already-tested classification (chela/restore.py): a
// Claude session a hard tmux death (or `wsl --shutdown`) orphaned, with enough on
// record (cwd + session id) to relaunch via /api/restore/resume. Terminals-gated
// (resuming spawns a window, same as the "+" launcher) and hidden entirely when
// there is nothing to resume — a rare recovery affordance, not a permanent fixture.

// The identity a row is hidden by (CMX-11): /api/restore's `dismiss_key` — the
// session id for a resumable row, a `row:` address key for a dispatcher row, which
// carries no session id. Every row the list shows has one, so every row has a ×.
function _dismissKey(r) {
    return (r && (r.dismiss_key || r.session_id)) || '';
}

function _dismissButtonHtml(r, label) {
    return `<button class="recent-dismiss" title="Dismiss — hide this session from Recent (the transcript is kept)"
                aria-label="Dismiss ${attrEsc(label)}" data-session="${attrEsc(r.session_id || '')}"
                data-dismiss="${attrEsc(_dismissKey(r))}"
                onclick="event.stopPropagation(); chela.dismissRecentSession(this)">${lucideIcon('x')}</button>`;
}

function _recentRowHtml(r) {
    const label = r.label || r.cwd || r.wid;
    const key = `${r.store} ${r.wid}`;
    return `<div class="agent-row recent-row" data-key="${attrEsc(key)}">
        <span class="ar-type recent" title="dead session — needs a human to resume">&#8635;</span>
        <div class="ar-main">
            <div class="ar-top"><span class="agent-row-name" title="${attrEsc(label)}">${escHtml(label)}</span></div>
            <div class="ar-sub"><span class="ar-recap" title="${attrEsc(r.cwd || '')}">${escHtml(r.cwd || '')}</span></div>
        </div>
        <button class="btn-accent recent-resume" title="Resume this session"
                data-store="${attrEsc(r.store)}" data-wid="${attrEsc(r.wid)}"
                data-session="${attrEsc(r.session_id || '')}" data-epoch="${attrEsc(r.stamped_epoch || '')}"
                onclick="event.stopPropagation(); chela.resumeSession(this)">Resume</button>
        ${_dismissButtonHtml(r, label)}
    </div>`;
}

// A dispatcher-owned row (its worktree/window is still the dispatcher's — see
// app.py's _dispatcher_owned_wid_epochs) never gets a Resume button, hidden or
// revealed: resuming it would race the dispatcher's own worktree reap/completion,
// the same single-writer hazard roster.json/telegram-bindings.json have already hit
// (three times). It is shown ONLY as a fact, behind the toggle below — but, like
// every row the list shows, it can be dismissed (CMX-11).
function _dispatcherRowHtml(r) {
    const label = r.label || r.cwd || r.wid;
    return `<div class="agent-row recent-row recent-row-dispatcher"
                title="dispatcher-owned — resuming it would race the dispatcher's own worktree lifecycle">
        <span class="ar-type recent" title="dispatcher-owned session">&#8635;</span>
        <div class="ar-main">
            <div class="ar-top"><span class="agent-row-name" title="${attrEsc(label)}">${escHtml(label)}</span></div>
            <div class="ar-sub"><span class="ar-recap" title="${attrEsc(r.cwd || '')}">${escHtml(r.cwd || '')}</span></div>
        </div>
        ${_dismissButtonHtml(r, label)}
    </div>`;
}

let _recentPayload = { rows: [], dispatcher_rows: [], hidden: 0 };
let _recentDispatcherRevealed = false;

// Accepts either the /api/restore object shape ({rows, dispatcher_rows, hidden}) or
// a bare array (tests, and any future caller that only has resumable rows in hand) —
// a bare array is treated as "no dispatcher rows to show".
function renderRecentSessions(data) {
    _recentPayload = Array.isArray(data)
        ? { rows: data, dispatcher_rows: [], hidden: 0 }
        : (data || { rows: [], dispatcher_rows: [], hidden: 0 });
    _paintRecentSessions();
}

function _paintRecentSessions() {
    const section = document.getElementById('side-recent-section');
    const host = document.getElementById('side-recent');
    const count = document.getElementById('hdr-recent');
    if (!section || !host) return;

    const rows = _recentPayload.rows || [];
    const dispatcherRows = _recentPayload.dispatcher_rows || [];
    if (count) count.textContent = String(rows.length);
    const clearAll = document.getElementById('recent-clear-all');
    if (clearAll) clearAll.hidden = rows.length < 2;

    if (!rows.length && !dispatcherRows.length) {
        section.hidden = true;
        host.innerHTML = '';
        return;
    }
    section.hidden = false;

    let html = rows.map(_recentRowHtml).join('');
    if (dispatcherRows.length) {
        html += `<button class="recent-toggle-dispatcher" aria-expanded="${_recentDispatcherRevealed}"`
            + ` onclick="chela.toggleDispatcherSessions()">`
            + `${escHtml(dispatcherToggleLabel(dispatcherRows.length, _recentDispatcherRevealed))}</button>`;
        if (_recentDispatcherRevealed) {
            html += dispatcherRows.map(_dispatcherRowHtml).join('');
        }
    }
    host.innerHTML = html;
    host.querySelectorAll('.recent-row:not(.recent-row-dispatcher)').forEach(_wireRecentSwipe);
}

// The toggle's copy (CMX-437). It once concatenated two labels into "Hide 1
// dispatcher session hidden"; collapsed it now says what a click reveals, expanded
// what a click does.
function dispatcherToggleLabel(n, revealed) {
    if (revealed) return 'Hide dispatcher sessions';
    return `Show ${n} hidden dispatcher session${n === 1 ? '' : 's'}`;
}

// Swipe-left on touch dismisses, same as the ×. A mostly-horizontal leftward drag
// past the threshold counts; anything shorter or more vertical is a scroll.
const _SWIPE_DISMISS_PX = 70;
function _wireRecentSwipe(row) {
    let x0 = null, y0 = 0;
    row.addEventListener('touchstart', e => {
        const t = e.touches && e.touches[0];
        if (t) { x0 = t.clientX; y0 = t.clientY; }
    }, { passive: true });
    row.addEventListener('touchmove', e => {
        const t = e.touches && e.touches[0];
        if (x0 === null || !t) return;
        const dx = Math.min(0, t.clientX - x0);
        if (Math.abs(dx) > Math.abs(t.clientY - y0)) row.style.transform = `translateX(${dx}px)`;
    }, { passive: true });
    row.addEventListener('touchend', e => {
        const t = e.changedTouches && e.changedTouches[0];
        const start = x0;
        x0 = null;
        row.style.transform = '';
        if (start === null || !t) return;
        const dx = t.clientX - start, dy = t.clientY - y0;
        if (dx < -_SWIPE_DISMISS_PX && Math.abs(dx) > 2 * Math.abs(dy)) {
            dismissRecentSession(row.querySelector('.recent-dismiss'));
        }
    });
}

// --- Dismiss (CMX-437) -------------------------------------------------------
// The × on a Recent row (or a swipe-left) hides it: the row leaves the list at once,
// the server records the session id (chela/dismissed_sessions.py, so every device
// agrees), and an Undo toast offers it back for a few seconds. Nothing is deleted —
// not the transcript, not the restore bookkeeping.

const _UNDO_MS = 6000;
let _undoToast = null;

function _closeRecentUndoToast() {
    if (!_undoToast) return;
    clearTimeout(_undoToast.timer);
    _undoToast.el.remove();
    _undoToast = null;
}

function _showRecentUndoToast(sids, text) {
    _closeRecentUndoToast();
    let stack = document.getElementById('run-toast-stack');
    if (!stack) {
        stack = document.createElement('div');
        stack.id = 'run-toast-stack';
        stack.className = 'run-toast-stack';
        document.body.appendChild(stack);
    }
    const el = document.createElement('div');
    el.className = 'run-toast recent-undo-toast';
    el.setAttribute('role', 'status');
    el.innerHTML = `<span class="recent-undo-text">${escHtml(text)}</span> <button class="recent-undo">Undo</button>`;
    el.querySelector('.recent-undo').onclick = e => { e.stopPropagation(); undoDismissRecent(sids); };
    el.onclick = () => _closeRecentUndoToast();
    stack.appendChild(el);
    _undoToast = { el, sids, timer: setTimeout(_closeRecentUndoToast, _UNDO_MS) };
}

async function _postDismiss(path, sids) {
    try {
        const res = await api(path, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_ids: sids }),
        });
        return !!(res && res.ok);
    } catch (e) {
        return false;
    }
}

async function _dismiss(sids, text) {
    sids = sids.filter(Boolean);
    if (!sids.length) return;
    const gone = new Set(sids);
    const before = _recentPayload;
    const keep = r => !gone.has(_dismissKey(r));
    _recentPayload = {
        ...before,
        rows: (before.rows || []).filter(keep),
        dispatcher_rows: (before.dispatcher_rows || []).filter(keep),
    };
    _paintRecentSessions();
    if (!(await _postDismiss('/api/restore/dismiss', sids))) {
        _recentPayload = before;
        _paintRecentSessions();
        alert('Could not dismiss — nothing was changed.');
        return;
    }
    _showRecentUndoToast(sids, text);
}

async function dismissRecentSession(btn) {
    if (!btn) return;
    const key = btn.dataset.dismiss || btn.dataset.session;
    const row = [...(_recentPayload.rows || []), ...(_recentPayload.dispatcher_rows || [])]
        .find(r => _dismissKey(r) === key);
    const label = row ? (row.label || row.cwd || row.wid) : 'session';
    await _dismiss([key], `Dismissed ${label}`);
}

async function clearRecentSessions() {
    const sids = (_recentPayload.rows || []).map(_dismissKey).filter(Boolean);
    if (!sids.length) return;
    const n = sids.length;
    if (!confirm(`Dismiss all ${n} recent session${n === 1 ? '' : 's'}? Transcripts are kept; Undo is offered briefly.`)) return;
    await _dismiss(sids, `Dismissed ${n} session${n === 1 ? '' : 's'}`);
}

async function undoDismissRecent(sids) {
    _closeRecentUndoToast();
    if (!(await _postDismiss('/api/restore/undismiss', sids))) {
        alert('Could not undo the dismiss.');
    }
    await refreshRecentSessions();
}

function toggleDispatcherSessions() {
    _recentDispatcherRevealed = !_recentDispatcherRevealed;
    _paintRecentSessions();
}

async function refreshRecentSessions() {
    if (!TERMINALS_ON) return;
    try {
        renderRecentSessions(await api('/api/restore'));
    } catch (e) {
        // transient — keep the last render; the next tick retries.
    }
}

// Resume button click: spawn `claude --resume <session>` in the row's recorded cwd
// (server-side, /api/restore/resume — the exact same window-open path the "+" launch
// menu and Telegram's /new use). The row disables itself immediately so a slow spawn
// can't be double-clicked into two windows for the same dead session.
async function resumeSession(btn) {
    if (!btn || btn.disabled) return;
    const { store, wid, session, epoch: stampedEpoch } = btn.dataset;
    btn.disabled = true;
    const prevText = btn.textContent;
    btn.textContent = 'Resuming…';
    let res = null;
    try {
        res = await api('/api/restore/resume', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ store, wid, session_id: session, stamped_epoch: stampedEpoch || null }),
        });
    } catch (e) { /* res stays null — handled below */ }
    if (!res || res.error || res.ok === false) {
        btn.disabled = false;
        btn.textContent = prevText;
        alert((res && res.error) || 'Could not resume this session — it may have changed since the list was loaded.');
        refreshRecentSessions();
        return;
    }
    await refreshSidebar();   // re-fetches both the live agent list and Recent sessions
}

// --- Agent detail view -----------------------------------------------------

// Where "← Back" returns to: agent-detail has no nav item of its own (it is
// reached from the always-visible sidebar Sessions list, not a nav tab), so
// there is no single "parent" view — fall back to the same default main.js
// picks on load (the Wall when terminals are on, else Work).
function _agentDetailBackView() {
    return (typeof TERMINALS_ON !== 'undefined' && TERMINALS_ON) ? 'terminals' : 'work';
}

function renderAgentDetail() {
    const host = document.getElementById('agent-detail');
    if (!host) return;
    const a = (_agentsCache || []).find(x => x.name === _detailAgent);
    if (!a) {
        host.innerHTML = `<div class="detail-head">
            <span class="detail-back" onclick="chela.selectView('${_agentDetailBackView()}')">← Back</span>
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
        ['Role', escHtml(_ROLE_LABEL[_agentRole(a)])],
        ['Claude', a.claude_running ? 'running' : 'stopped'],
        ['Status', escHtml(a.session_status || (a.claude_running ? 'running' : 'offline'))],
        ['Liveness', escHtml(a.liveness || '—')],
        ['CWD', escHtml(a.cwd || '—')],
    ];
    if (a.schedule_next_run) rows.push(['Next run', shortTime(a.schedule_next_run)]);
    if (a.schedule_last_run) rows.push(['Last run', shortTime(a.schedule_last_run)]);
    if (a.ai_title) rows.push(['Title', escHtml(a.ai_title)]);

    let pr = '';
    if (a.pr && a.pr.url) {
        // CMX-41: say when the PR is history — a transcript's pr-link outlives the merge.
        const st = (a.pr.state === 'merged' || a.pr.state === 'closed') ? ` · ${a.pr.state}` : '';
        const label = (a.pr.number ? `PR #${a.pr.number}` : 'PR') + st;
        pr = ` <a class="pr-badge" href="${attrEsc(a.pr.url)}" target="_blank" rel="noopener noreferrer">${escHtml(label)}</a>`;
    }

    let recap = '';
    if (a.recap) {
        const age = a.recap_ts ? ` · ${ageStr((Date.now() - new Date(a.recap_ts)) / 1000)}` : '';
        recap = `<h4 style="margin-top:16px;">Recap${age}</h4><div class="detail-recap">${escHtml(a.recap)}</div>`;
    }

    host.innerHTML = `
        <div class="detail-head">
            <span class="detail-back" onclick="chela.selectView('${_agentDetailBackView()}')">← Back</span>
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
            <code>chela-*</code> PM2 service (including this dashboard) — the same as running
            <code>chela update</code> from the CLI. A dirty working tree or a branch diverged
            from its upstream refuses rather than clobbering anything.</p>
            <div class="s-row">
                <button class="btn-accent" id="update-apply-btn" onclick="chela.applyUpdate()" disabled>Update now</button>
            </div>
            <div id="update-apply-msg" class="s-savemsg"></div>
        </section>

        <section class="settings-section" data-keywords="repos repositories directory path launcher">
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
            <p class="s-desc">The <strong>coding</strong> model — cmx tasks rarely need Opus,
            so Sonnet is the default (cheaper/faster). The <strong>judge</strong> (the
            adversarial reviewer) always runs on a capable model and is not affected by this
            setting.</p>
        </section>

        <section class="settings-section" id="settings-remote-control">
            <h4>Remote Control</h4>
            <p class="s-desc">Launch sessions with Claude Code's <code>--remote-control</code> so
            they can be driven from claude.ai — windows opened from the dashboard, Telegram
            <code>/new</code>, and the orchestrator. Applies to <strong>new</strong> sessions
            only: a window already running keeps the flag it was launched with. Never applied
            to dispatcher agents or judges.</p>
            <div class="s-row" data-keywords="claude.ai remote phone mobile anywhere">
                <label class="s-rowlabel" for="remote-control-toggle">Remote Control — make new sessions reachable from claude.ai</label>
                <input id="remote-control-toggle" type="checkbox" role="switch" disabled
                       onchange="chela.setRemoteControl(this.checked)">
            </div>
            <div id="remote-control-msg" class="s-savemsg"></div>
            <p class="s-desc" id="remote-control-source"></p>
        </section>

        <section class="settings-section" id="settings-file-drop">
            <h4>File drop into terminals</h4>
            <p class="s-desc">Drop a file on a Wall terminal — or paste one, e.g. a screenshot —
            and it is saved to that session's <code>uploads/</code> folder and its
            <code>@uploads/&lt;name&gt;</code> is typed into the prompt (not sent). Works from
            your phone too. Never overwrites; <span id="file-drop-cap">25</span> MB per file.
            Share guests can never upload. A pane picks up a change when it reloads.</p>
            <div class="s-row" data-keywords="upload drag drop paste file image screenshot attach uploads phone">
                <label class="s-rowlabel" for="file-drop-toggle">File drop into terminals — save dropped or pasted files into the session</label>
                <input id="file-drop-toggle" type="checkbox" role="switch" disabled
                       onchange="chela.setFileDrop(this.checked)">
            </div>
            <div id="file-drop-msg" class="s-savemsg"></div>
            <p class="s-desc" id="file-drop-source"></p>
        </section>

        <section class="settings-section" data-keywords="tailscale ssh tunnel vpn auth security">
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
        </div>

        <div class="settings-tabpanel" data-tab="timing">
        <section class="settings-section" id="settings-timing" data-keywords="interval cadence tick poll seconds daemon">
            <h4>Timing</h4>
            <p class="s-desc">Daemon and dispatcher cadences. Blank a field to fall back to
            its <code>CHELA_*</code> env var (or the built-in default, shown as its
            placeholder) — takes effect on the <strong>next tick</strong>, no restart, except
            the status-feed timeout/TTL pair (marked below), which the dashboard/daemon
            process reads once at startup. A knob whose env var is currently set is disabled
            here — <strong>env always wins</strong>, so an edit would be silently discarded.</p>
            <div id="timing-rows" class="s-timing-rows"><div class="s-desc">Loading…</div></div>
            <div class="s-row">
                <button class="btn-accent" onclick="chela.saveTiming()">Save</button>
            </div>
            <div id="timing-msg" class="s-savemsg"></div>
        </section>
        </div>

        <div class="settings-tabpanel" data-tab="dispatch">
        <section class="settings-section" id="settings-dispatch" data-keywords="judge critic merge workflow concurrency retries">
            <h4>Dispatch</h4>
            <p class="s-desc">Dispatcher, judge, and critic policy. Blank a field to fall back
            to its <code>CHELA_*</code> env var (or the built-in default, shown as its
            placeholder). Four of these — <strong>Dispatch workflows</strong>,
            <strong>Judge</strong>, <strong>Critic</strong>, and <strong>Merge base</strong>
            (marked <span class="s-badge off" style="display:inline-block">restart</span>
            below) — are read once when the daemon/dashboard starts, so a save there takes
            effect on the <strong>next restart</strong>, not immediately. The rest apply on the
            next tick. A knob whose env var is currently set is disabled here —
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
            reports (<code>cost.total_cost_usd</code>) — no separate accounting, just a read
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
            <p class="s-desc">Tokens, not dollars, read from EVERY Claude Code transcript —
            judges, subagents, dispatched agents, background sessions, and any extra root
            listed below — so a session with no statusLine still shows up. Limits come from the
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
            independently. Only <strong>Miriam Mono</strong> keeps Hebrew on the grid — the
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
                    ${['8', '10', '12', '13', '14', '15', '16', '18']
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
            <p class="s-desc">End-to-end encrypted — the relay (<code>CHELA_COLLAB_RELAY</code>)
            is a zero-knowledge fan-out that only ever sees ciphertext (keys are derived in your
            browser from the pairing code). It does see room names + traffic timing (metadata) —
            run your own relay for full metadata privacy.</p>
        </section>
        <section class="settings-section" id="settings-share-typing">
            <h4>Guest typing</h4>
            <p class="s-desc">Shares are <strong>view only</strong> unless this is on. With it on,
            a share may let the guest type — but only into a <strong>sandboxed session</strong>
            (New session → Sandboxed: a container that sees only the project), checked live on
            every keystroke. Turning it off stops typing on live shares at once. See
            <code>docs/SHARE_SANDBOX.md</code> and run its checklist first.</p>
            <div class="s-row" data-keywords="share typing keyboard input write guest sandbox sandboxed container view only unsandboxed full access">
                <label class="s-rowlabel" for="share-typing-toggle">Guest typing — let share guests type into sandboxed sessions</label>
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
        const from = locked ? `set by <code>${env}</code> — env wins; unset it to edit here`
            : (cfg && cfg.remote_control_source === 'dashboard') ? 'this setting'
            : 'the built-in default';
        src.innerHTML = `In effect: <strong>${on ? 'On' : 'Off'}</strong> — ${from}.`;
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
    } catch (e) { setMsg('err', 'Save failed — unchanged.'); _loadRemoteControlSetting(); return; }
    // api() resolves on a 4xx too — fail closed: report it and re-read what IS stored.
    if (!cfg || cfg.error) {
        setMsg('err', 'Rejected — unchanged.');
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
        const from = locked ? `set by <code>${env}</code> — env wins; unset it to edit here`
            : (cfg && cfg.share_typing_source === 'dashboard') ? 'this setting'
            : 'the built-in default';
        src.innerHTML = `In effect: <strong>${on ? 'On' : 'Off'}</strong> — ${from}.`;
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
    } catch (e) { setMsg('err', 'Save failed — unchanged.'); _loadShareTypingSetting(); return; }
    if (!cfg || cfg.error) {
        setMsg('err', 'Rejected — unchanged.');
        _loadShareTypingSetting();
        return;
    }
    setMsg('ok', 'Saved · guest typing ' + (cfg.share_typing ? 'allowed into sandboxed sessions' : 'off — every share is view only'));
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
        const from = locked ? `set by <code>${env}</code> — env wins; unset it to edit here`
            : (cfg && cfg.file_drop_source === 'dashboard') ? 'this setting'
            : 'the built-in default';
        src.innerHTML = `In effect: <strong>${on ? 'On' : 'Off'}</strong> — ${from}.`;
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
    } catch (e) { setMsg('err', 'Save failed — unchanged.'); _loadFileDropSetting(); return; }
    if (!cfg || cfg.error) {
        setMsg('err', 'Rejected — unchanged.');
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
        if (btn) btn.disabled = true;
        return;
    }
    const behind = upd.behind || 0;
    const stale = upd.stale_services || [];
    const unknown = upd.unknown_services || [];
    // CMX-56: a service whose start commit / code set couldn't be read is neither stale
    // nor fresh — say so instead of folding it into either.
    const unknownNote = unknown.length
        ? ` · freshness unknown for ${escHtml(unknown.join(', '))}` : '';
    if (btn) btn.dataset.mode = '';
    if (behind > 0) {
        row.innerHTML = `<span class="s-status-badge off"><span class="s-status-dot" aria-hidden="true">○</span>${behind} behind</span>
            <span class="s-status-detail">branch ${escHtml(upd.branch || '')} — ${behind} commit(s) unpulled; running services are still serving what they last loaded</span>`;
        if (btn) { btn.disabled = false; btn.textContent = 'Update now'; btn.dataset.mode = 'update'; }
    } else if (stale.length) {
        // The checkout itself is fully synced (nothing to pull) — but a bare `git pull`
        // run by hand, bypassing this control, can leave running services on the OLD
        // code. Stale is import-aware (CMX-56): only a service whose OWN code changed
        // since it started is listed. The button then restarts exactly these.
        row.innerHTML = `<span class="s-status-badge off"><span class="s-status-dot" aria-hidden="true">○</span>${stale.length} stale</span>
            <span class="s-status-detail">branch ${escHtml(upd.branch || '')} — nothing to pull, but code that ${escHtml(stale.join(', '))} ${stale.length === 1 ? 'runs' : 'run'} changed after ${stale.length === 1 ? 'it' : 'they'} started (or <code class="s-cmd">pm2 restart ${stale.map(n => `<span class="s-cmd-arg">${escHtml(n)}</span>`).join(' ')}</code>)${unknownNote}</span>`;
        if (btn) {
            btn.disabled = false;
            btn.textContent = `Restart stale services (${stale.length})`;
            btn.dataset.mode = 'restart';
        }
    } else {
        row.innerHTML = `<span class="s-status-badge on"><span class="s-status-dot" aria-hidden="true">●</span>Up to date</span>
            <span class="s-status-detail">branch ${escHtml(upd.branch || '')} — nothing to pull${unknownNote}</span>`;
        if (btn) { btn.disabled = true; btn.textContent = 'Update now'; }
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
        setMsg('err', 'Request failed — the dashboard may have already restarted; refresh to check.');
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
    setMsg('ok', `${what}. This dashboard may briefly disconnect if it restarts; `
        + 'refresh in a few seconds to see the result.');
    // The pull may restart THIS process — nothing left to poll from here. Leave the
    // button disabled rather than re-enabling it against a page that's about to reload.
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
            (isEnv ? ` <span class="s-badge on" title="Set by ${attrEsc(k.env)} — env wins over the dashboard.">env</span>` : '') +
            (k.restart_required ? ' <span class="s-badge off" title="Takes effect after a daemon/dashboard restart, not the next tick.">restart</span>' : '');
        const input = isEnv
            ? `<input class="s-input s-timing-input" type="number" step="any"
                   data-timing-key="${attrEsc(k.key)}" value="${attrEsc(String(k.effective))}" disabled
                   title="Overridden by ${attrEsc(k.env)} — dashboard edits are ignored while it's set.">`
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
    } catch (e) { setMsg('err', 'Save failed — nothing changed.'); return; }
    // api() resolves on a 4xx too (the rejection arrives as a body, not a throw) —
    // the whole batch is atomic server-side, so an error here means NONE of the
    // fields changed, not just the bad one.
    if (!body || body.error) {
        const detail = body && body.errors
            ? Object.entries(body.errors).map(([k, v]) => k + ': ' + v).join('; ')
            : 'rejected';
        setMsg('err', 'Nothing saved — ' + detail);
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
            (isEnv ? ` <span class="s-badge on" title="Set by ${attrEsc(k.env)} — env wins over the dashboard.">env</span>` : '') +
            (k.restart_required ? ' <span class="s-badge off" title="Takes effect after a daemon/dashboard restart, not the next tick.">restart</span>' : '');
        const envTitle = isEnv ? `title="Overridden by ${attrEsc(k.env)} — dashboard edits are ignored while it's set."` : '';
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
    } catch (e) { setMsg('err', 'Save failed — nothing changed.'); return; }
    if (!body || body.error) {
        const detail = body && body.errors
            ? Object.entries(body.errors).map(([k, v]) => k + ': ' + v).join('; ')
            : 'rejected';
        setMsg('err', 'Nothing saved — ' + detail);
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

// --- Popover placement (CMX-398) ---------------------------------------------

// The margin every anchored popover keeps from each viewport edge.
const POPOVER_MARGIN = 8;
// Open popovers placed by placePopover, re-placed on window resize. Keyed by
// the popover; an entry drops out once its popover is hidden or detached.
const _placedPopovers = new Map();

function _popoverOpen(m) {
    return m.isConnected && !m.hidden && m.style.display !== 'none';
}

// Anchor a position:fixed popover to a control: BELOW it when it fits,
// otherwise ABOVE it (`top = r.top - gap - h`) — the sidebar-foot gear
// (#btn-primary-menu, CMX-377) sits at the bottom of the screen, where the old
// topbar's "always below" math opened the menu almost entirely off-screen.
// Horizontally right-aligned to the anchor (`align: 'center'` centres it
// instead), and clamped inside the viewport with POPOVER_MARGIN on every side.
// A popover taller than the viewport allows gets a max-height and scrolls
// (.popover already has overflow-y:auto) instead of overflowing. The popover
// must be displayed before this is called — a display:none element has no
// size to measure.
function placePopover(m, anchor, { gap = 6, align = 'right' } = {}) {
    if (!m || !anchor) return;
    const M = POPOVER_MARGIN;
    const vw = window.innerWidth, vh = window.innerHeight;
    // Measure the popover's own height, not a max-height a previous (smaller)
    // viewport left behind.
    m.style.maxHeight = '';
    m.style.overflowY = '';
    const room = Math.max(0, vh - 2 * M);
    let h = m.offsetHeight;
    if (h > room) {
        m.style.maxHeight = room + 'px';
        m.style.overflowY = 'auto';
        h = room;
    }
    const w = m.offsetWidth;
    const r = anchor.getBoundingClientRect();
    let top;
    if (r.bottom + gap + h <= vh - M) top = r.bottom + gap;       // below
    else if (r.top - gap - h >= M) top = r.top - gap - h;         // above
    // Neither side has room: on the roomier side, pushed back inside.
    else top = (vh - r.bottom >= r.top) ? r.bottom + gap : r.top - gap - h;
    top = Math.max(M, Math.min(top, vh - M - h));
    let left = align === 'center' ? r.left + r.width / 2 - w / 2 : r.right - w;
    left = Math.max(M, Math.min(left, vw - M - w));
    m.style.top = top + 'px';
    m.style.left = left + 'px';
    _placedPopovers.set(m, { anchor, opts: { gap, align } });
}

window.addEventListener('resize', () => {
    for (const [m, { anchor, opts }] of _placedPopovers) {
        if (_popoverOpen(m) && anchor.isConnected) placePopover(m, anchor, opts);
        else _placedPopovers.delete(m);
    }
});

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
    // Right-aligned to the button off the MEASURED width (placePopover). A
    // hardcoded width here (it used to be 160, from the old popover) silently
    // sent the menu off the RIGHT edge the moment the CSS got wider than the
    // guess — which .launch-menu's 232px min-width did, on a button that sits
    // ~55px from the viewport edge.
    placePopover(m, anchor, { gap: 4 });
    setTimeout(() => document.addEventListener('click', hideNewMenu, { once: true }), 0);
}

function hideNewMenu() {
    const m = document.getElementById('new-menu');
    if (m) m.style.display = 'none';
}

// Primary menu (a settings gear at the right of the sidebar foot's one row since
// CMX-393 — a ⋮ in the foot since CMX-377, originally a topbar button): folds the three former topbar primaries — Jump
// to… (#btn-palette), New… (#btn-new), overflow (#btn-overflow) — behind ONE
// button (CMX-109 / CMX-108 Part A re-filed; cmx-108/#122's WALL toolbar fold —
// grid presets + lock behind openLayoutMenu — was reverted in CMX-111: Liav
// never asked for that one folded, only this one). Jump to…
// and the old overflow's secondary actions (Share current, Notifications,
// Settings) plus the usage/updated readouts are flat items here; New… reopens
// the existing #new-menu (openNewMenuFromPrimary below) rather than duplicating
// it, since #new-menu already serves the sidebar's own "+" trigger from a
// different anchor. The safety kill-switch (#btn-shares) is deliberately NOT in
// here — it stays visible whenever a share is live. Anchored + light-dismiss,
// same pattern as the old openNewMenu/openOverflowMenu.
function openPrimaryMenu(ev) {
    if (ev) ev.stopPropagation();
    const m = document.getElementById('primary-menu');
    if (!m) return;
    const anchor = (ev && ev.currentTarget) || document.getElementById('btn-primary-menu');
    m.style.display = 'block';
    // CMX-398: the button sits in the sidebar FOOT now, so "below it" is off
    // the bottom of the screen — placePopover flips the menu above it.
    placePopover(m, anchor, { gap: 6 });
    setTimeout(() => document.addEventListener('click', hidePrimaryMenu, { once: true }), 0);
}

function hidePrimaryMenu() {
    const m = document.getElementById('primary-menu');
    if (m) m.style.display = 'none';
}

// The primary menu's "New…" row: close the primary menu and reopen #new-menu
// (Favorites/Recent + Init a repo/Scheduled task/Shell window) anchored at
// #btn-primary-menu — the exact same popover the sidebar's "+" trigger opens
// from its own anchor, just from a second entry point.
function openNewMenuFromPrimary() {
    hidePrimaryMenu();
    openNewMenu({ stopPropagation() {}, currentTarget: document.getElementById('btn-primary-menu') });
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

// New session → Sandboxed (CMX-403): a window running the container launcher
// (chela.share_sandbox) — the only kind of window a share guest may type into. Asks
// for the project directory, pre-filled with the most recent launch target; the server
// refuses $HOME, secret dirs, or a host without docker/image/token, and says why.
// `web` (CMX-418, the "· allow web access…" entry) opts THIS session into web access:
// public hosts only, through the filtering egress proxy — confirmed before launch.
async function newSandboxedSession(web) {
    web = web === true;
    const recent = (_launcherData.favorites || []).concat(_launcherData.recent || []);
    const guess = recent.length ? recent[0].path : '';
    const label = web ? 'Sandboxed session with WEB ACCESS' : 'Sandboxed session';
    const cwd = (window.prompt(label + ' — project directory (the guest sees ONLY this):', guess) || '').trim();
    if (!cwd) return;
    if (web && !window.confirm('Allow web access for this session?\n\n'
            + 'It can fetch any PUBLIC web page (never your host or LAN), rate-limited, and every '
            + 'request is logged. A web page can also make it SEND workspace contents to a public '
            + 'site, so keep only the guest\'s own material in ' + cwd + '.')) return;
    try {
        const res = await api('/api/agents/spawn-sandboxed', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(web ? { cwd, web: true } : { cwd }),
        });
        if (!res || !res.ok) { alert('Sandboxed session refused: ' + ((res && res.error) || 'unknown error')); return; }
        setAgentsCache([]);
        selectView('terminals');
        refreshSidebar();
        refreshLauncher();
    } catch (e) {
        alert('Sandboxed session failed: ' + e);
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
        // Centred under the target — or above it when the target sits at the
        // bottom of the screen (the sidebar foot's CPU/RAM/Disk readouts) —
        // clamped to the viewport.
        placePopover(tipEl, target, { gap: 6, align: 'center' });
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
    // CMX-6: create a Linear issue (newtask.js) — reached through window.chela, not an
    // import, so nav.js does not pull the Work module graph in.
    items.push({ icon: lucideIcon('plus'), title: 'New task', sub: 'action · Linear issue',
                 run: () => { if (window.chela && window.chela.openNewTask) window.chela.openNewTask(); } });
    items.push({ icon: lucideIcon('clock'), title: 'Add scheduled task', sub: 'action',
                 run: () => { if (typeof showAddSchedule === 'function') showAddSchedule(); } });
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
// unminimized wall pane to the TOP — wall order (`_orderedWids`), with a pane
// wanting attention (waiting > working > idle) bubbling ahead of its peers,
// same status word the sidebar uses — so ⌘K instantly answers "what's open,
// what needs me". Once the user types, this section is gone: `_paletteItems()`
// alone (agents already included there) drives the usual fuzzy match over
// everything, so a query never traps you in "open panes only".
const _PANE_ATTENTION_RANK = { yellow: 0, green: 1, grey: 2 };

function _openPaneItems() {
    if (!TERMINALS_ON || typeof _renderedWids === 'undefined') return [];
    const live = _renderedWids.filter(w => !(_minimized && _minimized.has(w)));
    if (!live.length) return [];
    const wallOrder = _orderedWids(live);
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
export { _closeRecentUndoToast, closeShortcuts, deleteCustomGroup, newCustomGroup, groupMenuAction, groupNewSession, moveToGroup, openGroupMenu, openRowMenu, openViewMenu, renameCustomGroup, toggleGroup, toggleShowArchived, viewMenuAction, dispatcherToggleLabel, openPalette, openShortcuts, refreshRecentSessions, refreshSidebar, renderAgentDetail, renderNav, renderRecentSessions, renderSidebarAgents, selectView, updateCtxCache };

// --- Stage 0: window.chela — surface reachable from inline HTML handlers ---
window.chela = window.chela || {};
Object.assign(window.chela, { applyUpdate, groupMenuAction, groupNewSession, hideSideMenus, moveToGroup, openGroupMenu, openRowMenu, openViewMenu, toggleShowArchived, viewMenuAction, clearRecentSessions, clearSettingsSearch, closePalette, closeShortcuts, closeSidebar, dismissRecentSession, hideNewMenu, hidePrimaryMenu, newSandboxedSession, newShellWindow, openNewMenu, openNewMenuFromPrimary, openPalette, openPrimaryMenu, openShortcuts, _palRun, placePopover, _renderPalette, resumeSession, saveDispatch, saveProjectsDir, saveTiming, selectAgent, selectSettingsTab, selectView, setAgentModel, setAgentPermissionMode, setCollabName, setFileDrop, setRemoteControl, setRunToastsMuted, setShareTyping, setTermFont, setTermLatin, setTermSize, setTheme, settingsSearch, sidebarJumpInput, toggleDispatcherSessions, toggleGroup, toggleSettings, toggleSidebar, undoDismissRecent });
