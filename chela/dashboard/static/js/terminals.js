// --- Stage 0: ES-module imports ---
import { $, BASE_PATH, TERMINALS_ON, WALL_TILE_DISPATCHED, _agentsCache, api, attrEsc, currentTab, escHtml, lucideIcon, setAgentsCache, updateTabSignal, wantsHuman } from './util.js';
import { openPalette, renderSidebarAgents, selectView, updateCtxCache } from './nav.js';
import { applyRoomAccents, bezierPath, resolveDrop } from './wire.js';
import { onOrchestratorChange, orchestratorRelease, orchestratorState, orchestratorSubscribe } from './orchestrator.js';

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
let _termScroll = false;
let _grid = null;                 // Gridstack instance (wall mode only)
let _renderedWids = [];           // pane wids currently in the DOM (live render order)
let _termTimer = null;            // fast reactive poll while the terminals tab is open
let _wallPreset = _loadWallPreset();   // active {cols, rows} fill preset; default 2 columns
let _wallLocked = localStorage.getItem('pc_wall_locked') === '1';   // lock = swap-on-drag, no resize
let _paneActivity = {};           // wid -> epoch ms a pane last STARTED being busy; drives the taskbar MRU sort
let _paneStatus = {};             // wid -> last seen session_status, for rising-edge (idle→busy) detection
let _dockOrderSig = '';           // last rendered chip order, so a status poll only rebuilds on a real reorder

// Active wall preset persists across reloads (and drives the resize re-fit).
// Default = 2 columns, the standard terminal layout.
function _loadWallPreset() {
    try {
        const p = JSON.parse(localStorage.getItem('pc_wall_preset') || 'null');
        if (p && Number.isInteger(p.cols) && Number.isInteger(p.rows)) return p;
    } catch (e) { /* noop */ }
    return { cols: 2, rows: 1 };
}
let _wallResizeTimer = null;      // debounce for the resize re-fit
const TERM_REFRESH_MS = 4000;     // diff the live agent set every few seconds

// The fill is viewport-relative, so a window/screen resize changes how many
// rows fit. Re-apply the active preset (debounced) so the wall keeps filling
// the height on any screen size. No-op unless a preset is active in wall mode.
window.addEventListener('resize', () => {
    if (!TERMINALS_ON || _termMode !== 'wall' || !_wallPreset || !_grid) return;
    clearTimeout(_wallResizeTimer);
    _wallResizeTimer = setTimeout(() => applyGridLayout(_wallPreset.cols, _wallPreset.rows), 200);
});

