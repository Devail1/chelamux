// --- Stage 0: ES-module imports ---
import { $, BASE_PATH, TERMINALS_ON, WALL_TILE_DISPATCHED, _agentsCache, api, attrEsc, currentTab, escHtml, setAgentsCache, updateTabSignal, wantsHuman } from './util';
import { openPalette, selectView } from './nav';
import { altSwitchKeydown, renderSidebarAgents, setAltHeld, sidebarOrder, updateCtxCache } from '../../frontend/src/features/sidebar/controller';
import { orderByList } from '../../frontend/src/features/sidebar/model';
import { focusLayout, rankOrder } from './wallmodel';
import { pinKeybar } from '../../frontend/src/features/keybar';
import { minimizedPanes, paneOrderKey, pinnedPanes, setMinimized, termMode, wallLayout } from '../../frontend/src/shared/store/wall';
import { context, loadContext } from '../../frontend/src/shared/store/context';
import { onEvent } from '../../frontend/src/shared/events';
import { configureShareHost, followAgents, shareSession, stopOwnerPresence } from '../../frontend/src/features/share';
import { configurePaneHeads, displayLabel, mountPaneHeads, refreshPaneHeads } from '../../frontend/src/features/panehead';
import { mountTileFoots, refreshTileFoots } from '../../frontend/src/features/tilefoot';
import { configureDock, forgetAutoDock, mountDock, refreshDock, runLazyDock } from '../../frontend/src/features/walldock';
import { mountWallToolbar } from '../../frontend/src/features/walltoolbar';
import { leaveRoom, refreshRooms, replayRoomAccents, startWire } from '../../frontend/src/features/wire';

// ---------------------------------------------------------------------------
// Terminals (embedded ttyd via the gateway: /term/<wid>/)
// ---------------------------------------------------------------------------
//
// Identity model: every pane is keyed by its tmux WINDOW ID (`@N`), never its
// display name. Window ids are stable for a window's lifetime, so a rename
// (e.g. relabelling shell-N -> cwd basename) leaves the tile, iframe, and
// backing ttyd completely untouched — only the visible label changes. The
// human-readable name/label is resolved on demand via _paneTitle(wid) and is
// used for display only. localStorage (layout/order/minimized/titles) is also
// keyed by wid; it re-defaults on a tmux restart (wids are reassigned),
// which is rare and self-correcting.

let _termMode = localStorage.getItem('pc_term_mode') || 'wall';   // default view: wall
let _termSig = '';
let _grid = null;                 // Gridstack instance (wall mode only)
let _renderedWids = [];           // pane wids currently in the DOM (live render order)
let _termTimer = null;            // fast reactive poll while the terminals tab is open
// The layout the toolbar sets (features/walltoolbar → shared/store/wall.ts `wallLayout`):
// {preset: {cols, rows}, locked, auto, focus}. Manual layout (pc_wall_layout) stays SACRED:
// auto-arrange and Focus only ever compute a live fill and never write it, so turning either
// off restores exactly what the user left. The two are mutually exclusive (the store's own
// setters turn the other off). This module applies every change (_onLayoutChange below).
const _layout = () => wallLayout.get();
// Comma-joined wid sequence last applied by auto-arrange, so the 4s poll only
// touches GridStack when the ranked order actually changed (no per-tick thrash).
// Reset to '' to force the next _applyAutoArrange to (re)apply unconditionally
// (toggle-on, preset change while auto, or a fresh buildWall).
let _autoOrderSig = '';
// Which pane is currently the large "focus" pane. Chosen when Focus turns on
// (_pickFocusWid) and updated by clicking a strip pane's header (promote).
let _focusWid = null;
// Sig of "focus wid | strip order" last applied, mirroring _autoOrderSig —
// the 4s poll only touches GridStack when the focus target or the tiled pane
// set actually changed, never on every tick.
let _focusOrderSig = '';
let _wallResizeTimer = null;      // debounce for the resize re-fit
const TERM_REFRESH_MS = 4000;     // diff the live agent set every few seconds

// The fill is viewport-relative, so a window/screen resize changes how many
// rows fit. Re-apply the active preset (debounced) so the wall keeps filling
// the height on any screen size. No-op unless in wall mode.
window.addEventListener('resize', () => {
    if (!TERMINALS_ON || _termMode !== 'wall' || !_grid) return;
    clearTimeout(_wallResizeTimer);
    _wallResizeTimer = setTimeout(applyGridLayout, 200);
});

// ---- wid <-> agent helpers -------------------------------------------------
// Live panes are addressed by tmux window id; their display name/cwd/status
// come from the /api/agents cache, looked up by window_id.
function _agentByWid(wid) {
    return (_agentsCache || []).find(a => a.window_id === wid);
}

// ---- New-terminal readiness ------------------------------------------------
// A freshly-spawned pane's /term/<wid>/ iframe 404s ("Unknown terminal") until
// agent-terminals.sh assigns it a ttyd port (~12s). So a pane whose terminal is
// not yet ready renders a placeholder + spinner instead of the iframe, and polls
// GET /api/term/ready?agent=<wid> every TERM_READY_POLL_MS (capped) until ready,
// then swaps the placeholder for the real iframe in place (no stage rebuild, so
// already-live iframes are never reloaded). Agents already in the port map render
// the iframe immediately — no placeholder flash for existing / managed panes.
const _termReady = new Set();     // wids confirmed terminal-ready (port assigned)
const _readyPollers = new Map();  // wid -> interval handle for the readiness poll
const TERM_READY_POLL_MS = 1500;
const TERM_READY_MAX_TRIES = 20;  // ~30s, then offer a manual retry

function _cssEsc(s) {
    return (window.CSS && CSS.escape) ? CSS.escape(s) : String(s).replace(/["\\]/g, '\\$&');
}

async function _checkReady(wid) {
    try {
        const r = await api('/api/term/ready?agent=' + encodeURIComponent(wid));
        return !!(r && r.ready);
    } catch (e) { return false; }
}

// Placeholder tile shown in place of the iframe while the terminal spins up.
// Carries data-pending=<wid> so _swapToFrame can find and replace exactly this
// tile; the visible text uses the friendly label.
function _termPlaceholder(wid) {
    return `<div class="term-frame term-pending" data-pending="${attrEsc(wid)}">
      <div class="term-pending-inner"><span class="term-spinner"></span> starting ${escHtml(_paneTitle(wid))}…</div>
    </div>`;
}

// Replace a pending placeholder with the real ttyd iframe, in place — never
// rebuilds the stage, so sibling iframes are untouched.
function _swapToFrame(wid) {
    const stage = $('#term-stage');
    const ph = stage.querySelector('.term-pending[data-pending="' + _cssEsc(wid) + '"]');
    if (!ph) return;
    const ifr = document.createElement('iframe');
    ifr.className = 'term-frame';
    // Delegate the Clipboard API into the same-origin ttyd frame so the injected
    // Ctrl+V shim can read the clipboard (the paste event path works without it,
    // but clipboard.read()/readText() is blocked in a subframe lacking this).
    ifr.setAttribute('allow', 'clipboard-read; clipboard-write');
    ifr.src = _termFrameSrc(wid);
    ifr.title = _paneTitle(wid);
    ph.replaceWith(ifr);
}

function _stopReadyPoll(wid) {
    const h = _readyPollers.get(wid);
    if (h) { clearInterval(h); _readyPollers.delete(wid); }
}

function _stopAllReadyPolls() {
    _readyPollers.forEach(h => clearInterval(h));
    _readyPollers.clear();
}

function _startReadyPoll(wid) {
    _stopReadyPoll(wid);
    let tries = 0;
    const tick = async () => {
        tries += 1;
        if (await _checkReady(wid)) {
            _stopReadyPoll(wid);
            _termReady.add(wid);
            _swapToFrame(wid);
        } else if (tries >= TERM_READY_MAX_TRIES) {
            _stopReadyPoll(wid);
            _showReadyRetry(wid);
        }
    };
    _readyPollers.set(wid, setInterval(tick, TERM_READY_POLL_MS));
}

// After the cap, swap the spinner for a click-to-retry affordance that re-polls.
function _showReadyRetry(wid) {
    const stage = $('#term-stage');
    const ph = stage.querySelector('.term-pending[data-pending="' + _cssEsc(wid) + '"]');
    if (!ph) return;
    ph.innerHTML = `<div class="term-pending-inner">still starting.
      <a href="#" class="term-retry" onclick="chela.retryReady('${_jsStr(wid)}');return false;">click to retry</a></div>`;
}

function retryReady(wid) {
    const stage = $('#term-stage');
    const ph = stage.querySelector('.term-pending[data-pending="' + _cssEsc(wid) + '"]');
    if (ph) {
        ph.innerHTML = `<div class="term-pending-inner"><span class="term-spinner"></span> starting ${escHtml(_paneTitle(wid))}…</div>`;
    }
    _startReadyPoll(wid);
}

// Kick off readiness pollers for every pending placeholder currently on stage.
function _startPlaceholderPolls() {
    $('#term-stage').querySelectorAll('.term-pending[data-pending]').forEach(el => {
        _startReadyPoll(el.getAttribute('data-pending'));
    });
}

function setTermMode(m) {
    _termMode = m;
    localStorage.setItem('pc_term_mode', m);   // remember the user's choice across reloads
    $('#term-mode-single').classList.toggle('btn-accent', m === 'single');
    $('#term-mode-wall').classList.toggle('btn-accent', m === 'wall');
    renderTerminals();
}

// "The wall is the visible screen": the terminals tab is active AND it's in
// wall (not single-terminal) mode. Sidebar-row clicks (nav.js selectAgent,
// CMX-139) use this to decide whether clicking an already-open pane should
// hide it (wall visible) or summon it (wall not visible, or another tab
// entirely) — the same click means something different depending on what's
// actually on screen.
function isWallVisible() {
    return currentTab === 'terminals' && _termMode === 'wall';
}

async function termKeyFor(wid, key) {
    if (!wid) return;
    try {
        await api('/api/term/key', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ agent: wid, key }),
        });
    } catch (e) { console.error('termKey', e); }
}

// Single mode: target the selector's value (a wid). Wall mode passes the tile's
// own wid.
async function termKey(key) { return termKeyFor($('#term-agent').value, key); }

// Paste the device clipboard into the active pane. xterm.js can't surface iOS's
// native "Paste" callout inside its hidden textarea, so phones had no reliable
// paste path; this reads the clipboard on tap (the gesture unlocks the read on
// iOS) and ships it to /api/term/paste, which delivers a bracketed paste at the
// tmux layer.
//
// CMX-423: ONE clipboard.read() where it exists, like the in-pane Ctrl/Cmd+V shim. An
// image (a screenshot is the usual phone clipboard) takes the image path
// (/api/term/paste-image, then its path typed) so Claude attaches it. Every no-op says
// why on the button.
//
// CMX-428: exactly ONE clipboard call per tap — read() if it exists, else readText(),
// never both. On iOS WebKit the tap's user activation covers a single read, so a
// readText() after an awaited read() came back '' (or threw) and the button said
// "Empty" over a full clipboard. iOS also often hands copied text over as
// text/uri-list or text/html with no text/plain entry, so those are accepted too.
function _pasteFlash(btn, label) {
    if (!btn) return;
    const orig = btn.dataset.label || btn.textContent;
    btn.dataset.label = orig;
    btn.textContent = label;
    clearTimeout(btn._pasteFlash);
    btn._pasteFlash = setTimeout(() => { btn.textContent = orig; }, 1500);
}

async function _pasteTextTo(wid, text, btn) {
    if (!text) { _pasteFlash(btn, 'Empty'); return; }
    await api('/api/term/paste', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent: wid, text }),
    });
}

