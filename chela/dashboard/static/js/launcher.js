// --- Stage 0: ES-module imports ---
import { $, TERMINALS_ON, _agentsCache, api, attrEsc, escHtml, setAgentsCache } from './util.js';
import { _jsStr, focusPaneByWid } from './terminals.js';
import { refreshSidebar, renderSidebarAgents, selectView } from './nav.js';

// ---------------------------------------------------------------------------
// Launcher: Recent + Favorites click-to-launch (sidebar)
// ---------------------------------------------------------------------------
//
// One tap launches a Claude agent in a project directory. Recent auto-populates
// from past launches (server-side MRU); Favorites are user-pinned. Clicking a row
// spawns a tmux window in that dir and runs `claude`; the ⌫ glyph opens a plain
// shell there instead, and the star pins/unpins. State lives server-side
// (/api/launcher) so the same lists appear on every device. If a live Claude
// agent already runs in a dir, a launch focuses the wall instead of spawning a
// twin (dedup by the agent's resolved cwd).
//
// Loaded only when terminals are enabled (it shares _jsStr/attrEsc/escHtml/api
// with terminals.js + util.js, and a launch is a tmux spawn).

let _launcherData = { recent: [], favorites: [] };

async function refreshLauncher() {
    if (typeof TERMINALS_ON === 'undefined' || !TERMINALS_ON) return;
    try {
        const d = await api('/api/launcher');
        if (d) _launcherData = { recent: d.recent || [], favorites: d.favorites || [] };
    } catch (e) { return; }   // transient — keep the last render
    renderLauncher();
}

// One launcher row. `pinned` toggles the star between filled (unpin) and outline
// (pin). A missing dir renders dimmed with a ⚠ marker but stays clickable (the
// spawn will surface a clear error if it's truly gone).
function _launchRow(e, pinned) {
    const j = _jsStr(e.path);
    const star = pinned
        ? `<button class="lr-star pinned" title="Unpin from Favorites"
             onclick="event.stopPropagation(); chela.unpinFav('${j}')">&#9733;</button>`
        : `<button class="lr-star" title="Pin to Favorites"
             onclick="event.stopPropagation(); chela.pinFav('${j}')">&#9734;</button>`;
    // Recent rows carry a × to forget them; favorites are removed via the star.
    const forget = pinned ? '' :
        `<button class="lr-forget" title="Remove from Recent"
           onclick="event.stopPropagation(); chela.forgetRecent('${j}')">&#10005;</button>`;
    const gone = e.exists ? '' : ' lr-gone';
    return `<div class="side-item launch-row${gone}" data-path="${attrEsc(e.path)}"
        title="${attrEsc(e.path)}${e.exists ? '' : ' (missing)'}"
        onclick="chela.launchProject(this.dataset.path)">
        <span class="lr-icon">${e.exists ? '&#9656;' : '&#9888;'}</span>
        <span class="lr-label">${escHtml(e.label)}</span>
        <span class="lr-actions">
          <button class="lr-shell" title="Open a plain shell here"
            onclick="event.stopPropagation(); chela.launchProject(this.closest('.launch-row').dataset.path, {shell:true})">&#9003;</button>
          ${star}${forget}
        </span>
      </div>`;
}

function renderLauncher() {
    const host = document.getElementById('launcher-list');
    if (!host) return;
    const { recent, favorites } = _launcherData;
    if (!recent.length && !favorites.length) {
        host.innerHTML = '<div class="side-empty">No projects yet — add one with +</div>';
        return;
    }
    let html = '';
    if (favorites.length) {
        html += '<div class="launch-group-label">Favorites</div>';
        html += favorites.map(e => _launchRow(e, true)).join('');
    }
    if (recent.length) {
        html += '<div class="launch-group-label">Recent</div>';
        html += recent.map(e => _launchRow(e, false)).join('');
    }
    host.innerHTML = html;
}

// Compare two filesystem paths ignoring a trailing slash. The server normalises
// (realpath) both the stored launch path and the agent cwd, so a string compare
// is enough to dedup.
function _samePath(a, b) {
    if (!a || !b) return false;
    const strip = s => String(s).replace(/\/+$/, '');
    return strip(a) === strip(b);
}