// ---- wid <-> agent helpers -------------------------------------------------
// Live panes are addressed by tmux window id; their display name/cwd/status
// come from the /api/agents cache, looked up by window_id.
function _agentByWid(wid) {
    return (_agentsCache || []).find(a => a.window_id === wid);
}
function _nameOfWid(wid) {
    const a = _agentByWid(wid);
    return a ? a.name : wid;
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
    ifr.src = _termSrc(wid);
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
    ph.innerHTML = `<div class="term-pending-inner">still starting —
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

// Spawn a fresh plain-shell tmux window, then re-render the wall so its pane
// appears. The /term/<wid>/ iframe 404s until the ttyd supervisor's next poll
// (~12s); renderTerminals renders a pending placeholder + readiness poll for
// the new pane and swaps in the iframe once the port lands (no manual refresh).
async function spawnShell(btn) {
    const orig = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = 'Spawning…'; }
    try {
        const res = await api('/api/agents/spawn', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: '{}',
        });
        if (!res || !res.ok) {
            alert('Spawn failed: ' + ((res && res.error) || 'unknown error'));
            return;
        }
        setAgentsCache([]);   // force /api/agents refetch so the new window shows
        _termSig = '';       // invalidate render cache so the stage rebuilds
        await renderTerminals();
    } catch (e) {
        console.error('spawnShell', e);
        alert('Spawn failed: ' + e);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = orig; }
    }
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
// paste path; this reads the clipboard on tap (the gesture unlocks readText() on
// iOS) and ships it to /api/term/paste, which delivers a bracketed paste at the
// tmux layer. No-op where the Clipboard API is unavailable or permission denied.
async function termPaste(btn) {
    const wid = $('#term-agent').value;
    if (!wid || !navigator.clipboard || !navigator.clipboard.readText) return;
    let text = '';
    try { text = await navigator.clipboard.readText(); } catch (e) { return; }
    if (!text) return;
    try {
        await api('/api/term/paste', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ agent: wid, text }),
        });
    } catch (e) { console.error('termPaste', e); }
}

function termScrollToggle() {
    const btn = $('#term-scroll-btn');
    if (!_termScroll) {
        termKey('scroll'); _termScroll = true;
        btn.textContent = 'Exit Scroll'; btn.classList.add('btn-accent');
    } else {
        termKey('scroll-exit'); _termScroll = false;
        btn.innerHTML = '&#10514; Scroll'; btn.classList.remove('btn-accent');
    }
}

// Per-tile copy-mode toggle for wall mode (each button tracks its own state).
function termScrollFor(btn, wid) {
    const on = btn.classList.toggle('btn-accent');
    termKeyFor(wid, on ? 'scroll' : 'scroll-exit');
    btn.title = on ? 'Exit scroll (tmux copy-mode)' : 'Scroll (tmux copy-mode)';
}

// Shared pane header: agent label + quick keys. draggable=true for wall
// tiles (label is the Gridstack drag handle); false for the single view.
function _jsStr(s) { return String(s).replace(/\\/g, '\\\\').replace(/'/g, "\\'"); }
// chela has no managed-agent roster — every pane is a peer session, so the ×
// kill control is available on all of them (matches the dashboard kill route,
// which has no managed guard). Kept as a function so the call sites read the
// same and a future deployment could pin specific windows here.
function _isManaged(wid) {
    return false;
}

// ---- Display labels. THE TMUX WINDOW NAME IS THE SOURCE OF TRUTH: a rename goes
// to the server (renamePane -> POST /api/agents/<wid>/rename -> tmux rename-window)
// and comes back to every client through /api/agents. There is deliberately NO
// local label store. The old one (localStorage `pc_pane_titles`) was per-browser:
// a rename on the laptop was invisible on the phone, never reached tmux or the
// bound Telegram topic, and died with the browser's data. Drop any leftovers so
// the two layers can't disagree.
try { localStorage.removeItem('pc_pane_titles'); } catch (e) { /* private mode */ }

// Names tmux auto-assigns and never a human — a numbered shell, or a bare
// command tmux follows (`claude`, `bash`, …). Mirrors the server's
// is_generic_name() so both layers agree on what counts as a fillable blank.
const _GENERIC_NAMES = new Set(['bash', 'zsh', 'sh', 'fish', 'claude', 'node', 'python', 'python3']);
function _isGenericName(name) {
    const n = (name || '').trim();
    if (!n) return true;
    return /^shell(-\d+)?$/i.test(n) || _GENERIC_NAMES.has(n.toLowerCase());
}

// Friendly display label for a window, shared by agent cards + terminal panes so
// the two stay aligned. This is a FORMATTER over the real name, not a second name.
// A name a human chose is intent and is shown verbatim (same rule the server's
// is_generic_name() applies). A generic name (`shell-2`, or a bare `claude`) is a
// blank we fill with the most meaningful thing we know, in order: the Claude
// session name, then the repo (cwd basename — so cards read as the project, not
// shell-1/claude, and generic siblings in one repo get a trailing index), then
// the raw name; a literal placeholder only if even that is empty.
//
// NB: this only changes what a label RESOLVES to. It must never leak into the
// wall's `_termSig`, which is keyed on window ids alone — else buildWall reloads
// every live iframe on a relabel (CMX-67/71).
function _displayLabel(wid) {
    const cache = _agentsCache || [];
    const a = cache.find(x => x.window_id === wid);
    const name = a ? a.name : wid;
    if (!_isGenericName(name)) return name;
    if (a && a.session_name) return a.session_name;
    const baseOf = x => (x && x.cwd) ? (x.cwd.replace(/\/+$/, '').split('/').pop() || '') : '';
    const base = baseOf(a);
    if (base) {
        // Only siblings that also fall through to their repo (no chosen name, no
        // session name) share the numbering, so the indices stay contiguous.
        const siblings = cache.filter(x => _isGenericName(x.name) && !x.session_name && baseOf(x) === base);
        if (siblings.length > 1) {
            siblings.sort((x, y) => x.name.localeCompare(y.name));
            return `${base} ${siblings.findIndex(x => x.window_id === wid) + 1}`;
        }
        return base;
    }
    return name || 'claude window';
}

function _paneTitle(wid) {
    return _displayLabel(wid);
}

// Rename the WINDOW (not a local label): POST the new name, which renames the tmux
// window, so it shows up on every client, in tmux, and on the bound Telegram topic.
// Refetches /api/agents so the wall, cards and nav all relabel from the real name.
async function _commitRename(wid, name) {
    const res = await api(`/api/agents/${encodeURIComponent(wid)}/rename`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
    });
    if (!res || !res.ok) throw new Error((res && res.error) || 'rename failed');
    const agents = await api('/api/agents');
    setAgentsCache(agents);
    _refreshPaneLabels();
}

// Swap a pane title span for an inline input. Enter saves, Esc cancels. The name is
// the window's real name, so an unchanged (or empty) value is simply not submitted.
function renamePane(ev, span, wid) {
    ev.stopPropagation();
    const input = document.createElement('input');
    input.className = 'pane-title-edit';
    input.value = _nameOfWid(wid);   // edit the REAL name, not the prettified label
    input.title = 'Enter to save · Esc to cancel';
    span.replaceWith(input);
    input.focus();
    input.select();
    let done = false;
    const restore = () => {
        const fresh = document.createElement('span');
        fresh.className = 'pane-title';
        fresh.title = 'double-click to rename';
        fresh.textContent = _paneTitle(wid);
        fresh.addEventListener('dblclick', e2 => renamePane(e2, fresh, wid));
        input.replaceWith(fresh);
        return fresh;
    };
    const commit = async (save) => {
        if (done) return;
        done = true;
        const name = input.value.trim();
        const unchanged = !name || name === _nameOfWid(wid);
        restore();
        if (!save || unchanged) return;
        try {
            await _commitRename(wid, name);
        } catch (e) {
            console.error('renamePane', e);
            alert('Rename failed: ' + e.message);
        }
    };
    input.addEventListener('keydown', e2 => {
        e2.stopPropagation();
        if (e2.key === 'Enter') { e2.preventDefault(); commit(true); }
        else if (e2.key === 'Escape') { e2.preventDefault(); commit(false); }
    });
    input.addEventListener('blur', () => commit(true));
    input.addEventListener('mousedown', e2 => e2.stopPropagation());  // don't start a grid drag
}

// --- collab sharing (presence-only) ----------------------------------------
// The shared "link" is just the clean /term/<wid>/ URL — NO ?collab magic;
// sharing is a server-side per-wid flag (POST /api/term/<wid>/share) so the host
// can actually revoke. One src helper keeps frame()/_wallTileHTML/_swapToFrame
// in agreement.
function _termSrc(wid) { return '/term/' + encodeURIComponent(wid) + '/'; }
function _termLink(wid) { return location.origin + BASE_PATH + _termSrc(wid); }

// Per-wid shared flag (seeded from /api/agents .shared, flipped by toggleShare)
// and the latest header-facepile presence (from the in-iframe chelaPresence hook).
const _sharedWids = new Set();
const _presenceByWid = new Map();

// Owner-presence parent client (ES module: holds the pairing secret + crypto — the
// iframe shim never does; see static/collab/presence-owner.js). Loaded lazily and
// wired once via initOwnerPresence(), which routes the shim<->parent postMessage
// bridge and auto-resumes presence for already-shared panes on the shim's 'ready'.
let _ownerPresenceP = null;
function _ownerPresence() {
    if (!_ownerPresenceP) {
        _ownerPresenceP = import(BASE_PATH + '/static/collab/presence-owner.js')
            .then(m => { try { m.initOwnerPresence(); } catch (_) {} return m; })
            .catch(e => { console.warn('[chela] owner-presence load failed', e); _ownerPresenceP = null; return null; });
    }
    return _ownerPresenceP;
}
// Wire the router immediately so a shared pane's shim 'ready' is caught on load.
if (typeof TERMINALS_ON !== 'undefined' && TERMINALS_ON) _ownerPresence();

// On load, reflect any already-active shares in the global safety indicator —
// covers landing straight on a non-terminals tab with a forgotten live share.
if (typeof TERMINALS_ON !== 'undefined' && TERMINALS_ON) {
    api('/api/term/shared').then(shared => {
        Object.keys(shared || {}).forEach(w => _sharedWids.add(w));
        _renderSharesIndicator();
    }).catch(() => {});
}

function _seedSharedFromAgents(agents) {
    (agents || []).forEach(a => {
        if (!a.window_id) return;
        if (a.shared) _sharedWids.add(a.window_id); else _sharedWids.delete(a.window_id);
    });
    _renderSharesIndicator();
}

// --- global active-shares indicator + kill-switch --------------------------
// A persistent topbar pill (#btn-shares) shown on EVERY tab + mobile whenever any
// session is shared — because a forgotten share is an exposed live terminal behind
// a public link (SAFETY). Tapping opens a sheet listing each active share with a
// prominent Stop + a Stop-All, wired to the existing _stopShare(). The topbar
// button is best-effort (driven by _sharedWids, so once shown it persists across
// tabs); opening the sheet reconciles against the server truth (/api/term/shared)
// so the kill list is always accurate at the moment it matters.
function _renderSharesIndicator() {
    const btn = document.getElementById('btn-shares');
    if (!btn) return;
    const n = _sharedWids.size;
    btn.hidden = n === 0;
    if (n > 0) {
        const txt = btn.querySelector('.si-text');
        if (txt) txt.textContent = n + ' sharing';
        btn.setAttribute('aria-label', n + ' active share' + (n === 1 ? '' : 's') + ' — tap to manage or stop');
    }
    // Keep an open sheet in sync with the live set (e.g. the reaper stopped one).
    if (document.getElementById('shares-sheet-backdrop')) _buildSharesSheet();
}

async function openSharesSheet() {
    // Reconcile against the server truth first, so the kill list is accurate no
    // matter which tab we're on or how stale the best-effort set is.
    try {
        const shared = await api('/api/term/shared');
        _sharedWids.clear();
        Object.keys(shared || {}).forEach(w => _sharedWids.add(w));
        _renderedWids.forEach(_updateShareBtns);
    } catch (_) { /* offline / transient → fall back to the best-effort set */ }
    _buildSharesSheet();
    _renderSharesIndicator();
}

function closeSharesSheet() {
    const bd = document.getElementById('shares-sheet-backdrop');
    if (bd) bd.remove();
    document.removeEventListener('keydown', _sharesSheetKey, true);
}
function _sharesSheetKey(e) { if (e.key === 'Escape') closeSharesSheet(); }

function _buildSharesSheet() {
    const existing = document.getElementById('shares-sheet-backdrop');
    if (existing) existing.remove();
    const wids = [..._sharedWids];
    const backdrop = document.createElement('div');
    backdrop.id = 'shares-sheet-backdrop';
    backdrop.className = 'shares-backdrop';
    backdrop.onclick = (e) => { if (e.target === backdrop) closeSharesSheet(); };
    const sheet = document.createElement('div');
    sheet.className = 'shares-sheet';
    const rows = wids.map(wid => `
        <div class="ss-row" data-wid="${attrEsc(wid)}">
          <span class="ss-label">${escHtml(_paneTitle(wid))} <span class="ss-wid">${escHtml(wid)}</span></span>
          <button class="ss-stop" type="button" data-wid="${attrEsc(wid)}">Stop</button>
        </div>`).join('');
    sheet.innerHTML =
        `<div class="ss-hd"><span class="ss-hd-ic">${lucideIcon('share-2', 15)}</span> Active shares
           <button class="ss-close" type="button" aria-label="Close">&times;</button></div>
         <div class="ss-sub">Anyone with the link + code can watch and type. Stop a share to revoke it — the link dies and the code rotates.</div>
         <div class="ss-list">${rows || '<div class="ss-empty">No active shares.</div>'}</div>
         ${wids.length ? `<button class="ss-stopall" type="button">Stop all sharing (${wids.length})</button>` : ''}`;
    backdrop.appendChild(sheet);
    document.body.appendChild(backdrop);
    sheet.querySelector('.ss-close').onclick = closeSharesSheet;
    sheet.querySelectorAll('.ss-stop').forEach(b => b.onclick = async () => {
        b.disabled = true; b.textContent = 'Stopping…';
        await _stopShare(b.dataset.wid);
        if (_sharedWids.size) _buildSharesSheet(); else closeSharesSheet();
    });
    const all = sheet.querySelector('.ss-stopall');
    if (all) all.onclick = async () => {
        all.disabled = true; all.textContent = 'Stopping…';
        await _stopAllShares();
        closeSharesSheet();
    };
    document.addEventListener('keydown', _sharesSheetKey, true);
}

async function _stopAllShares() {
    // Snapshot first: _stopShare mutates _sharedWids as each resolves.
    await Promise.all([..._sharedWids].map(w => _stopShare(w)));
}

function _reloadPaneFrame(wid) {
    document.querySelectorAll('#term-stage iframe.term-frame').forEach(ifr => {
        if (_widOfFrame(ifr) !== wid) return;
        // Re-serve the ttyd page so term_http injects the new "shared" flag.
        try { ifr.contentWindow.location.reload(); }
        catch (_) { ifr.src = ifr.getAttribute('src'); }  // cross-frame guard
    });
}

// This pane's live terminal dims (cols x rows) — the presenter grid captured at
// share time so joiners pin to the host's pane, not a fixed 120x30.
function _paneTermDims(wid) {
    let dims = null;
    document.querySelectorAll('#term-stage iframe.term-frame').forEach(ifr => {
        if (_widOfFrame(ifr) !== wid) return;
        try {
            const t = ifr.contentWindow.term;
            if (t && t.cols) dims = { cols: t.cols, rows: t.rows };
        } catch (_) { /* cross-frame / not ready → server falls back to default */ }
    });
    return dims;
}

// Share button: turn sharing ON (mint the E2E bridge, get join URL + pairing code)
// then show the popover; if already shared, just (re)open the popover — never
// silently stop on a stray click (Stop lives inside the popover).
async function shareBtnClick(btn, wid) {
    // ADOPT-FIRST: the share lives on the SERVER and survives page refreshes, so
    // always ask the server before minting. If a share already exists, reopen its
    // popover (same code) — never re-mint. This makes _sharedWids a mere hint: even
    // if it is stale (e.g. right after a reload, before /api/agents has seeded it),
    // a click can't rotate the code or orphan the running bridge.
    let info = {};
    try { info = (await api('/api/term/' + encodeURIComponent(wid) + '/share-info')) || {}; } catch (_) {}
    if (info && info.pairing_code) {
        _sharedWids.add(wid); _updateShareBtns(wid); _renderSharesIndicator();
        _ownerPresence().then(m => m && m.startOwnerPresence(wid, info.join_url, info.pairing_code));
        _sharePopover(btn, wid, info);
        return;
    }
    const body = { on: true };
    const d = _paneTermDims(wid);
    if (d) { body.cols = d.cols; body.rows = d.rows; }
    let resp;
    try {
        resp = await api('/api/term/' + encodeURIComponent(wid) + '/share', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
    } catch (e) { _termShareToast(btn, 'Share failed'); return; }
    if (!resp || !resp.ok) { _termShareToast(btn, (resp && resp.error) || 'Share failed'); return; }
    _sharedWids.add(wid);
    _ownerPresence().then(m => m && m.startOwnerPresence(wid, resp.join_url, resp.pairing_code));
    _reloadPaneFrame(wid);
    _updateShareBtns(wid);
    _renderSharesIndicator();
    _sharePopover(btn, wid, resp);   // resp carries join_url + pairing_code
}

async function _stopShare(wid) {
    try {
        await api('/api/term/' + encodeURIComponent(wid) + '/share', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ on: false }),
        });
    } catch (_) {}
    _sharedWids.delete(wid); _presenceByWid.delete(wid); _renderFacepile(wid);
    _ownerPresence().then(m => m && m.stopOwnerPresence(wid));
    _reloadPaneFrame(wid); _updateShareBtns(wid); _renderSharesIndicator(); _closeSharePopover();
}

function _closeSharePopover() {
    document.querySelectorAll('.term-share-pop').forEach(p => p.remove());
    document.removeEventListener('mousedown', _sharePopOutside, true);
}
function _sharePopOutside(e) {
    const p = document.querySelector('.term-share-pop');
    if (p && !p.contains(e.target) && !e.target.closest('.gs-share-btn')) _closeSharePopover();
}

// Popover anchored under the share button: join URL + pairing code (each with a
// copy button) + a full-access badge + Stop sharing. The pairing code is what the
// joiner pastes to derive the E2E keys — the relay never sees plaintext. A paired
// joiner has full access (type + scroll); there is no separate grant.
function _sharePopover(btn, wid, info) {
    _closeSharePopover();
    const url = info && info.join_url ? info.join_url : '';
    const code = info && info.pairing_code ? info.pairing_code : '';
    const row = (label, val, extra) => `
      <label class="tsp-lbl">${label}${extra || ''}</label>
      <div class="tsp-row"><input class="tsp-in" readonly value="${attrEsc(val)}">
        <button class="tsp-copy" data-copy="${attrEsc(val)}">Copy</button></div>`;
    const pop = document.createElement('div');
    pop.className = 'term-share-pop';
    pop.innerHTML =
        `<div class="tsp-hd"><span class="tsp-lock">&#128274; end-to-end</span>` +
        `<span class="tsp-ro">read + write</span></div>` +
        (url ? row('Join link', url) : '') +
        (code ? row('Pairing code', code, ' <span class="tsp-hint">— needed to decrypt</span>') : '') +
        (!url && !code
            ? `<div class="tsp-warn">Relay not configured — set <code>CHELA_COLLAB_RELAY</code> to stream.</div>` : '') +
        `<button class="tsp-stop">Stop sharing</button>`;
    document.body.appendChild(pop);
    if (_isMobileTerm()) {
        // Mobile: CSS pins it as a bottom-sheet (safe-area padding, 44px targets);
        // skip the desktop top/right anchor so the inline styles don't fight it.
    } else if (btn) {
        const r = btn.getBoundingClientRect();
        pop.style.top = (r.bottom + 6) + 'px';
        pop.style.right = Math.max(8, window.innerWidth - r.right) + 'px';
    } else {                       // palette-invoked (no anchor button) → top-right
        pop.style.top = '52px';
        pop.style.right = '16px';
    }
    pop.querySelectorAll('.tsp-copy').forEach(b => b.onclick = () => {
        navigator.clipboard.writeText(b.dataset.copy)
            .then(() => { b.textContent = 'Copied'; setTimeout(() => (b.textContent = 'Copy'), 1500); })
            .catch(() => {});
    });
    pop.querySelector('.tsp-stop').onclick = () => _stopShare(wid);
    setTimeout(() => document.addEventListener('mousedown', _sharePopOutside, true), 0);
}

// Throwaway toast bubble anchored to the pane header — same plain-DOM affordance
// as kanban's _kanbanMergeToast.
function _termShareToast(anchor, msg) {
    const host = (anchor && anchor.closest('.gs-head')) || (anchor && anchor.parentElement);
    if (!host) { console.log('[chela-share]', msg); return; }
    host.querySelectorAll('.term-share-toast').forEach(t => t.remove());
    const t = document.createElement('div');
    t.className = 'term-share-toast';
    t.textContent = msg;
    host.appendChild(t);
    setTimeout(() => t.remove(), 4000);
}