async function _pasteImageTo(wid, blob, btn) {
    const fd = new FormData();
    fd.append('agent', wid);
    fd.append('image', blob, blob.name || 'paste');
    const j = await api('/api/term/paste-image', { method: 'POST', body: fd, credentials: 'same-origin' });
    if (!j || !j.path) { _pasteFlash(btn, 'Failed'); return; }
    await _pasteTextTo(wid, j.path, btn);
}

// Plain text from a text/html clipboard entry: drop script/style, fold source
// whitespace (HTML newlines are just spaces), break lines at <br> and block ends, strip tags, decode entities (a <textarea>'s innerHTML parses as RCDATA, so no
// markup is ever instantiated), then collapse whitespace.
function _htmlToText(html) {
    const stripped = String(html)
        .replace(/<(script|style)\b[\s\S]*?<\/\1\s*>/gi, '')
        .replace(/\s+/g, ' ')
        .replace(/<br\b[^>]*>|<\/(p|div|li|tr|h[1-6]|pre|blockquote)\s*>/gi, '\n')
        .replace(/<[^>]*>/g, '');
    const ta = document.createElement('textarea');
    ta.innerHTML = stripped;
    return ta.value.split('\n')
        .map(l => l.replace(/\s+/g, ' ').trim())
        .filter(Boolean)
        .join('\n');
}

// text/uri-list: first line that is neither blank nor a '#' comment (RFC 2483).
function _uriListToText(list) {
    return String(list).split(/\r?\n/).map(l => l.trim()).find(l => l && l[0] !== '#') || '';
}

const _PASTE_TEXT_TYPES = [
    ['text/plain', t => t],
    ['text/uri-list', _uriListToText],
    ['text/html', _htmlToText],
];

// Best text out of clipboard.read()'s items: text/plain, then text/uri-list, then
// text/html. '' when none of them yields anything.
async function _clipboardItemsText(items) {
    for (const [type, conv] of _PASTE_TEXT_TYPES) {
        for (const it of items) {
            if ((it.types || []).indexOf(type) < 0) continue;
            const text = conv(await (await it.getType(type)).text());
            if (text) return text;
        }
    }
    return '';
}

async function termPaste(btn) {
    const wid = $('#term-agent').value;
    const cb = navigator.clipboard;
    if (!wid) return;
    if (!cb || (!cb.read && !cb.readText)) { _pasteFlash(btn, 'No clipboard'); return; }
    try {
        if (!cb.read) {
            let text = '';
            try { text = await cb.readText(); } catch (e) { _pasteFlash(btn, 'Denied'); return; }
            await _pasteTextTo(wid, text, btn);
            return;
        }
        let items;
        try { items = await cb.read(); } catch (e) { _pasteFlash(btn, 'Denied'); return; }
        for (const it of items || []) {
            const t = (it.types || []).find(x => x.indexOf('image/') === 0);
            if (t) { await _pasteImageTo(wid, await it.getType(t), btn); return; }
        }
        await _pasteTextTo(wid, await _clipboardItemsText(items || []), btn);
    } catch (e) { console.error('termPaste', e); _pasteFlash(btn, 'Failed'); }
}

// Per-tile copy-mode toggle for wall mode (each button tracks its own state).
function termScrollFor(btn, wid) {
    const on = btn.classList.toggle('btn-accent');
    termKeyFor(wid, on ? 'scroll' : 'scroll-exit');
    btn.title = on ? 'Exit scroll (tmux copy-mode)' : 'Scroll (tmux copy-mode)';
}