// Launch `claude` (default) or a plain shell in `path`. Dedup: a claude launch
// into a dir that already has a live claude agent focuses that pane on the wall
// rather than spawning a duplicate. Plain-shell launches always spawn (you may
// want several shells in one repo).
async function launchProject(path, opts) {
    opts = opts || {};
    if (!opts.shell) {
        const existing = (_agentsCache || []).find(a => a.claude_running && _samePath(a.cwd, path));
        if (existing) {
            if (typeof focusPaneByWid === 'function') focusPaneByWid(existing.window_id);
            else if (typeof selectView === 'function') selectView('terminals');
            return;
        }
    }
    const body = { cwd: path };
    if (!opts.shell) body.command = 'claude';
    try {
        const res = await api('/api/agents/spawn', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res || !res.ok) { alert('Launch failed: ' + ((res && res.error) || 'unknown error')); return; }
        setAgentsCache([]);          // force /api/agents refetch so the new window shows
        selectView('terminals');    // surface the wall — the new pane spins up there
        refreshSidebar();
        refreshLauncher();          // the launch just bumped Recent
    } catch (e) {
        alert('Launch failed: ' + e);
    }
}

// Is `path` already a favorite? (Used to toggle the star on agent rows.)
function _isFav(path) {
    return (_launcherData.favorites || []).some(f => _samePath(f.path, path));
}

// Apply a launcher-mutation response: refresh the Launch section AND the agent
// rows (whose pin star reflects favorite state), so a pin from either surface
// updates both immediately rather than waiting for the next 30s tick.
function _applyLauncherResp(d) {
    if (!d || !d.ok) return;
    _launcherData = { recent: d.recent || [], favorites: d.favorites || [] };
    renderLauncher();
    if (typeof renderSidebarAgents === 'function') renderSidebarAgents(_agentsCache || []);
}

async function _launcherPost(route, path) {
    try {
        const d = await api(route, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path }),
        });
        _applyLauncherResp(d);
    } catch (e) { /* transient — leave the last render */ }
}

async function pinFav(path)      { return _launcherPost('/api/launcher/pin', path); }
async function unpinFav(path)    { return _launcherPost('/api/launcher/unpin', path); }
async function forgetRecent(path) { return _launcherPost('/api/launcher/forget', path); }

// Toggle a directory's favorite state — drives the star on Agents-list rows.
function toggleFavCwd(path) { return _isFav(path) ? unpinFav(path) : pinFav(path); }

// "Add a project to Favorites" picker: a popover of git-repo dirs under
// CHELA_PROJECTS_DIR that aren't pinned yet. Mirrors openNewMenu's anchoring +
// one-shot outside-click dismissal.
async function openFavAdd(ev) {
    if (ev) ev.stopPropagation();
    const m = document.getElementById('fav-add-menu');
    if (!m) return;
    m.innerHTML = '<div class="popover-item popover-note">Scanning…</div>';
    const anchor = (ev && ev.currentTarget) || document.getElementById('launcher-section');
    const r = anchor.getBoundingClientRect();
    m.style.top = (r.bottom + 4) + 'px';
    m.style.left = Math.max(8, r.left) + 'px';
    m.style.display = 'block';
    setTimeout(() => document.addEventListener('click', hideFavAdd, { once: true }), 0);
    let list = [];
    try { list = await api('/api/launcher/suggest') || []; } catch (e) { list = []; }
    const avail = list.filter(s => !s.pinned);
    if (!avail.length) {
        m.innerHTML = '<div class="popover-item popover-note">No projects to add'
            + ' <span class="popover-hint">(set CHELA_PROJECTS_DIR)</span></div>';
        return;
    }
    m.innerHTML = avail.map(s =>
        `<div class="popover-item" title="${attrEsc(s.path)}"
           onclick="chela.pinFav('${_jsStr(s.path)}'); chela.hideFavAdd()">${escHtml(s.label)}</div>`
    ).join('');
}

function hideFavAdd() {
    const m = document.getElementById('fav-add-menu');
    if (m) m.style.display = 'none';
}

// --- Stage 0: ES-module exports ---
export { _isFav, _launcherData, launchProject, refreshLauncher };

// --- Stage 0: window.chela — surface reachable from inline HTML handlers ---
window.chela = window.chela || {};
Object.assign(window.chela, { forgetRecent, hideFavAdd, launchProject, openFavAdd, pinFav, toggleFavCwd, unpinFav });