// Called by presence.js in the iframe (window.parent.chelaPresence) on every
// awareness change: render a compact facepile into this pane's header + badge.
function chelaPresence(wid, data) {
    _presenceByWid.set(wid, data || { humans: [], agent: null, count: 0 });
    _renderFacepile(wid);
    _updateShareBtns(wid);
}

function _renderFacepile(wid) {
    const slots = document.querySelectorAll('.gs-presence[data-presence-for="' + _cssEsc(wid) + '"]');
    if (!slots.length) return;
    const d = _presenceByWid.get(wid) || { humans: [], agent: null };
    const dots = [];
    (d.humans || []).slice(0, 3).forEach(h => {
        const initial = escHtml((h.name || '?').charAt(0).toUpperCase());
        dots.push('<span class="pf-dot" title="' + attrEsc(h.name + (h.you ? ' (you)' : '')) +
            '" style="background:' + attrEsc(h.color) + '">' + initial + '</span>');
    });
    if ((d.humans || []).length > 3) dots.push('<span class="pf-more">+' + (d.humans.length - 3) + '</span>');
    if (d.agent) {
        // Same status→colour mapping as the pane status dot, so agent presence
        // and pane status never disagree.
        const pip = d.agent.status === 'waiting' ? '#d29922' : d.agent.status === 'busy' ? '#3fb950' : '#8b949e';
        dots.push('<span class="pf-agent" title="' + attrEsc(d.agent.name) + '">&#9881;<i class="pf-pip" style="background:' + pip + '"></i></span>');
    }
    slots.forEach(s => { s.innerHTML = dots.join(''); });
}

function _updateShareBtns(wid) {
    const shared = _sharedWids.has(wid);
    document.querySelectorAll('.gs-share-btn[data-wid="' + _cssEsc(wid) + '"]').forEach(btn => {
        btn.classList.toggle('on', shared);
        btn.setAttribute('aria-pressed', shared ? 'true' : 'false');
        // The peer-count badge is owned by the owner-presence client (setBadge in
        // presence-owner.js), which alone knows the live joiner count; clearing the
        // share flag hides it there on stopOwnerPresence.
    });
}

function _shareBtnHTML(wid) {
    const on = _sharedWids.has(wid);
    return `<button class="gs-share-btn popover-item ov-item${on ? ' on' : ''}" data-wid="${attrEsc(wid)}"
      onclick="chela.shareBtnClick(this,'${_jsStr(wid)}')" aria-pressed="${on ? 'true' : 'false'}"
      title="Share this session"><span class="ov-ic">${lucideIcon('share-2', 14)}</span><span>Share current session</span><span class="gs-share-count" hidden></span></button>`;
}

// The pane-title toggle: "⊙ Orchestrator" — one click registers THIS pane's
// session as the decisions-inbox recipient (an atomic take-over of the single
// slot, chela/inbox.py::register via /api/orchestrator/subscribe), one click
// releases it (falls back to the decisions panel — chela/dashboard/static/js/
// decisions.js). `.on` reflects the LIVE owner, kept current across every pane
// via onOrchestratorChange (fired on our own clicks, other panes' clicks, and
// the SSE `orchestrator` delta — sse.js).
function _orchBtnHTML(wid) {
    const on = orchestratorState().wid === wid;
    const title = on
        ? 'This pane receives the decisions inbox — click to release'
        : 'Click to receive the decisions inbox in this pane';
    return `<button class="gs-orch-btn popover-item ov-item${on ? ' on' : ''}" data-wid="${attrEsc(wid)}"
      onclick="chela.orchestratorBtnClick(this,'${_jsStr(wid)}')" aria-pressed="${on ? 'true' : 'false'}"
      title="${attrEsc(title)}"><span class="ov-ic">${lucideIcon('circle-dot', 14)}</span><span>Orchestrator</span></button>`;
}

function _updateOrchBtns() {
    const owner = orchestratorState().wid;
    document.querySelectorAll('.gs-orch-btn').forEach(btn => {
        const wid = btn.getAttribute('data-wid');
        const on = owner === wid;
        btn.classList.toggle('on', on);
        btn.setAttribute('aria-pressed', on ? 'true' : 'false');
        btn.title = on
            ? 'This pane receives the decisions inbox — click to release'
            : 'Click to receive the decisions inbox in this pane';
    });
    // CMX-119: the pane's status dot (paneHead) carries a non-hue ring while
    // it owns the decisions inbox — driven off this SAME signal as the menu
    // row above, so the ring and the row's `.on` state can never disagree.
    document.querySelectorAll('.gs-dot[data-wid]').forEach(dot => {
        dot.classList.toggle('gs-dot-orch', dot.getAttribute('data-wid') === owner);
    });
}

// Fires on our own clicks, every other pane's clicks (subscribe is a global
// take-over) and the SSE `orchestrator` delta — one state, every button in sync.
onOrchestratorChange(_updateOrchBtns);

async function orchestratorBtnClick(btn, wid) {
    const on = orchestratorState().wid === wid;
    btn.disabled = true;
    try {
        const result = on ? await orchestratorRelease(wid) : await orchestratorSubscribe(wid);
        if (!result || !result.ok) {
            console.error('orchestrator toggle failed', result && result.error);
        }
    } finally {
        btn.disabled = false;
    }
}

function paneHead(wid, draggable) {
    const j = _jsStr(wid);
    const title = `<span class="pane-title" title="double-click to rename" ondblclick="chela.renamePane(event, this, '${j}')">${escHtml(_paneTitle(wid))}</span>`;
    // The NAME is the drag handle (CMX-117 drops the old "☰" glyph) — `.gs-grip`
    // stays the class GridStack's `handle`/`draggable.handle` option targets
    // (buildWall), so dragging still works; only the glyph is gone.
    const label = draggable
        ? `<span class="gs-grip" title="drag to move">${title}</span>`
        : `<span class="gs-label">${title}</span>`;
    // × kill: rendered ONLY for non-managed agents (spawned shells / ad-hoc
    // sessions). Managed personas (anything in agents.yaml) get no × so they
    // can't be torn down from the wall by accident; the backend also refuses.
    const kill = _isManaged(wid) ? '' :
        `<button class="gs-kill-btn" onclick="chela.termKillClick(this,'${j}')" title="Kill this session">&#10005;</button>`;
    // Minimize-to-dock is a wall-only concept (single view shows one pane, with
    // nothing to dock it beside) — only render it for draggable wall tiles.
    const min = draggable
        ? `<button class="gs-min-btn" onclick="chela.termMinFor(this)" title="Minimize to dock">&#128469;</button>` : '';
    // Pin is also wall-only: it exempts this tile from applyGridLayout's
    // auto-reflow (grid presets), so single mode (no reflow to opt out of) gets
    // no button either.
    const pin = draggable ? _pinBtnHTML(wid) : '';
    // No PgUp/PgDn/scroll/Esc/^C here: with focus in the pane those keys reach the
    // terminal natively (wheel/keys scroll, Esc and Ctrl-C pass through), so the
    // header carries only the window controls (minimize / maximize / kill).
    // The wire's port + the room badge are WALL chrome: single-pane mode has a
    // header but no gs-id (nothing to wire to, nothing to resolve a drop against),
    // so it simply gets neither — degrade, don't crash.
    const port = draggable
        ? `<button class="gs-port popover-item ov-item" onmousedown="chela.wireDragStart(event, this, '${j}')"
             title="Wire this agent to another — drag onto a peer"
             aria-label="Wire this agent to another"><span class="ov-ic">${lucideIcon('link-2', 14)}</span><span>Wire to…</span></button>` : '';
    const roomBadge = draggable
        ? `<button class="gs-room" onclick="chela.wireRoomClick(this, '${j}')" hidden></button>` : '';
    // CMX-119: pane header v2 — split the CMX-117 combined badge back apart
    // now that every pane has a bottom bar to hold its numbers. `.gs-dot` is a
    // plain status-by-SHAPE indicator (filled = working, hollow = idle, filled
    // + glow ring = waiting/wants-human — Liav is red-weak, so shape carries
    // the signal, not hue alone) — `_colorTermDots` (this file) targets
    // `.term-status-dot`, which this dot also wears, so the same
    // working/waiting/idle classes paint it same as everywhere else (see the
    // .gs-dot rules in style.css). The orchestrator ring (_updateOrchBtns,
    // below) is a SEPARATE non-hue outline on this same dot. The "⋯" trigger
    // (lucide `more-vertical`) opens the same Wire/Share/Orchestrator/Pin menu
    // the old combined badge used to — same togglePaneOverflow, same `menu =
    // btn.nextElementSibling` contract, so the menu's rows and all their live
    // state wiring (_updateShareBtns/_updateOrchBtns/the pin toggle) are
    // untouched, just anchored at a plain button instead of the pill. The
    // pane № (Alt+N jump target) moved OUT of the header entirely, down to
    // the bottom bar — see _ctxBarHTML.
    const dot = `<span class="gs-dot term-status-dot" data-status-for="${attrEsc(wid)}" data-wid="${attrEsc(wid)}" title="…"></span>`;
    const menu = `<span class="gs-menu-wrap">
        <button class="gs-menu-btn" onclick="chela.togglePaneOverflow(event, this)"
                aria-haspopup="true" aria-expanded="false" title="Session actions">${lucideIcon('more-vertical', 14)}</button>
        <div class="popover pane-overflow-menu overflow-menu" hidden>
          ${port}
          ${_shareBtnHTML(wid)}
          ${_orchBtnHTML(wid)}
          ${pin}
        </div>
      </span>`;
    return `<div class="gs-head">
      ${dot}
      ${menu}
      ${label}
      ${roomBadge}
      <span class="gs-presence" data-presence-for="${attrEsc(wid)}"></span>
      <span class="gs-keys">
        <span class="gs-win-ctl">
          ${min}
          <button class="gs-max-btn" onclick="chela.termMaxFor(this)" aria-pressed="false" title="Maximize pane">&#128470;</button>
          ${kill}
        </span>
      </span>
    </div>`;
}

// Toggle this pane's "⋯" overflow menu (Wire/Share/Orchestrator/Pin). Only one
// open at a time — opening a second closes the first — and light-dismisses on
// the next outside click, same pattern as nav.js's openPrimaryMenu/openNewMenu.
function togglePaneOverflow(ev, btn) {
    if (ev) ev.stopPropagation();
    const menu = btn.nextElementSibling;
    if (!menu) return;
    const opening = menu.hidden;
    _closeAllPaneOverflows();
    if (!opening) return;
    menu.hidden = false;
    btn.setAttribute('aria-expanded', 'true');
    const r = btn.getBoundingClientRect();
    menu.style.top = (r.bottom + 6) + 'px';
    // CMX-119: LEFT-anchored to the trigger (not right-anchored off it) — the
    // trigger sits near the pane's left edge, between the dot and the name, so
    // anchoring the menu's right edge to the trigger's right edge (the old
    // math) could park it far from the pane on a wide wall. Opening rightward
    // from the trigger's own left edge keeps it over the pane it belongs to.
    menu.style.left = Math.max(8, Math.min(r.left, window.innerWidth - menu.offsetWidth - 8)) + 'px';
    setTimeout(() => document.addEventListener('click', _closeAllPaneOverflows, { once: true }), 0);
}

function _closeAllPaneOverflows() {
    document.querySelectorAll('.pane-overflow-menu:not([hidden])').forEach(m => {
        m.hidden = true;
        const btn = m.previousElementSibling;
        if (btn) btn.setAttribute('aria-expanded', 'false');
    });
}