// A wid quoted for an inline `onclick="chela.x('…')"` handler.
function _jsStr(s) { return String(s).replace(/\\/g, '\\\\').replace(/'/g, "\\'"); }

// ---- Display labels. THE TMUX WINDOW NAME IS THE SOURCE OF TRUTH: a rename goes
// to the server (the pane header, features/panehead -> POST /api/agents/<wid>/rename ->
// tmux rename-window) and comes back to every client through /api/agents. There is
// deliberately NO local label store. The old one (localStorage `pc_pane_titles`) was
// per-browser: a rename on the laptop was invisible on the phone, never reached tmux or the
// bound Telegram topic, and died with the browser's data. Drop any leftovers so the two
// layers can't disagree.
try { localStorage.removeItem('pc_pane_titles'); } catch (e) { /* private mode */ }

// A window's label for what legacy still draws (dock chips, the dropdown, the iframe title):
// the model the pane header and the sidebar share (CMX-62, features/panehead).
function _displayLabel(wid) { return displayLabel(_agentByWid(wid), wid); }
function _paneTitle(wid) { return _displayLabel(wid); }

// --- collab sharing (presence-only) ----------------------------------------
// The shared "link" is just the clean /term/<wid>/ URL — NO ?collab magic;
// sharing is a server-side per-wid flag (POST /api/term/<wid>/share) so the host
// can actually revoke. One src helper keeps frame()/_wallTileHTML/_swapToFrame
// in agreement.
function _termSrc(wid) { return '/term/' + encodeURIComponent(wid) + '/'; }
function _termLink(wid) { return location.origin + BASE_PATH + _termSrc(wid); }

// iframe-only src: same clean path, plus a per-connection `cid` (CMX-175). ttyd's
// bundled client JS builds its WebSocket URL from `location.search` verbatim, so
// this round-trips onto the /term/<wid>/ws upgrade request and lets the backend
// (term_ws in app.py) match THIS iframe's connection to its own tmux client tty —
// see the ignore-size watch reporting below. NEVER use this for the copyable
// share link (_termLink) — that must stay the clean URL a joiner reuses verbatim.
function _newCid() {
    if (window.crypto && typeof window.crypto.randomUUID === 'function') return window.crypto.randomUUID();
    return 'c' + Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2);
}
function _termFrameSrc(wid) { return _termSrc(wid) + '?cid=' + _newCid(); }

// The pane's own frames, for the share feature's reload and mint-time size (CMX-240).
function _paneFrames(wid) {
    return Array.from(document.querySelectorAll('#term-stage iframe.term-frame')).filter(ifr => _widOfFrame(ifr) === wid);
}
configureShareHost({ paneTitle: wid => _paneTitle(wid), paneFrames: _paneFrames });

// The pane header is React since CMX-241 (features/panehead): one root, portaled into each
// tile's `[data-head-for]` slot, which _wallTileHTML and the single pane draw and
// mountPaneHeads fills. It reads the agents, share, orchestrator and pin stores itself; what
// it asks of the Wall comes through here.
configurePaneHeads({
    minimize: wid => minimizePane(wid),
    dropPane: wid => dropTerminalPane(wid),
    wireDragStart: (e, from, wid) => startWire(e, from, wid),
    leaveRoom: (badge, wid) => leaveRoom(badge, wid),
    labelsChanged: () => _refreshPaneLabels(),
});
// The slot a header portals into: `display: contents`, so the head and its kill confirm lay
// out as the pane's own children.
function _paneHeadSlot(wid, wall) {
    return `<div class="island-slot pane-head-slot" data-head-for="${attrEsc(wid)}"${wall ? ' data-head-wall="1"' : ''}></div>`;
}
// The footer (the context bar and, on the wall, the action bar) is React since CMX-242
// (features/tilefoot), portaled like the header into a slot under the terminal; the recap line
// into one under the header (wall tiles only). mountTileFoots fills them.
function _footSlot(wid, wall) {
    return `<div class="island-slot" data-foot-for="${attrEsc(wid)}"${wall ? ' data-foot-wall="1"' : ''}></div>`;
}
function _recapSlot(wid) {
    return `<div class="island-slot" data-recap-slot="${attrEsc(wid)}"></div>`;
}
// Draw the React parts of every tile under `scope`: BEFORE Gridstack adopts them (it finds each
// tile's `.gs-grip` drag handle, in the header, as it inits).
function _mountTileParts(scope) {
    mountPaneHeads(scope);
    mountTileFoots(scope);
}

// ---- Minimize-to-dock (wall mode) -----------------------------------------
//
// Minimizing a tile removes it from the Gridstack engine (freeing its cells)
// but LEAVES the element in the DOM, hidden — so the live ttyd iframe is never
// detached and never reloads. Its chip lands in the dock (features/walldock, CMX-242);
// clicking it re-adopts the same element via makeWidget at its saved coords. The set of
// minimized wids is shared/store/wall.ts `minimizedPanes` (persisted to pc_wall_minimized,
// so the dock survives a reload: buildWall re-applies it after rebuilding the wall).
const _isMinimized = wid => minimizedPanes.get().has(wid);
configureDock({ restore: wid => restoreFromDock(wid) });

// ---- Per-pane layout pin ----------------------------------------------------
// A pinned pane sits out of applyGridLayout's auto-reflow (grid presets): its
// x/y/w/h are left exactly as the user placed them while every other pane
// rearranges around it. The set is shared/store/wall.ts `pinnedPanes` (persisted
// by wid, so a pin survives a wall rebuild); the header's Pin row writes it, and
// the tile's dashed outline follows it here.
const _isPinned = wid => pinnedPanes.get().has(wid);
pinnedPanes.subscribe(() => {
    document.querySelectorAll('#term-stage .grid-stack-item[gs-id]').forEach(item => {
        item.classList.toggle('pane-pinned', _isPinned(item.getAttribute('gs-id')));
    });
});

// Hide a tile element + pull it out of the grid engine. Idempotent.
function _minimizeItem(item) {
    if (!item || item.dataset.minimized === '1') return;
    const node = item.gridstackNode;
    if (node) item.dataset.gsCoords = JSON.stringify({ x: node.x, y: node.y, w: node.w, h: node.h });
    if (_grid) { try { _grid.removeWidget(item, false); } catch (e) { /* noop */ } }
    item.dataset.minimized = '1';
    item.style.display = 'none';
}

// Minimize a pane by wid (pull its tile out of the grid, dock its chip).
function minimizePane(wid) {
    const item = Array.from($('#term-stage').querySelectorAll('.grid-stack-item'))
        .find(it => it.getAttribute('gs-id') === wid);
    if (!item) return;
    _minimizeItem(item);
    setMinimized(wid, true);    // the dock repaints synchronously, at its final height
    _refitWallForDock();        // wall re-packs above the (taller) dock
    _reportWatchForWid(wid);    // docked tile stops dictating the shared window's size (CMX-175)
}

// A DISPATCHED worker opens MINIMIZED and POPS OUT when it blocks on a human (CMX-76; the
// machine is features/walldock's). Called from _applyTermStatus, which runs AFTER the tiles
// exist on both paths in (renderTerminals → buildWall, and termTick → _absorbFreshTerminals):
// a tile we cannot see yet is simply decided on the next tick (4s), never dropped.
function _lazyDock(agents) {
    if (WALL_TILE_DISPATCHED) return;              // opt-out: tile every worker like a human's
    if (!Array.isArray(agents)) return;
    if (_termMode !== 'wall' || !_grid) return;    // the dock is a wall affordance
    runLazyDock(agents, { onWall: wid => _renderedWids.includes(wid), minimize: minimizePane, restore: restoreFromDock });
}

// The dock sits below the stage and changes the wall's available height, so
// after it shows/hides we re-apply the active fill preset to re-fit the wall
// (the remaining tiles re-pack to fill the space above the dock).
function _refitWallForDock() {
    if (_termMode !== 'wall' || !_grid) return;
    applyGridLayout();
}

function restoreFromDock(wid) {
    const stage = $('#term-stage');
    const item = Array.from(stage.querySelectorAll('.grid-stack-item'))
        .find(it => it.getAttribute('gs-id') === wid);
    if (item && _grid) {
        let c = {};
        try { c = JSON.parse(item.dataset.gsCoords || '{}'); } catch (e) { /* noop */ }
        item.removeAttribute('style');            // drop display:none + stale gridstack inline pos
        if (Number.isInteger(c.x)) item.setAttribute('gs-x', c.x);
        if (Number.isInteger(c.y)) item.setAttribute('gs-y', c.y);
        if (c.w) item.setAttribute('gs-w', c.w);
        if (c.h) item.setAttribute('gs-h', c.h);
        delete item.dataset.minimized;
        try { _grid.makeWidget(item); } catch (e) { /* noop */ }
    }
    setMinimized(wid, false);   // the dock repaints synchronously (it may have emptied)
    _refitWallForDock();        // dock may have emptied → grow the wall back
    _reportWatchForWid(wid);    // back on screen → resumes counting toward the shared window's size (CMX-175)
}

// Status tick: the share flags, the headers, the dock and the footers repaint from this
// poll (they read the agents store, and repaint here too when the rows are the same array),
// then the lazy dock and the layouts that rank by attention.
function _applyTermStatus(agents) {
    if (!agents) return;
    followAgents(agents);                     // each row's share flag (and owner presence with it)
    refreshPaneHeads();
    refreshTileFoots();
    refreshDock();
    _lazyDock(agents);   // dock the dispatcher's quiet workers; pop out the ones that block
    _applyAutoArrange(agents);     // wall redesign slice 2: re-rank the wall — no-op unless auto is on and the order changed
    _applyWallFocus(agents);       // wall redesign (Focus toggle): re-fit the one-large + strip layout — no-op unless focus is on and the target/pane-set changed
    // Wall tick is the fast path (4s) — refresh the tab signal here too so the
    // title/favicon update promptly on the flagship view, not just on the 30s
    // sidebar refresh. updateTabSignal is idempotent, so the two callers agree.
    updateTabSignal(agents);
}

// CMX-169: the single-mode pane a load opens. The last pane viewed on THIS device
// (localStorage, like pc_term_mode) if it is still online, else the first session in the
// sidebar's order (the top sidebar row), else the first option. The stored wid is
// paired with its session id and name: a wid a tmux restart handed to another session
// matches neither, so it is not trusted.
const LAST_PANE_KEY = 'pc_term_last_wid';
let _paneDefaulted = '';   // a pane picked before the sidebar had an order (adoptSidebarDefault)

function _rememberPane(wid, agents) {
    const a = (agents || []).find(x => x.window_id === wid) || {};
    try { localStorage.setItem(LAST_PANE_KEY, JSON.stringify({ wid, session_id: a.session_id || '', name: a.name || '' })); }
    catch (e) { /* private mode: nothing is remembered, a load still picks a pane */ }
}

function _storedPane(wids, agents) {
    let s;
    try { s = JSON.parse(localStorage.getItem(LAST_PANE_KEY)); } catch (e) { return ''; }
    if (!s || !wids.includes(s.wid)) return '';
    const a = (agents || []).find(x => x.window_id === s.wid) || {};
    const same = k => Boolean(s[k] && a[k] && s[k] === a[k]);
    const comparable = (s.session_id && a.session_id) || (s.name && a.name);
    return !comparable || same('session_id') || same('name') ? s.wid : '';
}

function _initialPane(wids, agents) {
    const stored = _storedPane(wids, agents);
    if (stored) return stored;
    const order = sidebarOrder();
    _paneDefaulted = order.length ? '' : (wids[0] || '');
    return orderByList(wids, order)[0] || '';
}

// The boot's first render runs before the sidebar has drawn (the scheduler starts the view tick
// as soon as /api/agents lands), so its default had no sidebar order to follow. Once the
// sidebar draws (nav.js calls this), move that default to the top sidebar row, unless a tap
// or a focus has already picked another pane.
function adoptSidebarDefault() {
    const was = _paneDefaulted;
    _paneDefaulted = '';
    const sel = $('#term-agent');
    if (!was || _termMode !== 'single' || !sel || sel.value !== was) return;
    const first = _mobileWids()[0];
    if (first && first !== was) { sel.value = first; renderTerminals(); }
}

async function renderTerminals(ctxRead) {   // ctxRead: refresh()'s context read (CMX-166)
    if (!TERMINALS_ON) return;   // terminals disabled — DOM (#term-stage etc.) absent
    let agents = _agentsCache;
    if (!agents || !agents.length) {
        const fresh = await api('/api/agents');
        agents = Array.isArray(fresh) ? fresh : [];   // an error body is not a pane list
        setAgentsCache(agents);
    }
    const wids = (agents || []).filter(a => a.online !== false && a.window_id).map(a => a.window_id);

    // Wall mode tiles 5+ terminals across a 12-col grid — useless at 375px.
    // Force single mode whenever the viewport is below the mobile breakpoint;
    // the CSS already hides the toggle + grid presets at the same width.
    if (window.matchMedia('(max-width: 768px)').matches && _termMode === 'wall') {
        _termMode = 'single';
        $('#term-mode-single').classList.add('btn-accent');
        $('#term-mode-wall').classList.remove('btn-accent');
    }

    // Sync the mode toggle's active state (the default is now wall, and the
    // choice is persisted, so the HTML's static class can't be relied on).
    $('#term-mode-single').classList.toggle('btn-accent', _termMode === 'single');
    $('#term-mode-wall').classList.toggle('btn-accent', _termMode === 'wall');

    const sel = $('#term-agent');
    sel.style.display = _termMode === 'single' ? '' : 'none';
    termMode.set(_termMode);   // the keybar island shows its desktop bar in single mode, the toolbar and dock show on the wall
    mountWallToolbar($('#wall-toolbar-island'));
    mountDock($('#term-min-dock'));
    if (sel.options.length !== wids.length) {
        const prev = sel.value;
        sel.innerHTML = wids.map(w => `<option value="${attrEsc(w)}">${escHtml(_paneTitle(w))}</option>`).join('');
        sel.value = prev && wids.includes(prev) ? prev : _initialPane(wids, agents);
    }

    // Order-insensitive: a surgically-appended tile lands at the end of
    // _renderedWids, which need not match /api/agents order — sorting both
    // sides keeps that from reading as "changed" and forcing a full rebuild.
    // Keyed by wid, so a rename never changes the signature.
    const sig = _termMode + '|' + (sel.value || '') + '|' + wids.slice().sort().join(',');
    if (sig === _termSig) return;          // unchanged -> keep live iframes, no reload
    _termSig = sig;

    // Resolve readiness BEFORE building the stage so agents already in the port
    // map render their iframe with no placeholder flash. Already-ready wids
    // short-circuit (no fetch); only not-yet-ready agents incur a cheap probe.
    await Promise.all(wids.map(async w => {
        if (!_termReady.has(w) && await _checkReady(w)) _termReady.add(w);
    }));

    // A full stage rebuild is imminent — cancel any in-flight readiness pollers
    // (they're re-seeded from the fresh DOM below) and drop the maximized-state
    // body class so it doesn't outlive the element it was tracking.
    _stopAllReadyPolls();
    document.body.classList.remove('pane-is-maximized');
    // A fresh single-pane header is about to render at its default (non-full)
    // icon/title — don't leave a stale term-mobile-full body class hiding
    // chrome the new header no longer claims to control.
    document.body.classList.remove('term-mobile-full');

    // Ready -> real iframe; not ready -> spinner placeholder (polled into an
    // iframe by _startPlaceholderPolls once the ttyd port lands).
    const frame = w => _termReady.has(w)
        ? `<iframe class="term-frame" allow="clipboard-read; clipboard-write" src="${_termFrameSrc(w)}" title="${escHtml(_paneTitle(w))}"></iframe>`
        : _termPlaceholder(w);
    const stage = $('#term-stage');
    if (!wids.length) {
        destroyGrid();
        stage.className = '';
        stage.innerHTML = '<div style="padding:20px; text-align:center; color:var(--text-dim);">No active agents</div>';
        _renderedWids = [];
    } else if (_termMode === 'wall') {
        _renderedWids = wids.slice();   // set before buildWall so the lazy dock sees current wids
        buildWall(wids);
        // Fit happens at the end, AFTER the taskbar renders — see _refitWallForDock below.
    } else {
        destroyGrid();
        stage.className = 'term-single';
        const sw = sel.value || wids[0];
        _rememberPane(sw, agents);   // every single-mode switch lands here: sidebar tap, swipe, dropdown, focus
        stage.innerHTML = `<div class="term-pane">${_paneHeadSlot(sw, false)}${frame(sw)}${_footSlot(sw, false)}</div>`;
        _mountTileParts(stage);
        _renderedWids = [sw];
    }
    // Seed readiness pollers for whatever placeholders we just rendered.
    _startPlaceholderPolls();
    _applyTermStatus(_agentsCache);   // paint the panes and the dock from cached state
    loadContext(ctxRead).catch(() => {});   // the footers drew the last context; this refreshes it

    // Fit the wall AFTER the taskbar is at its final rendered height, so the grid
    // leaves exactly the right room above it (dock height feeds _wallFill).
    _refitWallForDock();

    pinKeybar();              // CMX-102: a freshly built pane re-fits to the keybar if the keyboard is up
    _bindHeaderSwipe();       // header swipe → prev/next agent (idempotent)
    _bindFocusPromote();      // click a strip pane's header to promote it while Focus is on (idempotent)
    refreshRooms();           // paint room accents on the first render, not 4s later
}

// ---- Reactive pane lifecycle ----------------------------------------------
//
// A dead ttyd iframe (window killed / `claude` exited / killed elsewhere) would
// otherwise sit blank until the 30s renderTerminals cycle. Instead we poll the
// live agent set every TERM_REFRESH_MS while the terminals tab is open, and:
//   - drop tiles for wids that vanished (no flicker for survivors), and
//   - in WALL mode, rebuild when a NEW wid appears (layout genuinely changed);
//     in SINGLE mode, only refresh the dropdown so the new shell is selectable —
//     the displayed pane's iframe is never reloaded.
// Panes whose windows still exist are left completely untouched. Because the
// diff is by wid, a window rename never reads as a drop+add.

// ---- Focus ring -----------------------------------------------------------
//
// Highlight the pane whose embedded terminal currently holds browser focus.
// The terminals are same-origin iframes, so a clicked one becomes
// document.activeElement. We POLL for that rather than relying on window
// 'blur': blur fires when focus first leaves the parent into an iframe, but
// clicking straight from one iframe to another never refocuses the parent, so
// blur alone would leave the ring stuck on the first pane. The poll is a cheap
// activeElement read and only touches the DOM when the focused pane changes.
// Pure parent-frame — the iframe needs no cooperation — and it tracks the SAME
// focus that drives tmux's focus-events to Claude Code.
//
// CMX-104: the parent's activeElement is NOT the authority. Live, after
// CMX-90, Liav typed into one pane (its xterm drew the solid, focused cursor)
// while the ring sat on the OTHER pane (hollow, blurred cursor) — the parent's
// activeElement still named the frame he had left. The authority is each
// frame's OWN document.hasFocus(): it is the focus xterm's cursor and tmux's
// focus-events follow, so the ring now asks every term frame that question
// first and only falls back to activeElement when no frame can answer (an
// opaque frame) or the whole browser window is unfocused (keep marking the
// pane focus will return to — a screenshot tool steals OS focus). And every
// tick re-asserts the invariant on the DOM itself — exactly one .term-focused,
// on that pane — instead of trusting the remembered _focusedPane: a ring left
// on any pane the tracker no longer remembers is cleared, not kept forever.
let _focusPoll = null;
let _focusedPane = null;

function _frameDocHasFocus(frame) {
    let doc = null;
    try { doc = frame.contentDocument; } catch (e) { /* cross-doc guard */ }
    if (!doc || typeof doc.hasFocus !== 'function') return null;   // can't tell
    try { return doc.hasFocus(); } catch (e) { return null; }
}

function _focusedTermFrame() {
    const frames = Array.from(document.querySelectorAll('iframe.term-frame'));
    const owner = frames.find(f => _frameDocHasFocus(f) === true);
    if (owner) return owner;
    const ae = document.activeElement;
    const aeFrame = (ae && ae.classList && ae.classList.contains('term-frame')) ? ae : null;
    if (!aeFrame) return null;
    let pageFocused = true;
    try { pageFocused = document.hasFocus(); } catch (e) { /* assume focused */ }
    // The page has focus but this frame's own document says it doesn't: the
    // keyboard is NOT there, whatever activeElement claims (the CMX-104 bug).
    if (pageFocused && _frameDocHasFocus(aeFrame) === false) return null;
    return aeFrame;
}

function _applyTermFocus() {
    const frame = _focusedTermFrame();
    const pane = frame ? frame.closest('.grid-stack-item-content, .term-pane') : null;
    document.querySelectorAll('.term-focused').forEach(el => {
        if (el !== pane) el.classList.remove('term-focused');
    });
    if (pane && !pane.classList.contains('term-focused')) pane.classList.add('term-focused');
    _focusedPane = pane;
}

function startTermFocusTracking() {
    if (_focusPoll) return;                        // idempotent
    _focusPoll = setInterval(_applyTermFocus, 250);
    window.addEventListener('focus', _applyTermFocus);   // returning to the parent clears it promptly
}

// Bring an existing pane to the user's attention: switch to the wall, restore it
// if minimized, scroll it into view, focus its iframe (which lights the focus
// ring), and flash it briefly. The launcher's dedup calls this when you click a
// project that already has a live agent, so the click lands you ON that pane
// instead of silently no-op'ing because the wall was already showing.
function focusPaneByWid(wid) {
    if (!TERMINALS_ON || !wid) return;
    // An explicit focus beats the stored pane and becomes it: stored first, so a focus that
    // lands before the pane list (a deep link at boot) is what the first render opens.
    _paneDefaulted = '';
    _rememberPane(wid, _agentsCache);
    if (typeof selectView === 'function') selectView('terminals');
    if (_termMode === 'single') {
        const sel = $('#term-agent');
        if (sel && sel.value !== wid) { sel.value = wid; renderTerminals(); }
        return;
    }
    // Defer so selectView's render settles before we hunt for the tile.
    setTimeout(() => {
        if (_isMinimized(wid)) restoreFromDock(wid);
        const item = Array.from($('#term-stage').querySelectorAll('.grid-stack-item'))
            .find(it => it.getAttribute('gs-id') === wid);
        if (!item) return;
        item.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        const ifr = item.querySelector('iframe.term-frame');
        if (ifr) {
            try { ifr.contentWindow.focus(); } catch (e) { /* cross-doc guard */ }
            // CMX-114 (live-broken in the real browser, cmx-112's jsdom guard was
            // green while the feature was dead): contentWindow.focus() alone does
            // NOT reliably move the browser's actual keyboard focus into this frame
            // when switching FROM another focused pane — the border moves but
            // typing doesn't follow. Focus the iframe ELEMENT itself too, and route
            // focus into the terminal's own textarea (exposed as `term` by ttyd's
            // page, the same global the share dialog reads the pane size from) so typing lands
            // in the pane, not just highlights it. Both are called unconditionally
            // (not one-as-fallback-for-the-other) — that's what actually moves live
            // browser focus.
            try { ifr.focus(); } catch (e) { /* cross-doc guard */ }
            try {
                const t = ifr.contentWindow.term;
                if (t && typeof t.focus === 'function') t.focus();
            } catch (e) { /* cross-doc guard */ }
        }
        const content = item.querySelector('.grid-stack-item-content') || item;
        content.classList.add('pane-flash');
        setTimeout(() => content.classList.remove('pane-flash'), 1100);
    }, 60);
}

function startTermTimer() {
    if (!TERMINALS_ON) return;
    stopTermTimer();
    _termTimer = setInterval(termTick, TERM_REFRESH_MS);
    startTermFocusTracking();
}

function stopTermTimer() {
    if (_termTimer) { clearInterval(_termTimer); _termTimer = null; }
}

// ---- Background-tab teardown ----------------------------------------------
//
// Grouped tmux sessions SHARE one real window, and a tmux window has exactly one
// size. With `window-size largest` (scripts/agent-terminals.sh), a wall left open
// in a backgrounded tab keeps its ttyd WebSocket alive indefinitely — the
// keepalive pings auto-pong even while hidden — and pins every shared window to
// that ghost's dimensions, so the wall you're actively viewing gets its rows/cols
// CROPPED to the stale tab's size. Terminal activity can't tell a quiet-but-
// watched pane from an abandoned one (that's what the old server-side reaper got
// wrong, and why it disconnected live panes). The browser CAN: Page Visibility
// reports when THIS tab is hidden. So when the tab goes hidden past a short grace,
// blank its ttyd iframes — closing their sockets, which drops the backing tmux
// clients so they stop distorting window-size — and reconnect when it's shown
// again. A quick tab flip stays under the grace, so there's no needless reload.
//
// CONTENTION GATE: a pane is only torn down when its window has >1 viewer
// (/api/term/clients). With a single viewer there's no size contention to resolve
// — a tmux window has one size shared by all its clients, so a lone client always
// fits — and tearing it down would just churn the connection for nothing. So a
// backgrounded wall that's the only thing watching its agents keeps its terminals;
// teardown only fires for panes a second tab/device is also viewing.
const TERM_HIDE_GRACE_MS = 45000;   // hidden this long → release contended ttyd clients
let _termSuspended = false;
let _termHideTimer = null;

// /term/<wid>/ → wid. Reads the live src (or the stashed one once blanked).
function _widOfFrame(ifr) {
    const src = ifr.dataset.suspendedSrc || ifr.getAttribute('src') || '';
    const m = src.match(/\/term\/([^/]+)\//);
    return m ? decodeURIComponent(m[1]) : null;
}

async function _teardownTermFrames() {
    let counts;
    try { counts = await api('/api/term/clients'); }
    catch (e) { return; }                              // can't tell contention → disturb nothing
    if (document.visibilityState !== 'hidden') return; // became visible mid-fetch → abort
    let released = 0;
    document.querySelectorAll('#term-stage iframe.term-frame').forEach(ifr => {
        const src = ifr.getAttribute('src') || '';
        if (!src || src === 'about:blank') return;      // nothing live to release
        const wid = _widOfFrame(ifr);
        if (!wid || (counts[wid] || 0) <= 1) return;    // sole viewer → leave it connected
        ifr.dataset.suspendedSrc = src;                 // stash for restore
        ifr.src = 'about:blank';                         // unload → WS close → tmux client drops
        released++;
    });
    if (released) _termSuspended = true;   // only suspend reactive ticks if we actually released
}

function _restoreTermFrames() {
    _termSuspended = false;
    document.querySelectorAll('#term-stage iframe.term-frame[data-suspended-src]').forEach(ifr => {
        ifr.src = ifr.dataset.suspendedSrc;             // reconnect, freshly sized to the now-visible tile
        delete ifr.dataset.suspendedSrc;
    });
    // Reconcile any agent adds/drops we skipped while suspended (guarded below).
    if (TERMINALS_ON && currentTab === 'terminals') termTick();
}

// ---- Per-client ignore-size (CMX-175) ---------------------------------------
//
// The teardown above resolves size contention by DISCONNECTING a backgrounded
// tab's ttyd clients after TERM_HIDE_GRACE_MS — effective, but it kills the
// socket (a visible reconnect) and only fires once a tab has been hidden a
// while. tmux's per-client `ignore-size` flag gets the SAME outcome — THIS tab's
// clients stop dragging the shared window to their own geometry — instantly and
// non-destructively, so report it the moment a tile stops (or starts) being
// watched: tab backgrounded/foregrounded, or pane minimized/restored. Both
// mechanisms coexist: this one fixes the acute sizing fight; the teardown still
// exists to reclaim --max-clients slots from a genuinely abandoned tab.
//
// A tile only counts as "watched" while the browser tab is visible AND it isn't
// collapsed into the dock — a minimized tile is still a live tmux client (just
// CSS-hidden), so left alone it would go on dictating the shared window's size
// from inside the dock.
function _isWatched(wid) {
    return document.visibilityState !== 'hidden' && !_isMinimized(wid);
}

// /term/<wid>/?cid=... → cid. Mirrors _widOfFrame's src-parsing (including the
// suspended-src fallback so a torn-down tile's last-known cid is still visible).
function _cidOfFrame(ifr) {
    const src = ifr.dataset.suspendedSrc || ifr.getAttribute('src') || '';
    const m = src.match(/[?&]cid=([^&]+)/);
    return m ? decodeURIComponent(m[1]) : null;
}

// Report one iframe's current watched state to the ONE tmux client app.py's
// term_ws matched to its cid — never the whole session, so a sibling tab/device
// watching the same wid is untouched. Best-effort: a missed flip just leaves
// tmux's default (`largest` fights over every attached client, today's behavior).
function _reportWatch(ifr) {
    const src = ifr.getAttribute('src') || '';
    if (!src || src === 'about:blank') return;      // torn down — nothing to flag
    const wid = _widOfFrame(ifr);
    const cid = _cidOfFrame(ifr);
    if (!wid || !cid) return;
    api('/api/term/' + encodeURIComponent(wid) + '/watch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cid, watching: _isWatched(wid) }),
    }).catch(() => {});
}

function _reportAllWatch() {
    document.querySelectorAll('#term-stage iframe.term-frame').forEach(_reportWatch);
}

function _reportWatchForWid(wid) {
    const ifr = Array.from(document.querySelectorAll('#term-stage iframe.term-frame'))
        .find(f => _widOfFrame(f) === wid);
    if (ifr) _reportWatch(ifr);
}

// Single registration at module load. TERMINALS_ON is checked at fire time (it
// may not be resolved yet here), mirroring the window 'resize' handler above.
document.addEventListener('visibilitychange', () => {
    if (!TERMINALS_ON) return;
    clearTimeout(_termHideTimer);
    _reportAllWatch();
    if (document.hidden) {
        _termHideTimer = setTimeout(_teardownTermFrames, TERM_HIDE_GRACE_MS);
    } else if (_termSuspended) {
        _restoreTermFrames();
    }
});

async function termTick() {
    if (!TERMINALS_ON || currentTab !== 'terminals') return;
    if (_termSuspended) return;   // tab hidden: don't add/reconnect iframes we just released
    let agents;
    try { agents = await api('/api/agents'); } catch (e) { return; }   // transient — try again next tick
    setAgentsCache(agents);
    const live = (agents || []).filter(a => a.online !== false && a.window_id).map(a => a.window_id);
    const liveSet = new Set(live);

    // Drop any rendered pane whose wid is gone — surgically, so the other
    // tiles' iframes are never reloaded. (A rename keeps the wid, so survivors
    // are untouched.)
    const gone = _renderedWids.filter(w => !liveSet.has(w));
    gone.forEach(dropTerminalPane);

    // Absorb any newly-live agent (e.g. a "+ New shell"): wall mode appends a
    // tile surgically, single mode just refreshes the dropdown. Neither reloads
    // an iframe already on screen. (See _absorbFreshTerminals.)
    await _absorbFreshTerminals(live);

    // Refresh the busy/idle/waiting dots from this poll's fresh status. Also
    // recolour the sidebar dots off the SAME poll so they stay in lockstep with
    // the wall instead of lagging until the next 30s refresh, and refresh labels
    // in case a rename changed a pane's friendly name.
    _applyTermStatus(agents);
    _refreshPaneLabels();
    refreshRooms();   // rooms can also be made from the CLI/hooks — the wall follows
    try {
        const ctx = await api('/api/agents/context');
        if (Array.isArray(ctx)) context.set(ctx);   // the footers repaint from it
        refreshTileFoots();                          // …even when it is the same array
        updateCtxCache(ctx);
    } catch (e) { /* keep prior fills */ }
    // Full sidebar re-render off the same poll: keeps the dots in lockstep with
    // the wall AND lets the "Needs you" triage cluster reorder live as sessions
    // start/finish waiting. Cheap for a short list; group collapse state lives in
    // localStorage so it survives the rebuild.
    renderSidebarAgents(agents);
}

// Relabel everything that shows a window's label, WITHOUT touching an iframe: a rename
// only changes display text. The headers and the dock's chips repaint from the agents store
// (an inline rename in progress is the header's own state, never clobbered); legacy redraws
// the single-mode dropdown.
function _refreshPaneLabels() {
    if (!TERMINALS_ON) return;
    refreshPaneHeads();
    if (_termMode === 'wall') {
        refreshDock();   // chips carry labels too
    } else {
        const sel = $('#term-agent');
        if (sel) Array.from(sel.options).forEach(o => {
            const lbl = _paneTitle(o.value);
            if (o.textContent !== lbl) o.textContent = lbl;
        });
    }
}

// ---- Fast switch (Alt+1..9) + palette-first (Ctrl/⌘+K) ---------------------
// CMX-85: Alt+N opens the Nth visible SIDEBAR row, and holding Alt shows those
// numbers on the rows (nav.js `altSwitchKeydown`) — the sidebar is the one
// numbering; the panes carry none. CMX-116: the palette is the primary,
// discoverable switcher — Alt+N is the power-user fallback.

// Ctrl/⌘+K — the same combo the parent chrome's palette shortcut uses
// (nav.js). Matched the same way here so a pane's own keydown can trigger it.
function _isPaletteKey(e) {
    return (e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && (e.key === 'k' || e.key === 'K');
}

// Once Alt+N focuses a pane, keystrokes land in that ttyd iframe's OWN
// document — a same-origin iframe's keydown never bubbles to the parent, so
// nav.js's document listener goes deaf to the next Alt+N (or Ctrl/⌘+K) until focus
// manually returns to the parent. Wire the same shortcuts inside each pane's
// document too, as soon as it (re)loads. Unlike the parent, no INPUT/TEXTAREA
// guard is needed: ttyd's DOM holds nothing but the terminal, and its own
// input IS a textarea (the xterm helper) — excluding it would defeat the
// whole point.
//
// CMX-114 (live-broken in the real browser, cmx-112's jsdom guard was green
// while the feature was dead): a real keydown targets xterm's helper textarea
// and xterm handles/consumes it FIRST, so it never reliably reaches a
// bubble-phase listener on `doc` — only a synthetic `dispatchEvent` does,
// which is exactly why the old jsdom guard passed on a dead feature. Register
// in the CAPTURE phase with `stopImmediatePropagation` so this fires BEFORE
// xterm's own handler, both switching panes/opening the palette AND stopping
// the stray Alt-digit or Ctrl+K from reaching the shell (xterm otherwise
// treats Ctrl+K as readline's kill-to-end-of-line).
//
// CMX-116: a SET of shortcuts, not a second bespoke injector — Ctrl/⌘+K is
// the primary, discoverable pane switcher (the palette floats open panes to
// the top, see nav.js `_openPaneItems`); Alt+1..9 stays as the direct-jump
// power-user fallback. Was `_wireIframeAltSwitch`, generalized in place so
// "what shortcuts reach a focused pane" can't drift between the two.
function _wireIframeShortcuts(ifr) {
    let doc;
    try { doc = ifr.contentDocument; } catch (e) { return; }   // cross-doc guard
    if (!doc) return;
    doc.addEventListener('keydown', e => {
        if (altSwitchKeydown(e)) {
            e.preventDefault();
            e.stopImmediatePropagation();
            return;
        }
        if (_isPaletteKey(e)) {
            e.preventDefault();
            e.stopImmediatePropagation();
            if (typeof openPalette === 'function') openPalette();
        }
    }, true);
    // Releasing Alt inside a pane hides the sidebar numbers (CMX-85).
    doc.addEventListener('keyup', e => { if (e.key === 'Alt') setAltHeld(false); }, true);
}

// 'load' doesn't bubble, but it still traverses ancestors during the CAPTURE
// phase, so a single capturing listener on `document` catches every
// `.term-frame` iframe's load — including future ones — with no per-tile
// wiring needed at creation time. Re-running on every load (not just the
// first) is required: each navigation swaps in a brand-new Document, which
// discards whatever listener was attached to the one before it.
document.addEventListener('load', e => {
    const ifr = e.target;
    if (!ifr || ifr.tagName !== 'IFRAME' || !ifr.classList.contains('term-frame')) return;
    _wireIframeShortcuts(ifr);
    _reportWatch(ifr);   // register this fresh connection's watched state (CMX-175)
    pinKeybar();         // CMX-154: a pane's terminal (first view, tile switch) fits the box it landed in
}, true);

// Rebuild the single-mode agent dropdown's <option> list in place, preserving
// the current selection. Options carry the wid as value + the friendly label as
// text. Does NOT touch the displayed pane or its iframe — so a newly spawned
// shell becomes selectable without reloading what's on screen.
function _refreshTermOptions(wids) {
    const sel = $('#term-agent');
    if (!sel) return;
    const prev = sel.value;
    sel.innerHTML = wids.map(w => `<option value="${attrEsc(w)}">${escHtml(_paneTitle(w))}</option>`).join('');
    if (prev && wids.includes(prev)) sel.value = prev;
}

// Recompute the render signature from what's actually on screen so the next
// renderTerminals() sees the diffed state as the baseline (no spurious rebuild
// of the survivors after we've already dropped a dead tile).
function _syncTermSig() {
    const sel = $('#term-agent');
    _termSig = _termMode + '|' + (sel ? (sel.value || '') : '') + '|' + _renderedWids.slice().sort().join(',');
}

function dropTerminalPane(wid) {
    _stopReadyPoll(wid);   // pane is going away — kill any in-flight readiness poll
    _termReady.delete(wid);  // a re-spawn under the same wid must re-probe its port
    stopOwnerPresence(wid);  // tear down its presence session
    const stage = $('#term-stage');
    if (_termMode === 'wall' && _grid) {
        const el = Array.from(stage.querySelectorAll('.grid-stack-item'))
            .find(it => it.getAttribute('gs-id') === wid);
        // A minimized tile was already pulled from the engine — removeWidget
        // would no-op and leave a hidden orphan, so drop the element directly.
        if (el && el.dataset.minimized === '1') el.remove();
        else if (el) { try { _grid.removeWidget(el, true); } catch (e) { el.remove(); } }
    } else if (_termMode === 'single') {
        // Single view shows one pane; if that agent died, swap the iframe for a
        // brief "session ended" overlay rather than leaving a blank frame.
        const pane = stage.querySelector('.term-pane');
        if (pane) {
            const fr = pane.querySelector('.term-frame');
            if (fr) fr.remove();
            if (!pane.querySelector('.pane-ended')) {
                const ov = document.createElement('div');
                ov.className = 'pane-ended';
                ov.textContent = `${_paneTitle(wid)}: session ended`;
                pane.appendChild(ov);
            }
        }
    }
    _renderedWids = _renderedWids.filter(w => w !== wid);
    setMinimized(wid, false);   // its chip leaves the dock
    forgetAutoDock(wid);        // the window is gone: its lazy-dock decision dies with it
    _refitWallForDock();        // the dock may have emptied → re-fit the wall above it
    _syncTermSig();
}

// ---- Wall mode: draggable / resizable / persisted Gridstack tiles ----------

function destroyGrid() {
    if (_grid) { try { _grid.destroy(false); } catch (e) { /* noop */ } _grid = null; }
}

// One wall tile's HTML (a .grid-stack-item). Ready agents get the live iframe;
// not-yet-ready ones get the spinner placeholder (swapped in place by the
// readiness poll). Shared by the full buildWall and the surgical addWallTiles.
// gs-id is the stable wid so a rename never re-keys the tile.
function _wallTileHTML(wid, x, y, w, h) {
    const body = _termReady.has(wid)
        ? `<iframe class="term-frame" allow="clipboard-read; clipboard-write" src="${_termFrameSrc(wid)}" title="${escHtml(_paneTitle(wid))}"></iframe>`
        : _termPlaceholder(wid);
    const pinnedCls = _isPinned(wid) ? ' pane-pinned' : '';
    return `<div class="grid-stack-item${pinnedCls}" gs-id="${escHtml(wid)}" gs-x="${x}" gs-y="${y}" gs-w="${w}" gs-h="${h}">
  <div class="grid-stack-item-content">
    ${_paneHeadSlot(wid, true)}
    ${_recapSlot(wid)}
    ${body}
    ${_footSlot(wid, true)}
  </div>
</div>`;
}

function buildWall(wids) {
    const stage = $('#term-stage');
    destroyGrid();
    stage.className = '';
    stage.innerHTML = '<div class="grid-stack"></div>';
    const gsEl = stage.querySelector('.grid-stack');

    let saved = {};
    try { saved = JSON.parse(localStorage.getItem('pc_wall_layout') || '{}'); } catch (e) { /* noop */ }

    // renderTerminals pre-resolved _termReady before calling buildWall, so
    // existing panes never flash a placeholder.
    gsEl.innerHTML = wids.map((wid, i) => {
        const s = saved[wid] || {};
        const x = Number.isInteger(s.x) ? s.x : (i % 2) * 6;
        const y = Number.isInteger(s.y) ? s.y : Math.floor(i / 2) * 5;
        const w = s.w || 6, h = s.h || 5;
        return _wallTileHTML(wid, x, y, w, h);
    }).join('');
    _mountTileParts(gsEl);   // before GridStack.init: each tile's `.gs-grip` handle must exist

    _grid = GridStack.init({
        column: 12,
        cellHeight: 70,
        margin: 6,
        float: true,
        handle: '.gs-grip',
        // scroll:false disables gridstack's auto-scroll-near-edge, which otherwise
        // scrolls the page out from under you mid-drag ("slip and scroll down").
        draggable: { handle: '.gs-grip', scroll: false },
        resizable: { handles: 'e, se, s, sw, w' },
        columnOpts: { breakpoints: [{ w: 768, c: 1 }] },
    }, gsEl);

    const persist = () => {
        const out = {};
        _grid.save(false).forEach(nd => {
            if (nd.id != null) out[nd.id] = { x: nd.x, y: nd.y, w: nd.w, h: nd.h };
        });
        localStorage.setItem('pc_wall_layout', JSON.stringify(out));
    };
    // iframes swallow the mouse mid-drag — disable their pointer-events while
    // a drag/resize is in flight, then persist the new layout. `term-dragging`
    // (drag only, not resize) lets CSS hide the live iframes during a drag so the
    // wall isn't compositing N terminals while tiles move — keeps the drag snappy.
    const guard = on => gsEl.classList.toggle('gs-dragging', on);
    // Drag-only marker on both the grid (hides iframes) and body (locks page
    // scroll). gridstack briefly grows the grid into a phantom row mid-drag,
    // which would pop a scrollbar and let the page jump/auto-scroll — pin it.
    const dragMode = on => {
        gsEl.classList.toggle('term-dragging', on);
        document.body.classList.toggle('term-dragging', on);
    };
    // Locked swap shouldn't grow the wall: freeze the grid box to its fitted
    // height + clip overflow so a tile dragged low can't push the wall past the
    // viewport (no slip-below, dock stays put). Unlocked drags grow freely.
    const lockDragBox = on => {
        if (on) gsEl.style.maxHeight = gsEl.offsetHeight + 'px';
        else gsEl.style.maxHeight = '';
        gsEl.classList.toggle('term-lock-drag', on);
    };
    _grid.on('dragstart', (e, el) => { guard(true); dragMode(true); if (_layout().locked) { _snapshotForSwap(); lockDragBox(true); } });
    _grid.on('resizestart', () => guard(true));
    _grid.on('drag', (e, el) => _swapDragHover(el));   // live highlight of the swap target
    // Locked mode turns a drag into a pure swap: exchange the dragged pane's full
    // geometry with whatever pane it was dropped on (revert if dropped on empty).
    _grid.on('dragstop', (e, el) => { guard(false); dragMode(false); if (_layout().locked) _doSwap(el); lockDragBox(false); persist(); });
    _grid.on('resizestop', () => { guard(false); persist(); });

    // Re-apply persisted minimized state: pull those tiles back out of the grid
    // (their iframes still loaded, just hidden); the dock draws their chips.
    wids.forEach(wid => {
        if (!_isMinimized(wid)) return;
        const item = gsEl.querySelector(`.grid-stack-item[gs-id="${_cssEsc(wid)}"]`);
        if (item) _minimizeItem(item);
    });
    _autoOrderSig = '';    // fresh tile set: force the next auto-arrange apply, don't trust a stale sig
    _focusOrderSig = '';   // same, for Focus: force the next _applyWallFocus apply on a fresh tile set
    _applyWallInteractivity();   // restore the locked (swap-on-drag) / auto-arrange / focus feel across reloads
    replayRoomAccents();   // rebuilt tiles: repaint room accents from the last poll
}

// Surgically append new tiles to an existing wall WITHOUT touching the live
// iframes of panes already on screen. This is the fix for the whole-wall black
// flash: previously a single new window forced a full buildWall(), whose
// `innerHTML =` destroyed and recreated every iframe. Returns false if there's
// no live grid to append to (caller falls back to a full render).
async function addWallTiles(wids) {
    const gsEl = $('#term-stage').querySelector('.grid-stack');
    if (!_grid || _termMode !== 'wall' || !gsEl) return false;

    // Pre-resolve readiness so an already-ready agent renders its iframe with
    // no placeholder flash (mirrors renderTerminals()).
    await Promise.all(wids.map(async w => {
        if (!_termReady.has(w) && await _checkReady(w)) _termReady.add(w);
    }));

    let saved = {};
    try { saved = JSON.parse(localStorage.getItem('pc_wall_layout') || '{}'); } catch (e) { /* noop */ }
    // Stack new tiles below the lowest existing one unless they have a saved spot.
    let nextY = _grid.engine.nodes.reduce((m, nd) => Math.max(m, (nd.y || 0) + (nd.h || 0)), 0);

    wids.forEach(wid => {
        const present = Array.from(gsEl.querySelectorAll('.grid-stack-item'))
            .some(it => it.getAttribute('gs-id') === wid);
        if (present) return;
        const s = saved[wid] || {};
        const w = s.w || 6, h = s.h || 5;
        const x = Number.isInteger(s.x) ? s.x : 0;
        const y = Number.isInteger(s.y) ? s.y : nextY;
        if (!Number.isInteger(s.y)) nextY += h;
        const tmp = document.createElement('div');
        tmp.innerHTML = _wallTileHTML(wid, x, y, w, h);
        const el = tmp.firstElementChild;
        gsEl.appendChild(el);
        _mountTileParts(el);    // its header (and `.gs-grip` handle) before Gridstack adopts it
        _grid.makeWidget(el);   // v10: adopt an already-appended element as a widget
        if (!_renderedWids.includes(wid)) _renderedWids.push(wid);
    });
    _startPlaceholderPolls();   // seed readiness polls for any placeholders we just added
    _applyWallInteractivity();  // a tile added while locked/auto must inherit no-resize / no-move
    _refitWallForDock();        // re-fit the wall to the stage
    replayRoomAccents();        // a fresh tile already in a room wears its accent at once
    _syncTermSig();
    return true;
}

// Absorb agents that are live but not yet on the wall/pane, without reloading
// what's already on screen. Wall mode appends tiles surgically; single mode
// only refreshes the dropdown so a new shell is selectable (the displayed
// iframe is never touched). Shared by the 4s termTick and the SSE windows event.
async function _absorbFreshTerminals(live) {
    if (_termSuspended) return;   // tab hidden: defer new tiles until _restoreTermFrames re-ticks
    const fresh = live.filter(w => !_renderedWids.includes(w));
    if (!fresh.length) return;
    if (_termMode === 'wall') {
        if (!(await addWallTiles(fresh))) { _termSig = ''; await renderTerminals(); }
    } else {
        _refreshTermOptions(live);
    }
}

// GridStack.init params (must stay in sync with buildWall) — needed to convert
// the visible stage height into grid rows so a layout fills the viewport.
const WALL_CELL_H = 70, WALL_MARGIN = 6;

// Fill geometry for the visible wall: how many grid rows fit between the stage
// top and the viewport bottom, plus the per-row px height that makes exactly
// that many rows fill the space (so layouts reach the bottom with no gap).
// GridStack renders ~WALL_CELL_H px/row by default; we override cellHeight to
// the exact divisor in applyGridLayout.
function _wallFill() {
    const stage = $('#term-stage');
    const top = stage ? stage.getBoundingClientRect().top : 120;
    // The wall lives inside the scrollable .canvas, whose padding-bottom sits
    // BELOW the wall — so the real floor is the canvas content-box bottom, not
    // the viewport. Measuring it (rather than window.innerHeight) stops the wall
    // from overrunning that padding and pushing the canvas into a scroll. Falls
    // back to the viewport if the canvas isn't found.
    const canvas = stage ? stage.closest('.canvas') : null;
    let floorY = window.innerHeight;
    if (canvas) {
        const cr = canvas.getBoundingClientRect();
        const padB = parseFloat(getComputedStyle(canvas).paddingBottom) || 0;
        floorY = Math.min(window.innerHeight, cr.bottom) - padB;
    }
    // The dock lives below the stage, so its height (+ its top margin gap, and
    // its NEGATIVE bottom margin that sinks it into the canvas padding —
    // CMX-397) eats into the space the wall may fill. Subtract its margin box
    // when it's showing, read off the computed style so the CSS owns the
    // numbers. Phones force single mode (no wall, no dock), so skip it there.
    const dock = $('#term-min-dock');
    let dockH = 0;
    if (!_isMobileTerm() && dock && !dock.hidden) {
        const ds = getComputedStyle(dock);
        dockH = dock.getBoundingClientRect().height
            + (parseFloat(ds.marginTop) || 0) + (parseFloat(ds.marginBottom) || 0);
    }
    const avail = Math.max(240, floorY - top - dockH - 4);  // leave a hair at the bottom
    const rows = Math.max(3, Math.floor(avail / WALL_CELL_H));
    // CMX-414: NOT floored. A whole-px row height strands up to rows−1 px
    // under the Wall (the height the tighter toolbar row freed at the top just
    // reappeared at the bottom); GridStack takes a fractional cellHeight as-is.
    const cellPx = Math.max(40, avail / rows);      // exact divisor -> fills
    return { rows, cellPx };
}

// ---- Layout lock (swap-on-drop) --------------------------------------------
// GridStack's native swap only fires for equal-sized, touching tiles, so it
// can't "swap and resize" different-sized panes. Instead, lock just turns
// resize off and we implement the swap ourselves on dragstop: snapshot every
// tile's geometry at dragstart, then on drop find the pane under the cursor and
// exchange the two tiles' full {x,y,w,h} (others restored to the snapshot).
// Dropped on empty space → revert. float stays true so the drop lands exactly
// where the cursor is (no gravity snapping to confuse target detection).
let _swapSnapshot = null;          // wid -> {x,y,w,h} captured at dragstart while locked
let _swapHoverWid = null;          // pane currently outlined as the swap target during a locked drag

// Push the current lock + auto-arrange + focus state onto the live grid. Safe
// to call any time _grid exists (e.g. right after buildWall, so a reload
// restores the locked/auto/focus feel). Auto-arrange and Focus both COMPUTE
// the layout (rankOrder / focusLayout), so either one disables move AND
// resize outright — a manual drag/resize would just get overwritten by the
// next poll's re-apply, so disabling is the honest UI rather than a confusing
// revert. They are mutually exclusive (toggleWallAuto/toggleWallFocus enforce
// it), so at most one of them is ever true here.
function _applyWallInteractivity() {
    if (!_grid) return;
    const { locked, auto, focus } = _layout();
    _grid.enableMove(!auto && !focus);
    _grid.enableResize(!locked && !auto && !focus);
}

// The toolbar changed the layout (shared/store/wall.ts `wallLayout`): apply it now, never
// waiting for the next 4s poll. Turning Auto or Focus ON computes and applies its layout at
// once (Focus picks its large pane first: the click-focused one, else the first that wants a
// human, else the first tiled). Turning one OFF restores pc_wall_layout verbatim via
// _restoreManualLayout: neither mode ever wrote that key, so it is a pure restore. A new
// preset refits (applyGridLayout). Lock only changes what a drag may do.
let _layoutSeen = wallLayout.get();
function _onLayoutChange() {
    const prev = _layoutSeen, next = wallLayout.get();
    _layoutSeen = next;
    if (!TERMINALS_ON) return;
    if (prev.focus && !next.focus) { _focusWid = null; _focusOrderSig = ''; _clearFocusClasses(); }
    if (prev.auto !== next.auto) _autoOrderSig = '';   // stale from a previous auto session
    if (prev.focus !== next.focus) _focusOrderSig = '';
    _applyWallInteractivity();
    // A preset is a NEW object on every choice, the same one choosing again included: a click on
    // the active preset re-tidies a wall the user has dragged about.
    const presetChosen = prev.preset !== next.preset;
    if (!prev.focus && next.focus) {
        _pickFocusWid();
        _applyWallFocus(_agentsCache);
    } else if (!prev.auto && next.auto) {
        _applyAutoArrange(_agentsCache);
    } else if ((prev.auto || prev.focus) && !next.auto && !next.focus) {
        _restoreManualLayout();
    } else if (presetChosen) {
        applyGridLayout();
    }
}
wallLayout.subscribe(_onLayoutChange);

// Capture each live tile's pre-drag geometry, keyed by wid.
function _snapshotForSwap() {
    if (!_grid) return;
    _swapSnapshot = {};
    _grid.engine.nodes.forEach(n => {
        const wid = n.el && n.el.getAttribute('gs-id');
        if (wid) _swapSnapshot[wid] = { x: n.x, y: n.y, w: n.w, h: n.h };
    });
}

function _gridItemEl(wid) {
    return document.querySelector(`#term-stage .grid-stack-item[gs-id="${_cssEsc(wid)}"]`);
}

// The pane under the dragged tile's centre (in the pre-drag snapshot), or null.
function _swapTargetWid(el) {
    if (!_swapSnapshot || !el) return null;
    const draggedWid = el.getAttribute('gs-id');
    const from = _swapSnapshot[draggedWid];
    const node = el.gridstackNode;
    if (!from || !node) return null;
    const cx = node.x + from.w / 2, cy = node.y + from.h / 2;
    for (const wid in _swapSnapshot) {
        if (wid === draggedWid) continue;
        const t = _swapSnapshot[wid];
        if (cx >= t.x && cx < t.x + t.w && cy >= t.y && cy < t.y + t.h) return wid;
    }
    return null;
}

// Outline the pane a locked drag is hovering, so the swap target is obvious.
function _setSwapHover(wid) {
    if (_swapHoverWid && _swapHoverWid !== wid) {
        const prev = _gridItemEl(_swapHoverWid);
        if (prev) prev.classList.remove('swap-target');
    }
    _swapHoverWid = wid || null;
    if (_swapHoverWid) {
        const el = _gridItemEl(_swapHoverWid);
        if (el) el.classList.add('swap-target');
    }
}

function _swapDragHover(el) { if (_layout().locked) _setSwapHover(_swapTargetWid(el)); }

// On drop: swap the dragged tile with the pane under its centre, exchanging full
// geometry; restore only the tiles the drag actually nudged so the rest of the
// wall (and its live iframes) never repaints. No target (empty drop) → revert.
function _doSwap(el) {
    _setSwapHover(null);
    const snap = _swapSnapshot;
    const targetWid = _swapTargetWid(el);
    _swapSnapshot = null;
    if (!_grid || !snap || !el) return;
    const draggedWid = el.getAttribute('gs-id');
    const from = snap[draggedWid];
    if (!from || !el.gridstackNode) return;

    const elByWid = {};
    _grid.engine.nodes.forEach(n => { const w = n.el && n.el.getAttribute('gs-id'); if (w) elByWid[w] = n.el; });

    _grid.batchUpdate();
    try {
        // Undo only the reflow the drag caused: restore tiles whose live geometry
        // drifted from the snapshot (skipping the two we're about to swap).
        _grid.engine.nodes.forEach(n => {
            const wid = n.el && n.el.getAttribute('gs-id');
            if (!wid || wid === draggedWid || wid === targetWid) return;
            const s = snap[wid];
            if (s && (n.x !== s.x || n.y !== s.y || n.w !== s.w || n.h !== s.h)) _grid.update(n.el, s);
        });
        if (targetWid) {                       // exchange the two tiles' full geometry
            const to = snap[targetWid];
            _grid.update(el, { x: to.x, y: to.y, w: to.w, h: to.h });
            if (elByWid[targetWid]) _grid.update(elByWid[targetWid], { x: from.x, y: from.y, w: from.w, h: from.h });
        } else {                               // dropped on empty space → snap the dragged tile back
            _grid.update(el, from);
        }
    } finally {
        _grid.batchUpdate(false);   // commit (this gridstack has no commit())
    }
}

// Lay the current (non-pinned) wall tiles out to fill the viewport height with
// NO bottom gap, ordered by `keyFn(wid)` (ascending; ties broken by current
// x/y so an un-reordered wall keeps its existing positions). This is the
// shared fill engine behind BOTH applyGridLayout's taskbar-order presets and
// _applyAutoArrange's rank-order auto layout — one layout engine, two
// orderings, per docs/wall-redesign.md ("REUSE the existing preset/fill
// layout code"). Returns false (no-op) if there's no live grid or no
// unpinned tiles; callers decide what (if anything) to persist.
//
//  - Column presets (rows === 1): distribute every pane across `cols` columns
//    (balanced — earlier columns take the remainder), and split each column's
//    height evenly among its panes. The last pane in each column absorbs the
//    rounding remainder so every column reaches the bottom. All panes visible.
//  - Grid presets (rows > 1): a fixed cols×rows grid; each tile is 1/rows of
//    the viewport height, flowed row-major. Panes beyond cols×rows wrap below
//    and scroll. (Fewer panes than cells leaves the trailing cells empty — the
//    explicit-grid trade-off.)
function _fillNodesByOrder(keyFn, cols, rows) {
    if (!_grid) return false;
    const { rows: total, cellPx } = _wallFill();
    _grid.cellHeight(cellPx);                  // exact divisor so `total` rows fill the height
    // Pinned panes are excluded entirely — a fill reflows every OTHER pane
    // around them, leaving a pinned tile's x/y/w/h exactly where the user put
    // it (per-pane layout pin, CMX-108).
    const nodes = _grid.engine.nodes.slice()
        .filter(n => !_isPinned(n.id))
        .sort((a, b) => (keyFn(a.id) - keyFn(b.id)) || (a.x - b.x) || (a.y - b.y));
    const N = nodes.length;
    if (!N) return false;

    _grid.batchUpdate();
    try {
        if (rows === 1) {
            const c = Math.min(cols, N);            // don't open empty columns
            const w = Math.max(1, Math.floor(12 / c));
            const base = Math.floor(N / c), extra = N % c;
            let idx = 0;
            for (let col = 0; col < c; col++) {
                const count = base + (col < extra ? 1 : 0);
                const hEach = Math.max(3, Math.floor(total / count));
                let y = 0;
                for (let r = 0; r < count; r++) {
                    const h = (r === count - 1) ? Math.max(3, total - y) : hEach;  // last eats remainder
                    _grid.update(nodes[idx++].el, { x: col * w, y, w, h });
                    y += h;
                }
            }
        } else {
            const w = Math.max(1, Math.floor(12 / cols));
            // Row heights sum to `total` (last in-view row eats the remainder) so
            // the grid fills the height even when rows don't divide evenly.
            const hBase = Math.max(3, Math.floor(total / rows));
            const lastH = Math.max(3, total - (rows - 1) * hBase);
            const rowY = r => (r < rows) ? r * hBase : (rows - 1) * hBase + lastH + (r - rows) * hBase;
            const rowH = r => (r === rows - 1) ? lastH : hBase;
            // Row-major in `keyFn` order so cells fill left-to-right, top-down.
            nodes.sort((a, b) => (keyFn(a.id) - keyFn(b.id)) || (a.y - b.y) || (a.x - b.x));
            nodes.forEach((node, i) => {
                const r = Math.floor(i / cols);
                _grid.update(node.el, { x: (i % cols) * w, y: rowY(r), w, h: rowH(r) });
            });
        }
    } finally {
        _grid.batchUpdate(false);
    }
    return true;
}

// Apply the active preset (shared/store/wall.ts `wallLayout`, set by the toolbar) to the
// current wall panes, in pc_pane_order (shared/store/wall.ts `paneOrderKey`). Persists the result to
// pc_wall_layout, the SAME manual-layout key a drag/resize writes to, so a preset is itself a
// manual arrangement. Also the re-fit after a viewport resize or a dock change: the fill is
// viewport-relative. While auto-arrange is on, positions come from rankOrder() instead and are
// NEVER persisted (manual layout is sacred; auto must never be able to clobber it).
function applyGridLayout() {
    if (!_grid) return;
    const { preset, auto, focus } = _layout();
    // Focus owns the layout while it's on (fixed 8/4 split, not a column
    // count) — a preset or a viewport resize just refits the SAME focus
    // layout to the new viewport, it never falls through to the fill below.
    if (focus) {
        _focusOrderSig = '';
        _applyWallFocus(_agentsCache);
        return;
    }
    if (auto) {
        _autoOrderSig = '';   // force a re-apply at the new column/row count
        _applyAutoArrange(_agentsCache);
        return;
    }
    if (!_fillNodesByOrder(paneOrderKey(), preset.cols, preset.rows)) return;
    const out = {};
    _grid.save(false).forEach(nd => {
        if (nd.id != null) out[nd.id] = { x: nd.x, y: nd.y, w: nd.w, h: nd.h };
    });
    localStorage.setItem('pc_wall_layout', JSON.stringify(out));
}

// Wall redesign slice 2: rank the panes currently ON THE WALL by attention
// (rankOrder, wallmodel.ts) and apply that order via the shared fill engine —
// no-op unless auto-arrange is on. Compares the new order against the last
// one actually applied (_autoOrderSig) so a 4s poll with no attention-state
// change never touches GridStack (no thrash, no wasted reflow). Deliberately
// NEVER writes pc_wall_layout: that key is the sacred manual arrangement,
// restored verbatim by _restoreManualLayout on toggle-off.
function _applyAutoArrange(agents) {
    if (!_layout().auto || _termMode !== 'wall' || !_grid) return;
    const tiled = new Set(_grid.engine.nodes.map(n => n.id));
    const relevant = (agents || []).filter(a => a && a.window_id && tiled.has(a.window_id));
    const wantsByWid = {};
    relevant.forEach(a => { wantsByWid[a.window_id] = wantsHuman(a); });
    const order = rankOrder(relevant, wantsByWid);
    const sig = order.join(',');
    if (sig === _autoOrderSig) return;   // ranked order unchanged since the last apply — skip
    _autoOrderSig = sig;
    const idx = {};
    order.forEach((wid, i) => { idx[wid] = i; });
    // A tile GridStack knows about but that fell out of the fresh agents poll
    // (a transient gap, not yet dropped) sorts last rather than throwing.
    const { preset } = _layout();
    _fillNodesByOrder(wid => (wid in idx ? idx[wid] : Number.MAX_SAFE_INTEGER), preset.cols, preset.rows);
}

// Wall redesign slice 2: restore the user's manual arrangement (pc_wall_layout)
// verbatim when auto-arrange is toggled off. Only touches tiles that actually
// have a saved position — a tile added while auto was on (never manually
// placed) simply stays wherever auto last put it, same as any other
// never-dragged tile today.
function _restoreManualLayout() {
    if (!_grid) return;
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem('pc_wall_layout') || '{}'); } catch (e) { /* noop */ }
    const valid = s => s && Number.isInteger(s.x) && Number.isInteger(s.y) && s.w > 0 && s.h > 0;
    const nodes = _grid.engine.nodes.filter(n => !_isPinned(n.id) && valid(saved[n.id]));
    if (!nodes.length) return;
    _grid.batchUpdate();
    try {
        // TWO-PASS park-then-place. A restore often SWAPS columns (auto-arrange
        // put a working pane where an idle one is saved, and vice-versa); moving
        // one straight into a spot the other still occupies makes GridStack shove
        // the occupant away, and the swap never settles (both tiles cascade into
        // one column). So first park every node in its own empty row far below
        // the grid, clearing the real area, THEN drop each onto its saved cell —
        // now nothing collides. Both passes are inside one batch, so the parked
        // positions are transient and never paint.
        nodes.forEach((n, i) => _grid.update(n.el, { x: 0, y: 10000 + i * 2, w: 1, h: 1 }));
        nodes.forEach(n => {
            const s = saved[n.id];
            _grid.update(n.el, { x: s.x, y: s.y, w: s.w, h: s.h });
        });
    } finally {
        _grid.batchUpdate(false);
    }
}

// ---- Focus layout (one pane large, the rest a strip) -----------------------
//
// Choose which pane becomes the large focus pane the moment Focus turns on
// (or its current target falls off the wall — killed, minimized out, pinned):
// the user's own click-focused pane (._focusedPane — the SAME signal that
// paints the .term-focused ring, docs/wall-redesign.md's focus tracking)
// first, else the first pane that wantsHuman (the attention signal the whole
// redesign is built on), else the first tiled pane. Pinned panes are never
// eligible — they sit out of every fill, same as auto-arrange/presets.
function _pickFocusWid() {
    _focusWid = null;
    if (!_grid) return;
    const tiled = _grid.engine.nodes
        .map(n => n.id)
        .filter(id => id != null && !_isPinned(id));
    if (!tiled.length) return;
    const tiledSet = new Set(tiled);
    if (_focusedPane) {
        const item = _focusedPane.closest('.grid-stack-item');
        const wid = item && item.getAttribute('gs-id');
        if (wid && tiledSet.has(wid)) { _focusWid = wid; return; }
    }
    const wanter = (_agentsCache || []).find(a => a && a.window_id && tiledSet.has(a.window_id) && wantsHuman(a));
    if (wanter) { _focusWid = wanter.window_id; return; }
    _focusWid = tiled[0];
}