// Inline kill-confirm — same plain-DOM affordance as the kanban delete button
// (kanbanDeleteClick), NOT window.confirm. The confirm bar overlays the top of
// the pane; Kill calls POST /api/agents/kill and, on success, drops the tile
// immediately (see dropTerminalPane) rather than waiting for the next poll.
function termKillClick(btn, wid) {
    const pane = btn.closest('.term-pane, .grid-stack-item-content');
    if (!pane || pane.querySelector('.pane-kill-confirm')) return;
    const confirmEl = document.createElement('div');
    confirmEl.className = 'kanban-confirm pane-kill-confirm';
    confirmEl.dataset.agent = wid;
    confirmEl.innerHTML = `
        <span class="kanban-confirm-msg">Kill ${escHtml(_paneTitle(wid))}?</span>
        <button class="btn-confirm" type="button" onclick="chela.termKillConfirm(this, true)">Kill</button>
        <button type="button" onclick="chela.termKillConfirm(this, false)">Cancel</button>`;
    pane.appendChild(confirmEl);
    btn.style.visibility = 'hidden';
}

async function termKillConfirm(actionBtn, ok) {
    const confirmEl = actionBtn.closest('.pane-kill-confirm');
    if (!confirmEl) return;
    const pane = confirmEl.closest('.term-pane, .grid-stack-item-content');
    const killBtn = pane ? pane.querySelector('.gs-kill-btn') : null;
    const wid = confirmEl.dataset.agent;
    if (!ok) {
        confirmEl.remove();
        if (killBtn) killBtn.style.visibility = '';
        return;
    }
    confirmEl.innerHTML = '<span class="kanban-confirm-msg">Killing…</span>';
    let resp, data = {};
    try {
        resp = await fetch(BASE_PATH + '/api/agents/kill', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ agent: wid }),
        });
        try { data = await resp.json(); } catch (_) { data = {}; }
    } catch (e) {
        _termKillShowError(confirmEl, killBtn, String(e));
        return;
    }
    if (!resp.ok || !data.ok) {
        _termKillShowError(confirmEl, killBtn, data.error || `HTTP ${resp.status}`);
        return;
    }
    // Reactivity: drop the tile now so the wall reflects the kill instantly.
    // The next termTick would catch it too, but waiting leaves a dead iframe.
    dropTerminalPane(wid);
}

function _termKillShowError(confirmEl, killBtn, msg) {
    confirmEl.innerHTML = `
        <span class="kanban-confirm-msg" style="color:var(--red);">${escHtml(msg)}</span>
        <button type="button" onclick="chela.termKillConfirm(this, false)">Close</button>`;
    if (killBtn) killBtn.style.visibility = '';
}

// Maximize/restore toggle for the shared pane header. The pane is lifted to a
// full-viewport overlay (fixed inset:0 over the header AND sidebar — see the CSS
// at .pane-maximized) and a body class hides the other Gridstack tiles. We
// deliberately do NOT touch Gridstack's saved layout, so restore is a true
// inverse — the tile snaps back to its exact prior x/y/w/h with no recompute.
// There is no separate banner or ESC binding (ESC is the agent's interrupt key):
// the way out is the same button, which goes accent-filled while maximized so
// it reads as the obvious exit. Icon flips between 🗖 and 🗗 to match state.
function termMaxFor(btn) {
    if (!btn) return;
    const pane = btn.closest('.term-pane, .grid-stack-item-content');
    if (!pane) return;
    const isMax = pane.classList.toggle('pane-maximized');
    document.body.classList.toggle('pane-is-maximized', isMax);
    btn.innerHTML = isMax ? '&#128471;' : '&#128470;';
    btn.title = isMax ? 'Restore pane' : 'Maximize pane';
    btn.setAttribute('aria-pressed', isMax ? 'true' : 'false');
}

// ---- Minimize-to-dock (wall mode) -----------------------------------------
//
// Minimizing a tile removes it from the Gridstack engine (freeing its cells)
// but LEAVES the element in the DOM, hidden — so the live ttyd iframe is never
// detached and never reloads. A chip lands in #term-min-dock; clicking it
// re-adopts the same element via makeWidget at its saved coords. The set of
// minimized wids is persisted to pc_wall_minimized so the dock survives a
// reload (buildWall re-applies it after rebuilding the wall).
let _minimized = _loadMinimized();

function _loadMinimized() {
    try { return new Set(JSON.parse(localStorage.getItem('pc_wall_minimized') || '[]')); }
    catch (e) { return new Set(); }
}
function _saveMinimized() {
    localStorage.setItem('pc_wall_minimized', JSON.stringify([..._minimized]));
}

// ---- Per-pane layout pin ----------------------------------------------------
// A pinned pane sits out of applyGridLayout's auto-reflow (grid presets): its
// x/y/w/h are left exactly as the user placed them while every other pane
// rearranges around it. Persisted by wid so a pin survives a wall rebuild.
let _pinned = _loadPinned();

function _loadPinned() {
    try { return new Set(JSON.parse(localStorage.getItem('pc_wall_pinned') || '[]')); }
    catch (e) { return new Set(); }
}
function _savePinned() {
    localStorage.setItem('pc_wall_pinned', JSON.stringify([..._pinned]));
}

function _pinBtnHTML(wid) {
    const on = _pinned.has(wid);
    const title = on
        ? "Pinned — this pane keeps its spot when a grid layout is applied. Click to unpin"
        : "Pin — keep this pane's position when a grid layout is applied";
    return `<button class="gs-pin-btn popover-item ov-item${on ? ' on' : ''}" onclick="chela.termPinToggle(this,'${_jsStr(wid)}')"
      aria-pressed="${on ? 'true' : 'false'}" title="${attrEsc(title)}"><span class="ov-ic">${lucideIcon('pin', 14)}</span><span>Pin</span></button>`;
}

// Toggle a pane's pin state. applyGridLayout reads _pinned directly (no
// rebuild needed here) — this only flips the button + tile visuals and persists.
function termPinToggle(btn, wid) {
    if (_pinned.has(wid)) _pinned.delete(wid); else _pinned.add(wid);
    _savePinned();
    const on = _pinned.has(wid);
    btn.classList.toggle('on', on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    btn.title = on
        ? "Pinned — this pane keeps its spot when a grid layout is applied. Click to unpin"
        : "Pin — keep this pane's position when a grid layout is applied";
    const item = btn.closest('.grid-stack-item');
    if (item) item.classList.toggle('pane-pinned', on);
}

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
    _minimized.add(wid);
    _saveMinimized();
    renderMinDock();
    _refitWallForDock();        // wall re-packs above the (taller) dock
}

function termMinFor(btn) {
    const item = btn && btn.closest('.grid-stack-item');
    if (item) minimizePane(item.getAttribute('gs-id'));
}

// Dock chip click: the dock holds only minimized panes now, so a click always
// restores. (minimizePane is invoked from the tile header's minimize button.)
function toggleDockChip(wid) {
    if (_minimized.has(wid)) restoreFromDock(wid);
    else minimizePane(wid);
}

// ---- Lazy tiles: a DISPATCHED worker opens MINIMIZED, and POPS OUT when it blocks --
//
// ⛔ THE RULE, and it is one rule across two surfaces: a dispatched worker must not
// occupy human attention surface — a Telegram topic (CMX-73) OR a Wall tile (this) —
// until it needs a human. The wall is where you WATCH the fleet; five workers grinding
// through a backlog tile it edge to edge and crowd out the one session you are in, and
// none of them wanted watching. So a worker the dispatcher owns opens as a chip in the
// dock (its ttyd iframe live and scrollable the whole time — minimize never detaches
// it) and takes a tile the moment it BLOCKS on you. The wall stops being a wall of
// noise and becomes what the topic list already is: the agents that want a human.
//
// The two facts come from /api/agents, both server-derived (see app.py):
//   `dispatched`  — the RUN ROW owns the window id (never a guess from the name);
//   `needs_human` — blocked on a human NOW: claude's own `waiting`, OR the hook log,
//                   OR the pane (a Bash/Edit PERMISSION gate lives only as pixels).
//
// Per-wid state is a persisted 3-value machine — absent → 'docked' → 'popped' — and
// the reason it exists rather than "just re-read the flags each tick" is that this must
// NEVER FIGHT THE HUMAN. Re-deriving would re-minimize a pane the moment you pulled it
// out of the dock, forever. So each decision is taken exactly ONCE per window:
//   * absent  — never decided. Working → dock it ('docked'). Already blocked → leave it
//               on the wall ('popped'): it was born wanting you.
//   * 'docked'— we hid it, and we are the only one who may un-hide it: when it blocks,
//               pop it out. Then 'popped', permanently.
//   * 'popped'— hands off. Minimize it, restore it, ignore it — the wall never touches
//               that window again, whatever it does next.
// It is keyed by wid and persisted for the same reason `pc_wall_minimized` is: a reload
// must not re-litigate a decision (which, mid-gate, would re-dock the very pane you were
// answering). It is pruned in dropTerminalPane when the window dies, and disowned below
// the moment a wid stops being dispatched — tmux recycles `@N` after a server restart,
// and a stale 'docked' must never leave a HUMAN's window hidden.
let _autoDock = _loadAutoDock();

function _loadAutoDock() {
    try { return JSON.parse(localStorage.getItem('pc_wall_autodock') || '{}') || {}; }
    catch (e) { return {}; }
}
function _saveAutoDock() {
    localStorage.setItem('pc_wall_autodock', JSON.stringify(_autoDock));
}
function _forgetAutoDock(wid) {
    if (!(wid in _autoDock)) return;
    delete _autoDock[wid];
    _saveAutoDock();
}

// Run the machine over one poll's worth of agents. Called from _applyTermStatus, which
// runs AFTER the tiles exist on both paths in (renderTerminals → buildWall, and
// termTick → _absorbFreshTerminals) — a tile we cannot see yet is simply decided on the
// next tick (4s), never dropped.
function _lazyDock(agents) {
    if (WALL_TILE_DISPATCHED) return;              // opt-out: tile every worker like a human's
    if (!Array.isArray(agents)) return;
    if (_termMode !== 'wall' || !_grid) return;    // the dock is a wall affordance
    let dirty = false;
    for (const a of agents) {
        const wid = a && a.window_id;
        if (!wid) continue;
        const state = _autoDock[wid];

        // Not (or no longer) the dispatcher's. A tile we docked on a worker's behalf
        // must not stay hidden once that wid is someone else's — tmux hands `@N` out
        // afresh after a server restart, and hiding a human's terminal is the one
        // failure this feature is not allowed to have.
        if (!a.dispatched) {
            if (state === 'docked' && _minimized.has(wid)) restoreFromDock(wid);
            if (state) { delete _autoDock[wid]; dirty = true; }
            continue;
        }

        if (!state) {
            if (wantsHuman(a)) {
                _autoDock[wid] = 'popped';         // born blocked — it keeps its tile
            } else if (_renderedWids.includes(wid)) {
                minimizePane(wid);                 // working quietly — off the attention surface
                _autoDock[wid] = 'docked';
            } else {
                continue;                          // no tile yet — decide on the next tick
            }
            dirty = true;
        } else if (state === 'docked' && wantsHuman(a)) {
            // 🗜️ THE POP-OUT. It wants a human, so it has earned the wall.
            if (_minimized.has(wid)) restoreFromDock(wid);
            _autoDock[wid] = 'popped';             // one pop, ever — from here it is yours
            dirty = true;
        }
    }
    if (dirty) _saveAutoDock();
}