// Strip the promote/large-pane CSS hooks from every tile — called on
// toggle-off (and implicitly superseded by a fresh _applyWallFocus pass on
// toggle-on) so a leftover class never survives into manual/auto mode.
function _clearFocusClasses() {
    if (!_grid) return;
    _grid.engine.nodes.forEach(n => { if (n.el) n.el.classList.remove('wall-focus-main', 'wall-focus-strip'); });
    const gsEl = $('#term-stage') && $('#term-stage').querySelector('.grid-stack');
    if (gsEl) gsEl.classList.remove('wall-focus-on');
}

// Apply the Focus layout — no-op unless Focus is on. Mirrors _applyAutoArrange's
// shape (agents in, sig-guarded, never writes pc_wall_layout) but writes
// GridStack directly instead of going through _fillNodesByOrder: that shared
// engine only expresses "flow N panes in an order across a fixed cols×rows
// grid," not focusLayout's fixed 8-vs-4-column split with one pane pinned
// large — so this computes the {wid:{x,y,w,h}} map itself (wallmodel.ts's
// focusLayout, PURE) and applies it in one batch, same GridStack idiom every
// other fill here uses.
function _applyWallFocus(agents) {
    if (!_layout().focus || _termMode !== 'wall' || !_grid) return;
    const tiled = _grid.engine.nodes
        .map(n => n.id)
        .filter(id => id != null && !_isPinned(id));
    if (!tiled.length) return;
    const tiledSet = new Set(tiled);
    // The current target fell off the wall (killed/minimized/pinned since it
    // was chosen) — pick a fresh one before laying out.
    if (!_focusWid || !tiledSet.has(_focusWid)) _pickFocusWid();
    if (!_focusWid) return;
    const relevant = (agents || []).filter(a => a && a.window_id && tiledSet.has(a.window_id));
    const wantsByWid = {};
    relevant.forEach(a => { wantsByWid[a.window_id] = wantsHuman(a); });
    // Strip order = needs-you-first (rankOrder, same ranking auto-arrange
    // uses), minus the focus pane itself — it's already large, not in the strip.
    const order = rankOrder(relevant, wantsByWid).filter(wid => wid !== _focusWid);
    // A tile GridStack knows about but missing from the fresh agents poll (a
    // transient gap, not yet dropped) still needs a strip slot — append it at
    // the end, the same fallback _applyAutoArrange gives an unranked tile.
    tiled.forEach(wid => { if (wid !== _focusWid && !order.includes(wid)) order.push(wid); });
    const sig = _focusWid + '|' + order.join(',');
    if (sig === _focusOrderSig) return;   // target + strip order unchanged since the last apply — skip
    _focusOrderSig = sig;
    const { rows: total, cellPx } = _wallFill();
    _grid.cellHeight(cellPx);   // exact divisor so `total` rows fill the height, same as _fillNodesByOrder
    const layout = focusLayout([_focusWid, ...order], _focusWid, total);
    _grid.batchUpdate();
    try {
        // Park-then-place (same as _restoreManualLayout): a focus layout SPREADS
        // panes into very different cells — one 8-wide main + a 4-wide strip — so
        // moving each straight into its cell cascades collisions and the wide
        // main never lands (it gets shoved into a lower row). Park every laid-out
        // node far below first, then drop each onto its focus cell: nothing
        // collides. Classes are toggled separately, over every node.
        const laidOut = _grid.engine.nodes.filter(n => layout[n.id]);
        laidOut.forEach((n, i) => _grid.update(n.el, { x: 0, y: 10000 + i * 2, w: 1, h: 1 }));
        laidOut.forEach(n => _grid.update(n.el, layout[n.id]));
        _grid.engine.nodes.forEach(n => {
            if (!n.el) return;
            n.el.classList.toggle('wall-focus-main', n.id === _focusWid);
            n.el.classList.toggle('wall-focus-strip', !!layout[n.id] && n.id !== _focusWid);
        });
    } finally {
        _grid.batchUpdate(false);
    }
    const gsEl = $('#term-stage') && $('#term-stage').querySelector('.grid-stack');
    if (gsEl) gsEl.classList.add('wall-focus-on');
}

// ---- Mobile session switching ----------------------------------------------
//
// On phones the wall is forced to single mode (one full-width pane). The sidebar
// (☰) is the session switcher (CMX-202 dropped the pill strip that sat above the
// pane); the pane header names the open session. The live ttyd terminal is a
// same-origin iframe that swallows its own touches, so direct swipe-on-terminal
// can't be caught; we instead allow a swipe on the pane HEADER (parent DOM) to
// step prev/next. The dropdown (#term-agent) stays in the DOM as the value
// holder; a switch writes sel.value and calls renderTerminals(), so it reuses the
// exact single-mode switch path with no new state.

function _isMobileTerm() {
    return window.matchMedia('(max-width: 768px)').matches;
}

// CMX-165: the header swipe (and the boot's default pane) go in the SIDEBAR's order
// (nav.js sidebarOrder: group order, custom moves, each group's sort), not #term-agent's
// option order. A session the sidebar has not drawn yet goes at the end; none is dropped.
function _mobileWids() {
    const sel = $('#term-agent');
    return orderByList(sel ? Array.from(sel.options).map(o => o.value) : [], sidebarOrder());
}

// Share the CURRENT terminal session (the active agent, #term-agent's value — the
// same handle termKey uses). Invoked from the topbar overflow menu; reuses the
// full share flow (shareSession), so on mobile it opens the bottom-sheet and the
// global "N sharing" indicator lights. No current session (e.g. a non-terminals
// view) → fall back to the command palette's per-session Share list.
function shareCurrentAgent() {
    const sel = document.getElementById('term-agent');
    const wid = sel && sel.value;
    if (wid) { shareSession(wid); return; }
    if (typeof openPalette === 'function') openPalette();
}