// ---- Taskbar drag-reorder (order drives the wall layout) -------------------
// pc_pane_order is the user's manual ordering (list of wids); _orderedWids
// sorts the live panes by it (unknowns keep discovery order at the end), and
// applyGridLayout lays tiles out in the same order — so dragging a chip
// re-sorts the grid.
function _paneOrderList() {
    try { return JSON.parse(localStorage.getItem('pc_pane_order') || '[]'); } catch (e) { return []; }
}
function _orderIndex(wid) {
    const i = _paneOrderList().indexOf(wid);
    return i === -1 ? 1e9 : i;
}
function _orderedWids(wids) {
    return wids.slice().sort((a, b) =>
        (_orderIndex(a) - _orderIndex(b)) || (wids.indexOf(a) - wids.indexOf(b)));
}

// The dock sits below the stage and changes the wall's available height, so
// after it shows/hides we re-apply the active fill preset to re-fit the wall
// (the remaining tiles re-pack to fill the space above the dock). Guarded on a
// preset being active — a manual drag-layout is left exactly as the user set it.
function _refitWallForDock() {
    if (_termMode !== 'wall' || !_wallPreset || !_grid) return;
    applyGridLayout(_wallPreset.cols, _wallPreset.rows);
}

function restoreFromDock(wid) {
    const stage = $('#term-stage');
    const item = Array.from(stage.querySelectorAll('.grid-stack-item'))
        .find(it => it.getAttribute('gs-id') === wid);
    _minimized.delete(wid);
    _saveMinimized();
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
    renderMinDock();
    _refitWallForDock();        // dock may have emptied → grow the wall back
}

// Live busy/idle/waiting indicator. The dot starts neutral and is coloured by
// _applyTermStatus from /api/agents (status from `claude agents --json`):
// busy -> green pulse, waiting -> amber (needs input), idle/unknown -> dim.
function _statusDot(wid) {
    return `<span class="term-status-dot" data-status-for="${attrEsc(wid)}" title="…"></span>`;
}

const _STATUS_CLASS = { busy: 'working', waiting: 'waiting', idle: 'idle' };
const _STATUS_TITLE = { busy: 'Working', waiting: 'Waiting for input', idle: 'Idle' };

// Colour the live status dots (pane headers + taskbar chips) from /api/agents.
// "Waiting" is the shared wantsHuman() predicate, NOT session_status alone: a
// dispatched worker stopped at a Bash PERMISSION gate is `busy` to `claude agents
// --json` and blocked in fact, and it is the pane probe (needs_human) that says so.
// Amber it — it is the same agent the wall is about to pop out of the dock.
function _colorTermDots(agents) {
    if (!agents) return;
    const by = {};
    agents.forEach(a => { if (a.window_id) by[a.window_id] = a; });
    document.querySelectorAll('#panel-terminals .term-status-dot').forEach(dot => {
        const a = by[dot.dataset.statusFor];
        const st = a ? a.session_status : null;
        const wants = wantsHuman(a);
        dot.classList.remove('working', 'waiting', 'idle');
        dot.classList.add(wants ? 'waiting' : (_STATUS_CLASS[st] || 'idle'));
        dot.title = wants ? _STATUS_TITLE.waiting
            : (_STATUS_TITLE[st] || (a && a.claude_running ? 'Idle' : 'No Claude session'));
        // Flag the host surface (live pane OR taskbar chip) so a "waiting for
        // input" pane gets a yellow border even when minimized to the dock.
        const host = dot.closest('.grid-stack-item-content, .term-pane, .min-chip');
        if (host) host.classList.toggle('term-waiting', wants);
    });
}

// Status tick: stamp last-busy times (drives the MRU sort), recolour the dots,
// and — if the new activity changed the chip order — re-render the taskbar.
function _applyTermStatus(agents) {
    if (!agents) return;
    _seedSharedFromAgents(agents);            // keep share-button state authoritative
    _renderedWids.forEach(_updateShareBtns);  // refresh accent/badge after each poll
    const now = Date.now();
    agents.forEach(a => {
        if (!a.window_id) return;
        const st = a.session_status;
        if (st === 'busy' && _paneStatus[a.window_id] !== 'busy') _paneActivity[a.window_id] = now;  // rising edge
        _paneStatus[a.window_id] = st;
    });
    _lazyDock(agents);   // dock the dispatcher's quiet workers; pop out the ones that block
    _colorTermDots(agents);
    // Wall tick is the fast path (4s) — refresh the tab signal here too so the
    // title/favicon update promptly on the flagship view, not just on the 30s
    // sidebar refresh. updateTabSignal is idempotent, so the two callers agree.
    updateTabSignal(agents);
    if (_termMode === 'wall' && _renderedWids.length
        && _taskbarOrder(_renderedWids).join(',') !== _dockOrderSig) {
        renderMinDock();   // a pane's recency changed → reflow the chips
    }
}

// Fill the per-tile context bars from /api/agents/context, keyed by window_id.
// statusline source = exact; transcript source = estimate (dimmed fill, "~"/est).
let _ctxCache = [];
function _applyTermContext(ctx) {
    if (!ctx) return;
    _ctxCache = ctx;
    const by = {};
    ctx.forEach(c => { if (c.window_id) by[c.window_id] = c; });
    document.querySelectorAll('#panel-terminals .term-ctx-bar').forEach(bar => {
        const c = by[bar.dataset.ctxFor];
        const fill = bar.querySelector('.term-ctx-fill');
        if (!fill) return;
        // CMX-117: branch + context chips moved OUT of the top .gs-head into this
        // same subtle bottom bar (_ctxBarHTML) — read/write them from `bar` itself
        // now, not the header.
        const head = bar.parentElement && bar.parentElement.querySelector('.gs-head');
        const ctxChip = bar.querySelector('.gs-ctx');
        const branchChip = bar.querySelector('.gs-branch');
        if (!c || c.used_pct == null) {
            fill.style.width = '0';
            fill.className = 'term-ctx-fill';
            bar.title = 'Context: —';
            if (ctxChip) ctxChip.hidden = true;
            if (branchChip) branchChip.hidden = true;
            return;
        }
        const pct = c.used_pct;
        fill.style.width = Math.min(100, pct) + '%';
        const sev = pct > 80 ? ' ctx-danger' : pct > 60 ? ' ctx-warn' : '';
        fill.className = 'term-ctx-fill' + sev + (c.estimated ? ' est' : '');
        const bits = [`Context: ${c.used}/${c.total} (${pct}%${c.estimated ? '~' : ''})`];
        if (c.model) bits.push(c.model);
        if (c.cost_usd != null) bits.push(`$${c.cost_usd}`);
        if (c.estimated) bits.push('estimate — run `chela install-statusline` for exact');
        const tip = bits.join(' · ');
        bar.title = tip;
        // The bar is pointer-events:none (so it can't block resize), so mirror
        // the tooltip onto the tile header — a safe, hoverable surface.
        if (head) head.title = tip;
        // Visible header chips: branch + "74% · 147.5K/1M". The bottom bar gives
        // the at-a-glance color; these give the exact numbers without hovering.
        if (ctxChip) {
            const counter = (c.used && c.total) ? ` · ${c.used}/${c.total}` : '';
            ctxChip.textContent = `${pct}%${c.estimated ? '~' : ''}${counter}`;
            ctxChip.className = 'gs-ctx' + (sev ? ' ' + sev.trim() : '');
            ctxChip.hidden = false;
        }
        if (branchChip) {
            if (c.branch) {
                branchChip.textContent = '⎇ ' + c.branch;
                branchChip.title = 'branch: ' + c.branch;
                branchChip.hidden = false;
            } else {
                branchChip.hidden = true;
            }
        }
    });
}

// Taskbar order: most-recently-busy first (MRU), with the manual order as a
// stable tiebreak for panes with equal/zero activity. Decoupled from the wall
// grid — a chip moving never disturbs a pane's seat on the wall.
function _taskbarOrder(wids) {
    const base = _orderedWids(wids);
    const waiting = w => (_paneStatus[w] === 'waiting' ? 0 : 1);   // needs-your-input panes jump to the front
    const ts = w => _paneActivity[w] || 0;
    return base.slice().sort((a, b) =>
        (waiting(a) - waiting(b)) || (ts(b) - ts(a)) || (base.indexOf(a) - base.indexOf(b)));
}

// FLIP animation: measure chip rects before a rebuild, then slide each surviving
// chip from its old spot to its new one so MRU reshuffles glide instead of snap.
// Matched by data-wid; chips with no prior position (just appeared) don't animate.
function _flipDock(dock, mutate) {
    const first = {};
    dock.querySelectorAll('.min-chip').forEach(c => { first[c.dataset.wid] = c.getBoundingClientRect(); });
    mutate();
    dock.querySelectorAll('.min-chip').forEach(c => {
        const f = first[c.dataset.wid];
        if (!f) return;
        const l = c.getBoundingClientRect();
        const dx = f.left - l.left, dy = f.top - l.top;
        if (!dx && !dy) return;
        c.style.transition = 'none';
        c.style.transform = `translate(${dx}px, ${dy}px)`;
        requestAnimationFrame(() => {
            c.style.transition = 'transform 160ms ease';
            c.style.transform = '';
        });
    });
}

// Always-on taskbar: one chip per pane (wall mode). Visible panes get an accent
// border; minimized panes are dimmed. Each chip carries the busy/idle/waiting
// status dot and toggles min/restore on click. Hidden only in single mode or
// when there are no panes. Order is MRU (see _taskbarOrder); chips are click-only.
function renderMinDock() {
    const dock = $('#term-min-dock');
    if (!dock) return;
    // A TRUE taskbar: only MINIMIZED panes dock here. The sidebar already lists
    // every agent, so mirroring all panes in the dock was redundant (two lists
    // of the same thing). Now the dock is "things I've tucked away" and is empty
    // (hidden) until you minimize a tile; visible tiles are reordered on the wall.
    const wids = _termMode === 'wall'
        ? _taskbarOrder(_renderedWids).filter(w => _minimized.has(w)) : [];
    if (!wids.length) { dock.style.display = 'none'; dock.innerHTML = ''; _dockOrderSig = ''; return; }
    dock.style.display = 'flex';
    _flipDock(dock, () => {
        dock.innerHTML = '<span class="min-dock-label">Minimized:</span>' + wids.map(wid => {
            const j = _jsStr(wid);
            const min = _minimized.has(wid);
            const cls = min ? 'min-chip min-chip-minimized' : 'min-chip min-chip-active';
            const icon = min ? '&#128471;' : '&#128469;';   // restore vs minimize glyph
            return `<button class="${cls}" data-wid="${attrEsc(wid)}"
              onclick="chela.toggleDockChip('${j}')" title="Click to ${min ? 'restore' : 'minimize'} ${attrEsc(_paneTitle(wid))}">
              ${_statusDot(wid)}
              <span class="min-chip-title">${escHtml(_paneTitle(wid))}</span>
              <span class="min-chip-icon" aria-hidden="true">${icon}</span>
            </button>`;
        }).join('');
    });
    _dockOrderSig = wids.join(',');
    _colorTermDots(_agentsCache);   // colour the just-built chips (no re-sort → no recursion)
}

// Collapsible on-screen key bar. Default = collapsed (HTML ships with .kb-collapsed);
// localStorage remembers the user's choice across sessions.
function kbApplyState() {
    const bar = $('#term-bar'), tog = $('#kb-toggle');
    if (!bar) return;
    const collapsed = localStorage.getItem('pc_kb_collapsed') !== '0';   // default collapsed
    bar.classList.toggle('kb-collapsed', collapsed);
    if (tog) tog.setAttribute('aria-expanded', String(!collapsed));
}

function kbToggle() {
    const bar = $('#term-bar');
    const collapsed = bar.classList.toggle('kb-collapsed');
    localStorage.setItem('pc_kb_collapsed', collapsed ? '1' : '0');
    $('#kb-toggle').setAttribute('aria-expanded', String(!collapsed));
}