// Switch the single-mode pane to `wid` via the dropdown's existing path.
function switchAgentMobile(wid) {
    const sel = $('#term-agent');
    if (!sel || sel.value === wid) return;
    sel.value = wid;
    renderTerminals();
}

// Step to the previous/next agent (dir -1 / +1), wrapping. Drives header swipe.
function stepAgentMobile(dir) {
    const sel = $('#term-agent');
    if (!sel) return;
    const wids = _mobileWids();
    if (wids.length < 2) return;
    const i = Math.max(0, wids.indexOf(sel.value));
    switchAgentMobile(wids[(i + dir + wids.length) % wids.length]);
}

// Attach a horizontal-swipe listener to the single-mode pane header (delegated
// off #term-stage so it survives pane rebuilds). Swiping the header left → next
// agent, right → previous. Ignored on the iframe itself (it owns its touches).
let _termSwipeBound = false;
function _bindHeaderSwipe() {
    if (_termSwipeBound) return;
    const stage = document.getElementById('term-stage');
    if (!stage) return;
    let x0 = null, y0 = null;
    stage.addEventListener('touchstart', e => {
        const head = e.target.closest && e.target.closest('.gs-head, .gs-label, .pane-title');
        if (!head || !_isMobileTerm() || _termMode !== 'single') { x0 = null; return; }
        x0 = e.touches[0].clientX; y0 = e.touches[0].clientY;
    }, { passive: true });
    stage.addEventListener('touchend', e => {
        if (x0 == null) return;
        const t = e.changedTouches[0];
        const dx = t.clientX - x0, dy = t.clientY - y0;
        x0 = null;
        if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.5) stepAgentMobile(dx < 0 ? 1 : -1);
    }, { passive: true });
    _termSwipeBound = true;
}

// Promote a STRIP pane to the large focus slot by clicking its HEADER — same
// delegated-off-#term-stage idiom as _bindHeaderSwipe (survives buildWall's
// innerHTML rebuilds, bound once). Only the header promotes: the iframe stays
// fully interactive (click-to-focus-and-type is untouched), and only while
// Focus mode is actually on. Buttons/links inside the header (kill/min/max/
// menu) keep doing their own thing rather than ALSO promoting: this skips any
// button/link/popover explicitly (the ⋯ menu itself is portaled to <body>).
let _focusPromoteBound = false;
function _bindFocusPromote() {
    if (_focusPromoteBound) return;
    const stage = document.getElementById('term-stage');
    if (!stage) return;
    stage.addEventListener('click', e => {
        if (!_layout().focus || _termMode !== 'wall') return;
        if (e.target.closest('button, a, .popover')) return;   // let their own handlers act alone
        const head = e.target.closest('.gs-head');
        if (!head) return;
        const item = head.closest('.grid-stack-item');
        const wid = item && item.getAttribute('gs-id');
        if (!wid || wid === _focusWid || _isPinned(wid)) return;
        _focusWid = wid;
        _focusOrderSig = '';   // target changed — force a re-apply
        _applyWallFocus(_agentsCache);
    });
    _focusPromoteBound = true;
}

// --- SSE: the window set and terminal readiness (CMX-217: moved here from sse.js) ----------
// The window set changed (util.js has already invalidated the agent cache). Reconcile by
// stable window id, not the payload names: refetch the live agent set, drop tiles for
// vanished wids, and absorb new ones surgically (wall appends a tile, single refreshes the
// dropdown; neither reloads a live iframe). Diffing by wid makes a pure rename a no-op. The
// frame fires on spawn/kill with the Wall inactive too; the guard skips it then.
onEvent('windows', () => {
    if (!TERMINALS_ON || currentTab !== 'terminals') return;
    api('/api/agents').then(agents => {
        setAgentsCache(agents);
        const live = (agents || []).filter(a => a.online !== false && a.window_id)
            .map(a => a.window_id);
        const liveSet = new Set(live);
        _renderedWids.filter(w => !liveSet.has(w)).forEach(dropTerminalPane);
        _absorbFreshTerminals(live);
        _refreshPaneLabels();   // a rename may have changed only the label
    }).catch(() => { /* transient: the 4s termTick will catch up */ });
});

// A terminal just got its ttyd port: swap its placeholder for the iframe now. The ~1.5s
// ready poll stays the reliable default, so this is purely an accelerator. d.ready carries
// window ids (@N), the same value as each placeholder's data-pending.
onEvent('term-ready', d => {
    if (!TERMINALS_ON || currentTab !== 'terminals') return;
    const stage = $('#term-stage');
    (d && d.ready || []).forEach(wid => {
        if (stage.querySelector('.term-pending[data-pending="' + _cssEsc(wid) + '"]')) {
            _stopReadyPoll(wid);
            _termReady.add(wid);
            _swapToFrame(wid);
        }
    });
});

// --- Stage 0: ES-module exports ---
export { adoptSidebarDefault, stepAgentMobile, _absorbFreshTerminals, _applyTermFocus, _cssEsc, _displayLabel, _jsStr, _refreshPaneLabels, _renderedWids, _stopReadyPoll, _swapToFrame, _termReady, dropTerminalPane, focusPaneByWid, isWallVisible, minimizePane, renderTerminals, setTermMode, termTick, startTermTimer, stopTermTimer };

// --- Stage 0: window.chela — surface reachable from inline HTML handlers ---
window.chela = window.chela || {};
Object.assign(window.chela, { renderTerminals, retryReady, setTermMode, shareCurrentAgent, termKey, termPaste });