async function renderTerminals() {
    if (!TERMINALS_ON) return;   // terminals disabled — DOM (#term-stage etc.) absent
    let agents = _agentsCache;
    if (!agents || !agents.length) { agents = await api('/api/agents'); setAgentsCache(agents); }
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
    $('#term-bar').style.display = _termMode === 'single' ? 'flex' : 'none';
    $('#term-wall-grid').style.display = _termMode === 'wall' ? 'inline-flex' : 'none';
    $('#term-new-shell').style.display = _termMode === 'wall' ? 'inline-block' : 'none';
    _buildGridPicker();
    kbApplyState();
    if (sel.options.length !== wids.length) {
        const prev = sel.value;
        sel.innerHTML = wids.map(w => `<option value="${attrEsc(w)}">${escHtml(_paneTitle(w))}</option>`).join('');
        if (prev && wids.includes(prev)) sel.value = prev;
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

    // Ready -> real iframe; not ready -> spinner placeholder (polled into an
    // iframe by _startPlaceholderPolls once the ttyd port lands).
    const frame = w => _termReady.has(w)
        ? `<iframe class="term-frame" allow="clipboard-read; clipboard-write" src="${_termSrc(w)}" title="${escHtml(_paneTitle(w))}"></iframe>`
        : _termPlaceholder(w);
    const stage = $('#term-stage');
    if (!wids.length) {
        destroyGrid();
        stage.className = '';
        stage.innerHTML = '<div style="padding:20px; text-align:center; color:var(--text-dim);">No active agents</div>';
        _renderedWids = [];
    } else if (_termMode === 'wall') {
        _renderedWids = wids.slice();   // set before buildWall so renderMinDock sees current wids
        buildWall(wids);
        // Fit happens at the end, AFTER the taskbar renders — see _refitWallForDock below.
    } else {
        destroyGrid();
        stage.className = 'term-single';
        const sw = sel.value || wids[0];
        stage.innerHTML = `<div class="term-pane">${paneHead(sw, false)}${frame(sw)}${_ctxBarHTML(sw, false)}</div>`;
        _renderedWids = [sw];
    }
    // Seed readiness pollers for whatever placeholders we just rendered.
    _startPlaceholderPolls();
    renderMinDock();   // single / no-agents → hides the dock; wall already rebuilt it
    _applyTermStatus(_agentsCache);   // colour pane dots (single + wall) from cached state
    _applyTermContext(_ctxCache);     // repaint ctx bars from cache instantly…
    api('/api/agents/context').then(_applyTermContext).catch(() => {});  // …then refresh

    // Fit the wall AFTER the taskbar is at its final rendered height, so the grid
    // leaves exactly the right room above it (dock height feeds _wallFill).
    _refitWallForDock();

    renderMobileSwitcher();   // phone single-mode agent pills (no-op on desktop)
    _bindHeaderSwipe();       // header swipe → prev/next agent (idempotent)
    _refreshRooms();          // paint room accents on the first render, not 4s later
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
let _focusPoll = null;
let _focusedPane = null;

function _applyTermFocus() {
    const ae = document.activeElement;
    const frame = (ae && ae.classList && ae.classList.contains('term-frame')) ? ae : null;
    const pane = frame ? frame.closest('.grid-stack-item-content, .term-pane') : null;
    if (pane === _focusedPane) return;            // unchanged — leave the DOM alone
    if (_focusedPane) _focusedPane.classList.remove('term-focused');
    if (pane) pane.classList.add('term-focused');
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
    if (typeof selectView === 'function') selectView('terminals');
    if (_termMode === 'single') {
        const sel = $('#term-agent');
        if (sel && sel.value !== wid) { sel.value = wid; renderTerminals(); }
        return;
    }
    // Defer so selectView's render settles before we hunt for the tile.
    setTimeout(() => {
        if (_minimized.has(wid)) restoreFromDock(wid);
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
            // page, same global _paneTermDims already reaches for) so typing lands
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

// Single registration at module load. TERMINALS_ON is checked at fire time (it
// may not be resolved yet here), mirroring the window 'resize' handler above.
document.addEventListener('visibilitychange', () => {
    if (!TERMINALS_ON) return;
    clearTimeout(_termHideTimer);
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
    _refreshRooms();   // rooms can also be made from the CLI/hooks — the wall follows
    try {
        const ctx = await api('/api/agents/context');
        _applyTermContext(ctx);
        updateCtxCache(ctx);
    } catch (e) { /* keep prior fills */ }
    // Full sidebar re-render off the same poll: keeps the dots in lockstep with
    // the wall AND lets the "Needs you" triage cluster reorder live as sessions
    // start/finish waiting. Cheap for a short list; group collapse state lives in
    // localStorage so it survives the rebuild.
    renderSidebarAgents(agents);
}

// Update the visible label of every on-screen pane + chip from current state,
// WITHOUT touching the iframe. A window rename only changes display text, so
// this keeps the header/chip/dropdown in sync with zero reload. (During an
// inline rename the `.pane-title` span is swapped for an input of a different
// class, so the query below never clobbers an in-progress edit.)
function _setSpanLabel(span, wid) {
    if (!span || !wid) return;
    const lbl = _paneTitle(wid);
    if (span.textContent !== lbl) span.textContent = lbl;
}
function _refreshPaneLabels() {
    if (!TERMINALS_ON) return;
    if (_termMode === 'wall') {
        $('#term-stage').querySelectorAll('.grid-stack-item').forEach(item => {
            _setSpanLabel(item.querySelector('.pane-title'), item.getAttribute('gs-id'));
        });
        renderMinDock();   // chips carry labels too
    } else {
        const sel = $('#term-agent');
        const pane = $('#term-stage') && $('#term-stage').querySelector('.term-pane');
        if (sel && pane) _setSpanLabel(pane.querySelector('.pane-title'), sel.value);
        if (sel) Array.from(sel.options).forEach(o => {
            const lbl = _paneTitle(o.value);
            if (o.textContent !== lbl) o.textContent = lbl;
        });
    }
    renderMobileSwitcher();   // reflect renames / added / dropped agents in the pills
}

// ---- Keyboard-fast pane switcher (Alt+1..9) + palette-first (Ctrl/⌘+K) -----
// The first 9 panes in stable wall order get a jump key. Shared by the badge
// painter and the shortcut handler so "what a tile shows" and "what Alt+N
// jumps to" can never drift apart. CMX-116: the palette is the primary,
// discoverable pane switcher — Alt+N stays as the power-user fallback.
function _switcherOrder() {
    return _orderedWids(_renderedWids).slice(0, 9);
}

// Paint (or hide) each tile's index badge. Cheap — only touches tiles whose
// number actually changed — call freely after anything that can reorder the
// wall (a build, a surgical append, a pane drop).
function _refreshPaneIdx() {
    if (!TERMINALS_ON || _termMode !== 'wall') return;
    const order = _switcherOrder();
    $('#term-stage').querySelectorAll('.grid-stack-item .gs-idx').forEach(el => {
        if (!el.hidden) { el.hidden = true; el.textContent = ''; }
    });
    order.forEach((wid, i) => {
        const el = _gridItemEl(wid);
        const idx = el && el.querySelector('.gs-idx');
        if (!idx) return;
        idx.textContent = String(i + 1);
        idx.hidden = false;
    });
}

// Alt+1..9: jump straight to that pane, tmux-prefix style — no menu, no
// typing. Ignored while the user is typing anywhere (an input) or while the
// palette is open, so it never steals a keystroke. Shared with the
// iframe-side wiring below so "what counts as a switch key" can't drift.
function _altSwitchWid(e) {
    if (!TERMINALS_ON || _termMode !== 'wall' || !e.altKey || e.ctrlKey || e.metaKey) return null;
    if (!/^[1-9]$/.test(e.key)) return null;
    return _switcherOrder()[Number(e.key) - 1] || null;
}

// Ctrl/⌘+K — the same combo the parent chrome's palette shortcut uses
// (nav.js). Matched the same way here so a pane's own keydown can trigger it.
function _isPaletteKey(e) {
    return (e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && (e.key === 'k' || e.key === 'K');
}

// Capture phase for consistency with the iframe-side listener below (which is
// the load-bearing one — see its comment): harmless here since the parent
// document IS the dispatch target for these keydowns either way.
document.addEventListener('keydown', e => {
    const target = e.target;
    if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;
    const wid = _altSwitchWid(e);
    if (!wid) return;
    e.preventDefault();
    focusPaneByWid(wid);
}, true);

// Once Alt+N focuses a pane, keystrokes land in that ttyd iframe's OWN
// document — a same-origin iframe's keydown never bubbles to the parent, so
// the listener above goes deaf to the next Alt+N (or Ctrl/⌘+K) until focus
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
        const wid = _altSwitchWid(e);
        if (wid) {
            e.preventDefault();
            e.stopImmediatePropagation();
            focusPaneByWid(wid);
            return;
        }
        if (_isPaletteKey(e)) {
            e.preventDefault();
            e.stopImmediatePropagation();
            if (typeof openPalette === 'function') openPalette();
        }
    }, true);
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
    _ownerPresence().then(m => m && m.stopOwnerPresence(wid));  // tear down its presence session
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
                ov.textContent = `${_paneTitle(wid)} — session ended`;
                pane.appendChild(ov);
            }
        }
    }
    _renderedWids = _renderedWids.filter(w => w !== wid);
    if (_minimized.has(wid)) { _minimized.delete(wid); _saveMinimized(); }
    _forgetAutoDock(wid);   // the window is gone: its lazy-dock decision dies with it
    renderMinDock();        // taskbar lists every pane → refresh whenever one drops
    _refitWallForDock();    // taskbar may have shrunk a row → re-fit the wall above it
    _refreshPaneIdx();      // Alt+N badges (keyboard-fast pane switcher)
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
        ? `<iframe class="term-frame" allow="clipboard-read; clipboard-write" src="${_termSrc(wid)}" title="${escHtml(_paneTitle(wid))}"></iframe>`
        : _termPlaceholder(wid);
    const pinnedCls = _pinned.has(wid) ? ' pane-pinned' : '';
    return `<div class="grid-stack-item${pinnedCls}" gs-id="${escHtml(wid)}" gs-x="${x}" gs-y="${y}" gs-w="${w}" gs-h="${h}">
  <div class="grid-stack-item-content">
    ${paneHead(wid, true)}
    ${body}
    ${_ctxBarHTML(wid, true)}
  </div>
</div>`;
}

// Per-tile context-window bar pinned to the tile bottom edge (CMX-117: now the
// subtle home for branch + context, folded together with the ambient fill strip
// — previously two top-header chips PLUS this bar). Filled/coloured by
// _applyTermContext from /api/agents/context, keyed by window_id. CMX-119:
// also the new home for the pane № (Alt+N jump target) chip, moved out of the
// header — filled in and shown/hidden by _refreshPaneIdx, same as before, just
// a different resting place; `draggable` gates it exactly like paneHead's own
// wall-only controls (single mode has no Alt+N switcher to target).
function _ctxBarHTML(wid, draggable) {
    const idxNum = draggable ? `<span class="gs-idx" title="Alt+N to jump here" hidden></span>` : '';
    return `<div class="term-ctx-bar" data-ctx-for="${attrEsc(wid)}" title="Context: —">
      <span class="gs-branch" hidden></span>
      <span class="gs-ctx" hidden></span>
      ${idxNum}
      <i class="term-ctx-fill"></i>
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
    _grid.on('dragstart', (e, el) => { guard(true); dragMode(true); if (_wallLocked) { _snapshotForSwap(); lockDragBox(true); } });
    _grid.on('resizestart', () => guard(true));
    _grid.on('drag', (e, el) => _swapDragHover(el));   // live highlight of the swap target
    // Locked mode turns a drag into a pure swap: exchange the dragged pane's full
    // geometry with whatever pane it was dropped on (revert if dropped on empty).
    _grid.on('dragstop', (e, el) => { guard(false); dragMode(false); if (_wallLocked) _doSwap(el); lockDragBox(false); persist(); });
    _grid.on('resizestop', () => { guard(false); persist(); });

    // Re-apply persisted minimized state: pull those tiles back out of the grid
    // (their iframes still loaded, just hidden) and rebuild the dock chips.
    wids.forEach(wid => {
        if (!_minimized.has(wid)) return;
        const item = gsEl.querySelector(`.grid-stack-item[gs-id="${_cssEsc(wid)}"]`);
        if (item) _minimizeItem(item);
    });
    _applyWallLock();   // restore the locked (swap-on-drag) feel across reloads
    renderMinDock();
    _replayRoomAccents();   // rebuilt tiles: repaint room accents from the last poll
    _refreshPaneIdx();      // Alt+N badges (keyboard-fast pane switcher)
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
        _grid.makeWidget(el);   // v10: adopt an already-appended element as a widget
        if (!_renderedWids.includes(wid)) _renderedWids.push(wid);
    });
    _startPlaceholderPolls();   // seed readiness polls for any placeholders we just added
    _applyWallLock();           // a tile added while locked must inherit no-resize
    renderMinDock();            // taskbar lists every pane → add the new chip(s)
    _refitWallForDock();        // taskbar may have grown a row → re-fit the wall above it
    _replayRoomAccents();       // a fresh tile already in a room wears its accent at once
    _refreshPaneIdx();          // Alt+N badges (keyboard-fast pane switcher)
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

// Inline split-glyph layout picker (editor-style). Each preset is a column
// count + a target row count that fills the viewport height; the glyph depicts
// cols×rows so the shape is obvious at a glance. "N columns" (rows:1) makes
// each pane full viewport height; the ×grids stack `rows` of panes to fill the
// same height. Extra panes beyond cols×rows wrap below and scroll.
const WALL_PRESETS = [
    { cols: 1, rows: 1, label: '1 column (stack)' },
    { cols: 2, rows: 1, label: '2 columns' },
    { cols: 3, rows: 1, label: '3 columns' },
    { cols: 4, rows: 1, label: '4 columns' },
    { cols: 2, rows: 2, label: '2 × 2 grid' },
    { cols: 3, rows: 2, label: '3 × 2 grid' },
];

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
    // The dock lives below the stage, so its height (+ its 8px top-margin gap)
    // eats into the space the wall may fill. Subtract it when it's showing.
    // Phones force single mode (no wall, no dock), so skip the measurement there.
    const dock = $('#term-min-dock');
    const dockH = (!_isMobileTerm() && dock && dock.style.display !== 'none')
        ? dock.getBoundingClientRect().height + 8 : 0;
    const avail = Math.max(240, floorY - top - dockH - 4);  // leave a hair at the bottom
    const rows = Math.max(3, Math.floor(avail / WALL_CELL_H));
    const cellPx = Math.max(40, Math.floor(avail / rows));      // exact divisor -> fills
    return { rows, cellPx };
}

// Build a cols×rows grid glyph as an SVG of rounded rects on a 18×14 canvas.
function _gridGlyph(cols, rows) {
    const W = 18, H = 14, m = 1, g = 1.5;
    const cw = (W - 2 * m - (cols - 1) * g) / cols;
    const ch = (H - 2 * m - (rows - 1) * g) / rows;
    let rects = '';
    for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
            const x = (m + c * (cw + g)).toFixed(2);
            const y = (m + r * (ch + g)).toFixed(2);
            rects += `<rect x="${x}" y="${y}" width="${cw.toFixed(2)}" height="${ch.toFixed(2)}" rx="1"/>`;
        }
    }
    return `<svg viewBox="0 0 ${W} ${H}" width="18" height="14" fill="currentColor" aria-hidden="true">${rects}</svg>`;
}

// Lucide lock / lock-open (https://lucide.dev/icons/lock), 14px to match the
// preset glyphs. Stroke-based, so it inherits .gl-btn's currentColor.
function _lockGlyph(locked) {
    const shackle = locked ? 'M7 11V7a5 5 0 0 1 10 0v4' : 'M7 11V7a5 5 0 0 1 9.9-1';
    return `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"
        stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="${shackle}"/></svg>`;
}

// Populate the toolbar's glyph picker once. Idempotent (dataset guard) so the
// per-render renderTerminals() call is cheap.
function _buildGridPicker() {
    const host = $('#term-grid-presets');
    if (!host) return;
    if (!host.dataset.built) {
        host.innerHTML = WALL_PRESETS.map((p, i) =>
            `<button class="gl-btn" data-preset="${i}" title="${escHtml(p.label)}" aria-label="${escHtml(p.label)}"
                     onclick="chela.applyGridLayout(${p.cols}, ${p.rows}, this)">${_gridGlyph(p.cols, p.rows)}</button>`
        ).join('');
        host.dataset.built = '1';
    }
    // Reflect the active preset (default/persisted, or last clicked) on the buttons.
    host.querySelectorAll('.gl-btn').forEach((b, i) => {
        const p = WALL_PRESETS[i];
        b.classList.toggle('active', !!(_wallPreset && p.cols === _wallPreset.cols && p.rows === _wallPreset.rows));
    });
    _reflectLockBtn();
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

function _reflectLockBtn() {
    const b = $('#term-lock-btn');
    if (!b) return;
    b.classList.toggle('active', _wallLocked);
    b.innerHTML = _lockGlyph(_wallLocked);   // lucide lock / lock-open
    b.title = _wallLocked
        ? 'Layout locked — drag a pane onto another to swap them. Click to unlock (free move + resize).'
        : 'Lock layout — drag swaps panes, resize off';
}

// Push the current lock state onto the live grid. Safe to call any time _grid
// exists (e.g. right after buildWall, so a reload restores the locked feel).
function _applyWallLock() {
    if (!_grid) return;
    _grid.enableResize(!_wallLocked); // no resizing while locked (move stays on → drag-to-swap)
    _reflectLockBtn();
}

function toggleWallLock(btn) {
    _wallLocked = !_wallLocked;
    localStorage.setItem('pc_wall_locked', _wallLocked ? '1' : '0');
    _applyWallLock();
    if (btn) btn.blur();
}

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

function _swapDragHover(el) { if (_wallLocked) _setSwapHover(_swapTargetWid(el)); }

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

// ---- THE WIRE: drag a line between two tiles to put their agents in a room ---
//
// The relationship is chela/rooms.py's (typed ledger, active dispatch, loop
// guards); this is the gesture that CREATES one, and nothing more.
//
// Three things make it work on a wall of live iframes:
//
//   1. THE IFRAMES MUST NOT EAT THE MOUSE. Same idiom the tile drag already uses:
//      `.gs-dragging` on the grid → CSS drops `pointer-events` on every
//      `.term-frame`, so mousemove/elementFromPoint see the TILES. (NOT
//      `.term-dragging`, which HIDES the terminals — watching them is the point.)
//   2. THE PORT MUST STEAL THE MOUSEDOWN, or gridstack starts a tile drag under
//      us (`renamePane` does exactly this).
//   3. EVERY OTHER TILE SPROUTS A SOCKET while a wire is in flight (`.wire-live`
//      on the stage). Without that nobody discovers the gesture exists.
//
// Room state NEVER reaches `_termSig` — the accent is patched per tile
// (applyRoomAccents), so wiring two agents together reloads no terminal.

const SVG_NS = 'http://www.w3.org/2000/svg';
let _wire = null;         // in-flight drag: {fromWid, svg, path, stage, gsEl, hoverWid, x1, y1}
let _roomsCache = null;   // last /api/rooms payload, replayed onto freshly built tiles

function _wireCleanup() {
    if (!_wire) return;
    const { svg, stage, gsEl, hoverWid } = _wire;
    document.removeEventListener('mousemove', _wireMove);
    document.removeEventListener('mouseup', _wireDrop);
    document.removeEventListener('keydown', _wireKey);
    document.removeEventListener('pointercancel', _wireCleanup);
    window.removeEventListener('blur', _wireCleanup);
    if (svg) svg.remove();
    if (stage) stage.classList.remove('wire-live');
    if (gsEl) gsEl.classList.remove('gs-dragging');
    if (hoverWid) { const el = _gridItemEl(hoverWid); if (el) el.classList.remove('wire-target'); }
    const src = _gridItemEl(_wire.fromWid);
    if (src) src.classList.remove('wire-source');
    _wire = null;
}

// Where the pointer is, in #term-stage coordinates (the SVG overlay's box).
function _wirePoint(e) {
    const r = _wire.stage.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
}

function wireDragStart(e, btn, wid) {
    e.preventDefault();
    e.stopPropagation();          // or gridstack takes the gesture as a tile drag
    if (!TERMINALS_ON || _termMode !== 'wall') return;
    const stage = $('#term-stage');
    const gsEl = stage && stage.querySelector('.grid-stack');
    if (!stage || !gsEl) return;
    _wireCleanup();               // never two wires at once
    // CMX-117: Wire now lives inside the badge-anchored popover — close it the
    // instant the drag starts so a `position:fixed` menu never sits open (and
    // floating on top of the wall) for the whole gesture; it would otherwise
    // outlive the drag until the next document click.
    _closeAllPaneOverflows();

    gsEl.classList.add('gs-dragging');   // iframes stop swallowing the mouse
    stage.classList.add('wire-live');    // every tile sprouts a drop socket
    const src = _gridItemEl(wid);
    if (src) src.classList.add('wire-source');

    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('class', 'wire-overlay');
    const path = document.createElementNS(SVG_NS, 'path');
    svg.appendChild(path);
    stage.appendChild(svg);

    // CMX-123: origin from the SOURCE PANE's top-center, not `btn` — `btn` is the
    // "Wire to…" row inside the badge/⋮ overflow menu, a `position:fixed` portal, so
    // anchoring there made the wire visibly start from the floating menu instead of
    // the pane it's wiring from.
    const b = (src || btn).getBoundingClientRect(), s = stage.getBoundingClientRect();
    _wire = {
        fromWid: wid, svg, path, stage, gsEl, hoverWid: null,
        x1: b.left + b.width / 2 - s.left, y1: b.top - s.top,
    };
    _wireMove(e);
    document.addEventListener('mousemove', _wireMove);
    document.addEventListener('mouseup', _wireDrop);
    document.addEventListener('keydown', _wireKey);
    // A HELD gesture can end where `document` cannot see it: release the button over
    // devtools, a second monitor, or the OS desktop and the mouseup never arrives —
    // `.gs-dragging` would then outlive the drag, and it is what puts
    // `pointer-events: none` on EVERY .term-frame (style.css). The wall would go dead
    // to clicks for the whole fleet, with nothing to heal it (buildWall runs only when
    // the fleet changes). So every way out is wired to the same cleanup.
    document.addEventListener('pointercancel', _wireCleanup);   // touch stolen, devtools
    window.addEventListener('blur', _wireCleanup);               // dragged off the window
}

// The wid of the tile under the pointer (or null — empty stage, or a tile with
// no gs-id, both of which are a CANCEL, not a room).
function _wireHit(e) {
    const el = document.elementFromPoint(e.clientX, e.clientY);
    const item = el && el.closest && el.closest('#term-stage .grid-stack-item');
    return (item && item.getAttribute('gs-id')) || null;
}

function _wireMove(e) {
    if (!_wire) return;
    // The primary button is no longer down, and we never saw it come up: it was
    // released off-window (blur can lag, or never fire if the window kept focus while
    // the cursor left it). The gesture is OVER — and it ended on nothing we saw, so it
    // is a CANCEL, not a drop: a room the user did not aim at is worse than no room.
    if (e.buttons !== undefined && !(e.buttons & 1)) return _wireCleanup();
    const p = _wirePoint(e);
    _wire.path.setAttribute('d', bezierPath(_wire.x1, _wire.y1, p.x, p.y));
    const hit = _wireHit(e);
    const next = resolveDrop(_wire.fromWid, hit).ok ? hit : null;
    if (next === _wire.hoverWid) return;
    if (_wire.hoverWid) { const prev = _gridItemEl(_wire.hoverWid); if (prev) prev.classList.remove('wire-target'); }
    _wire.hoverWid = next;
    if (next) { const el = _gridItemEl(next); if (el) el.classList.add('wire-target'); }
}

function _wireKey(e) { if (e.key === 'Escape') _wireCleanup(); }

async function _wireDrop(e) {
    if (!_wire) return;
    const fromWid = _wire.fromWid;
    const drop = resolveDrop(fromWid, _wireHit(e));
    const anchor = _gridItemEl(fromWid);
    _wireCleanup();
    if (!drop.ok) return;         // self / empty stage / no wid → nothing created
    const head = anchor && anchor.querySelector('.gs-head');
    try {
        const res = await api('/api/rooms/join', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ wids: drop.wids }),
        });
        if (!res || res.error) { _termShareToast(head, res && res.error ? res.error : 'wire failed'); return; }
        _termShareToast(head, `🔌 ${_displayLabel(_nameOfWid(drop.wids[1]))} joined ${res.room}`);
    } catch (err) {
        _termShareToast(head, 'wire failed — could not reach the dashboard');
        return;
    }
    await _refreshRooms();
}

// Click a tile's room badge → leave that room. (The only room *un*-wiring in the
// UI; the ledger itself is untouched — leaving is a membership edit, not a purge.)
async function wireRoomClick(btn, wid) {
    const room = btn && btn.getAttribute('data-room');
    if (!room) return;
    try {
        await api('/api/rooms/leave', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ wid, room }),
        });
    } catch (e) { /* transient — the next poll re-reads the truth */ }
    await _refreshRooms();
}

// Read the rooms (the SAME dict `chela room status` prints) and PATCH the tiles.
// Never re-renders the wall: `_termSig` is not consulted and not touched, so no
// iframe is recreated when membership changes.
async function _refreshRooms() {
    if (!TERMINALS_ON || _termMode !== 'wall') return;
    let status;
    try { status = await api('/api/rooms'); } catch (e) { return; }   // transient — next tick
    _roomsCache = status;
    applyRoomAccents($('#term-stage'), status);
}

// Repaint accents from cache onto tiles that were just (re)built — a full
// buildWall or a surgical addWallTiles produces bare tiles, and the room state is
// already known, so there's no reason to make them wait for the next poll.
function _replayRoomAccents() {
    if (_roomsCache) applyRoomAccents($('#term-stage'), _roomsCache);
}

// Apply a layout to the current wall panes, sized to fill the viewport height
// with NO bottom gap.
//
//  - Column presets (rows === 1): distribute every pane across `cols` columns
//    (balanced — earlier columns take the remainder), and split each column's
//    height evenly among its panes. The last pane in each column absorbs the
//    rounding remainder so every column reaches the bottom. All panes visible.
//  - Grid presets (rows > 1): a fixed cols×rows grid; each tile is 1/rows of
//    the viewport height, flowed row-major. Panes beyond cols×rows wrap below
//    and scroll. (Fewer panes than cells leaves the trailing cells empty — the
//    explicit-grid trade-off.)
function applyGridLayout(cols, rows, btn) {
    const host = $('#term-grid-presets');
    if (btn) {
        host.querySelectorAll('.gl-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
    }
    // Remember the active preset so a viewport resize can re-fit it (the fill
    // is viewport-relative, so a different screen / window size needs a recompute).
    _wallPreset = { cols, rows };
    localStorage.setItem('pc_wall_preset', JSON.stringify(_wallPreset));   // persist for reloads + resize re-fit
    if (!_grid) return;
    const { rows: total, cellPx } = _wallFill();
    _grid.cellHeight(cellPx);                  // exact divisor so `total` rows fill the height
    // Lay panes out in the taskbar order (pc_pane_order); fall back to current
    // column-order so an un-reordered wall keeps its existing positions. Pinned
    // panes are excluded entirely — a preset reflows every OTHER pane around
    // them, leaving a pinned tile's x/y/w/h exactly where the user put it
    // (per-pane layout pin, CMX-108).
    const nodes = _grid.engine.nodes.slice()
        .filter(n => !_pinned.has(n.id))
        .sort((a, b) => (_orderIndex(a.id) - _orderIndex(b.id)) || (a.x - b.x) || (a.y - b.y));
    const N = nodes.length;
    if (!N) return;

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
            // Row-major in taskbar order so cells fill left-to-right, top-down.
            nodes.sort((a, b) => (_orderIndex(a.id) - _orderIndex(b.id)) || (a.y - b.y) || (a.x - b.x));
            nodes.forEach((node, i) => {
                const r = Math.floor(i / cols);
                _grid.update(node.el, { x: (i % cols) * w, y: rowY(r), w, h: rowH(r) });
            });
        }
    } finally {
        _grid.batchUpdate(false);
    }
    const out = {};
    _grid.save(false).forEach(nd => {
        if (nd.id != null) out[nd.id] = { x: nd.x, y: nd.y, w: nd.w, h: nd.h };
    });
    localStorage.setItem('pc_wall_layout', JSON.stringify(out));
}

// ---- Mobile agent switcher -------------------------------------------------
//
// On phones the wall is forced to single mode (one full-width pane); the desktop
// dropdown is a poor touch target, so we render a horizontally-scrollable strip
// of agent pills above the pane instead (status dot + label, active highlighted).
// Tapping a pill switches the pane; the live ttyd terminal is a same-origin
// iframe that swallows its own touches, so direct swipe-on-terminal can't be
// caught — we instead allow a swipe on the pane HEADER (parent DOM) to step
// prev/next. The dropdown (#term-agent) stays in the DOM as the value holder; the
// strip reads/writes sel.value and calls renderTerminals(), so it reuses the
// exact single-mode switch path with no new state.

function _isMobileTerm() {
    return window.matchMedia('(max-width: 768px)').matches;
}

// Build/refresh the pill strip. Hidden unless mobile + single mode with panes.
function renderMobileSwitcher() {
    const host = document.getElementById('term-switcher');
    if (!host) return;
    const sel = $('#term-agent');
    const wids = sel ? Array.from(sel.options).map(o => o.value) : [];
    const show = TERMINALS_ON && _isMobileTerm() && _termMode === 'single' && wids.length > 0;
    if (!show) { host.style.display = 'none'; host.innerHTML = ''; return; }
    host.style.display = 'flex';
    const active = sel.value || wids[0];
    // Pills only: starting a share moved to the topbar overflow menu ("Share current
    // session" → shareCurrentAgent), so the row no longer carries its own 🔗 button.
    host.innerHTML = wids.map(w => {
        const cls = 'term-pill' + (w === active ? ' active' : '');
        return `<button class="${cls}" data-wid="${attrEsc(w)}" onclick="chela.switchAgentMobile('${_jsStr(w)}')">
          ${_statusDot(w)}<span class="term-pill-label">${escHtml(_paneTitle(w))}</span>
        </button>`;
    }).join('');
    _colorTermDots(_agentsCache);   // tint the just-built pill dots
    // Centre the active pill without scrolling the page (no scrollIntoView).
    const activeEl = host.querySelector('.term-pill.active');
    if (activeEl) {
        host.scrollLeft = activeEl.offsetLeft - (host.clientWidth - activeEl.clientWidth) / 2;
    }
}

// Share the CURRENT terminal session (the active agent, #term-agent's value — the
// same handle termKey uses). Invoked from the topbar overflow menu; reuses the
// full share flow (shareBtnClick), so on mobile it opens the bottom-sheet and the
// global "N sharing" indicator lights. No current session (e.g. a non-terminals
// view) → fall back to the command palette's per-session Share list.
function shareCurrentAgent() {
    const sel = document.getElementById('term-agent');
    const wid = sel && sel.value;
    if (wid) { shareBtnClick(null, wid); return; }
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
    const wids = Array.from(sel.options).map(o => o.value);
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

// Keep the strip in sync when the viewport crosses the mobile breakpoint.
window.addEventListener('resize', () => {
    if (TERMINALS_ON && currentTab === 'terminals') renderMobileSwitcher();
});

// ---- Mobile keybar v2: sticky-Ctrl modifier -------------------------------
//
// Modeled on Moshi's terminal keyboard. The Ctrl key is a three-state toggle
// (tap cycles off → armed → locked → off). Engaging it reveals a Ctrl-letter
// layer; tapping a letter sends C-<letter>. Armed = consumed after one letter
// (Moshi's "next keystroke"); locked = stays for sequences (^C ^D ^Z without
// re-tapping). The other keys (Esc/arrows/chars/paging) are independent of the
// modifier — they have no useful Ctrl variant — and go straight through
// termKey(). State lives in the keybar's data-ctrl attr (CSS reveals the layer).
const _KB_CTRL_STATES = ['off', 'armed', 'locked'];

function _kbCtrlState() {
    const bar = document.getElementById('term-keybar');
    return bar ? (bar.dataset.ctrl || 'off') : 'off';
}
function _kbSetCtrl(state) {
    const bar = document.getElementById('term-keybar');
    if (bar) bar.dataset.ctrl = state;
}

// Tap the Ctrl key: advance the three-state cycle.
function kbCtrlTap() {
    const i = _KB_CTRL_STATES.indexOf(_kbCtrlState());
    _kbSetCtrl(_KB_CTRL_STATES[(i + 1) % _KB_CTRL_STATES.length]);
}

// Tap a Ctrl-letter: send C-<letter>; disarm if armed (one-shot), keep if locked.
function kbCtrlKey(letter) {
    termKey('C-' + letter);
    if (_kbCtrlState() === 'armed') _kbSetCtrl('off');
}

// Pin the keybar just above the on-screen keyboard. When the soft keyboard
// opens, the visual viewport shrinks below the layout viewport. We anchor the
// bar's TOP to the visual viewport's bottom edge — (offsetTop + height) in
// layout-viewport coords, minus the bar's own height — rather than lifting a
// bottom:0 bar with translateY. The translate approach floats the bar up over
// the terminal on iOS: rubber-band scrolling fires `scroll` with a changing
// offsetTop, and a translated bottom-fixed bar chases it mid-gesture. Anchoring
// `top` to the live viewport bottom is stable through that scroll. Best-effort:
// where VisualViewport is unavailable the CSS bottom:0 keeps the bar in place.
function _kbPin() {
    const bar = document.getElementById('term-keybar');
    const vv = window.visualViewport;
    if (!bar || !vv) return;
    const top = Math.max(0, Math.round(vv.offsetTop + vv.height - bar.offsetHeight));
    bar.style.bottom = 'auto';
    bar.style.top = top + 'px';
}
if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', _kbPin);
    window.visualViewport.addEventListener('scroll', _kbPin);
}

// --- Stage 0: ES-module exports ---
export { _absorbFreshTerminals, _cssEsc, _displayLabel, _jsStr, _minimized, _orderedWids, _refreshPaneLabels, _renderedWids, _sharedWids, _stopReadyPoll, _stopShare, _swapToFrame, _termReady, dropTerminalPane, focusPaneByWid, renderTerminals, termTick, shareBtnClick, startTermTimer, stopTermTimer };

// --- Stage 0: window.chela — surface reachable from inline HTML handlers ---
window.chela = window.chela || {};
Object.assign(window.chela, { applyGridLayout, kbCtrlKey, kbCtrlTap, kbToggle, openSharesSheet, orchestratorBtnClick, renamePane, renderTerminals, retryReady, setTermMode, shareBtnClick, shareCurrentAgent, spawnShell, switchAgentMobile, termKey, termKillClick, termKillConfirm, termMaxFor, termMinFor, termPaste, termPinToggle, termScrollToggle, toggleDockChip, togglePaneOverflow, toggleWallLock, wireDragStart, wireRoomClick });
