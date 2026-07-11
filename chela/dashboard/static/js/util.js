/* chela dashboard — client-side JS */

// Inline Lucide icons (https://lucide.dev), vendored as SVG path strings — NO CDN,
// matching the topbar's inline SVGs and terminals.js _lockGlyph. One shared set so
// the command palette, share controls, and menus stay visually consistent. Stroke
// inherits currentColor; `lucideIcon(name, size)` returns an <svg> string.
const _LUCIDE = {
    'layout-grid': '<rect width="7" height="7" x="3" y="3" rx="1"/><rect width="7" height="7" x="14" y="3" rx="1"/><rect width="7" height="7" x="14" y="14" rx="1"/><rect width="7" height="7" x="3" y="14" rx="1"/>',
    'share-2': '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" x2="15.42" y1="13.51" y2="17.49"/><line x1="15.41" x2="8.59" y1="6.51" y2="10.49"/>',
    'x': '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    'play': '<polygon points="6 3 20 12 6 21 6 3"/>',
    'terminal': '<path d="m4 17 6-6-6-6"/><path d="M12 19h8"/>',
    'clock': '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    'more-vertical': '<circle cx="12" cy="12" r="1"/><circle cx="12" cy="5" r="1"/><circle cx="12" cy="19" r="1"/>',
    'bell': '<path d="M10.268 21a2 2 0 0 0 3.464 0"/><path d="M3.262 15.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673C19.41 13.956 18 12.499 18 8A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326"/>',
    'settings': '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
};
function lucideIcon(name, size = 16) {
    return `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" `
        + `stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${_LUCIDE[name] || ''}</svg>`;
}

const REFRESH_MS = 30000;
let currentTab = 'agents';
let msgTargetAgent = '';
let _agentsCache = [];  // cache for populating schedule dropdown

// Embedded terminals feature flag, bootstrapped inline by the template before
// this file loads (see index.html). When false the Terminals tab + panel are
// never emitted, so every terminals code path here must no-op cleanly: the
// terminals DOM (#term-stage etc.) does not exist. Defaults to true if the
// bootstrap is somehow absent, preserving prior behavior.
const TERMINALS_ON = window.TERMINALS_ENABLED !== false;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function $(sel) { return document.querySelector(sel); }
function $$(sel) { return document.querySelectorAll(sel); }

function shortTime(iso) {
    if (!iso) return '-';
    try {
        const d = new Date(iso);
        return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false })
            + ' ' + d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    } catch { return iso; }
}

function relativeTime(iso) {
    if (!iso) return '-';
    try {
        const diff = (new Date(iso) - Date.now()) / 1000;
        if (diff < 0) return 'overdue';
        if (diff < 60) return Math.round(diff) + 's';
        if (diff < 3600) return Math.round(diff / 60) + 'm';
        if (diff < 86400) return Math.round(diff / 3600) + 'h';
        return Math.round(diff / 86400) + 'd';
    } catch { return iso; }
}

function ageStr(seconds) {
    if (seconds == null) return 'never';
    if (seconds < 60) return Math.round(seconds) + 's ago';
    if (seconds < 3600) return Math.round(seconds / 60) + 'm ago';
    if (seconds < 86400) return Math.round(seconds / 3600) + 'h ago';
    return Math.round(seconds / 86400) + 'd ago';
}

function escHtml(s) {
    if (s == null) return '';
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

// escHtml is safe between tags but does NOT escape quote characters that
// would break out of an attribute value. attrEsc layers that on for any
// user-supplied string we splice into `attr="…"`.
function attrEsc(s) {
    return escHtml(s).replace(/"/g, '&quot;');
}

// Status dot colour shared by the sidebar list, the agent detail view, and the
// agents canvas, so one agent reads the same in every view as it does on the
// wall. Mirrors the wall's term-status-dot (terminals.js _colorTermDots): busy →
// green (working), waiting → yellow, idle or no Claude session → grey. These
// views previously coloured from the server `health` field, which collapses
// busy+idle+running all into green and so disagreed with the wall's grey "idle"
// dot for an agent that's running but not actively working.
function agentDotColor(a) {
    const st = a && a.session_status;
    if (st === 'busy') return 'green';
    if (st === 'waiting') return 'yellow';
    return 'grey';   // idle, or no Claude in the window — matches the wall
}

// --- Tab signal: surface "agents waiting on input" in the title + favicon ----
// Ambient at-a-glance for a backgrounded/pinned tab: when one or more Claude
// panes block on a prompt (session_status === 'waiting'), prefix the title with
// a count and paint a badged favicon so the tab itself shows it needs you — the
// same `waiting` signal the sidebar's "Needs you" triage and the daemon's
// push notification fire on. Idempotent and driven off the existing polls
// (renderSidebarAgents every 30s + SSE; _applyTermStatus every 4s on the wall),
// so it only touches the DOM / redraws the favicon when the count changes.
const _TAB_BASE_TITLE = document.title;   // captured before any prefix is applied
// The page ships a relative favicon href; capture it so we can restore the
// brand mark when nothing is waiting (and so a base-path deploy still resolves).
const _favLink0 = document.querySelector('link[rel~="icon"]');
const _FAVICON_DEFAULT = _favLink0 ? _favLink0.getAttribute('href') : 'static/img/favicon.svg';
let _tabWaiting = -1;   // last applied count; -1 forces the first paint

function updateTabSignal(agents) {
    const n = (agents || []).filter(a => a && a.session_status === 'waiting').length;
    if (n === _tabWaiting) return;   // unchanged → no title churn, no favicon redraw
    _tabWaiting = n;
    document.title = n > 0 ? `(${n}) Needs you · ${_TAB_BASE_TITLE}` : _TAB_BASE_TITLE;
    _drawFavicon(n);
}

function _faviconLink() {
    let l = document.querySelector('link[rel~="icon"]');
    if (!l) { l = document.createElement('link'); l.rel = 'icon'; document.head.appendChild(l); }
    return l;
}

// Badge the favicon with the waiting count. n === 0 restores the brand SVG;
// n > 0 paints an amber disc + count, amber pulled live from the active theme's
// --yellow so it tracks theme switches (falls back to the dark-theme value).
function _drawFavicon(n) {
    const link = _faviconLink();
    if (n <= 0) { link.type = 'image/svg+xml'; link.href = _FAVICON_DEFAULT; return; }
    const amber = (getComputedStyle(document.body).getPropertyValue('--yellow') || '').trim() || '#d29922';
    const size = 32;
    const cv = document.createElement('canvas');
    cv.width = cv.height = size;
    const c = cv.getContext('2d');
    c.beginPath();
    c.arc(size / 2, size / 2, size / 2, 0, 2 * Math.PI);
    c.fillStyle = amber;
    c.fill();
    const label = n > 9 ? '9+' : String(n);
    c.fillStyle = '#1b1f24';   // dark ink — same as the favicon's light-scheme color
    c.font = `bold ${label.length > 1 ? 17 : 22}px system-ui, -apple-system, sans-serif`;
    c.textAlign = 'center';
    c.textBaseline = 'middle';
    c.fillText(label, size / 2, size / 2 + 1);
    link.type = 'image/png';
    link.href = cv.toDataURL('image/png');
}

const BASE_PATH = window.location.pathname.replace(/\/$/, '');
async function api(path, opts) {
    const res = await fetch(BASE_PATH + path, opts);
    return res.json();
}

// Modal show/hide — the .modal-overlay shows on `.active` (see style.css). The
// Add-Schedule and Send-Message modals rely on these; the sidebar refactor
// (4a71b9e) dropped the definitions while leaving every call site, so without
// them those modals were dead on click.
function showModal(id) { const el = $('#' + id); if (el) el.classList.add('active'); }
function closeModal(id) { const el = $('#' + id); if (el) el.classList.remove('active'); }

function toggleMenu(btn) {
    const menu = btn.nextElementSibling;
    const isOpen = menu.classList.contains('open');
    // Close all open menus first
    $$('.kebab-menu.open').forEach(m => m.classList.remove('open'));
    if (!isOpen) menu.classList.add('open');
}

document.addEventListener('click', e => {
    if (!e.target.closest('.kebab-wrap')) {
        $$('.kebab-menu.open').forEach(m => m.classList.remove('open'));
    }
});

function humanSchedule(type, value) {
    if (type === 'interval') return 'every ' + value;
    if (type === 'once') return 'once';
    if (type !== 'cron') return type + ' ' + value;
    const parts = value.split(/\s+/);
    if (parts.length !== 5) return 'cron ' + value;
    const [min, hr, dom, mon, dow] = parts;
    if (/^\*\/\d+$/.test(min) && hr === '*') return 'every ' + min.slice(2) + 'm';
    if (/^\d+$/.test(min) && hr === '*') return 'every 1h at :' + min.padStart(2, '0');
    if (/^\d+$/.test(min) && /^\*\/\d+$/.test(hr)) return 'every ' + hr.slice(2) + 'h at :' + min.padStart(2, '0');
    if (/^\d+$/.test(min) && /^\d+$/.test(hr)) return 'daily at ' + hr.padStart(2, '0') + ':' + min.padStart(2, '0');
    // N H1,H2,H3 * * * → 3x daily at HH:MM, HH:MM, HH:MM
    if (/^\d+$/.test(min) && /^[\d,]+$/.test(hr) && dom === '*' && mon === '*' && dow === '*') {
        const hrs = hr.split(',').map(Number).sort((a, b) => a - b);
        const mm = min.padStart(2, '0');
        if (hrs.length >= 2) {
            const interval = hrs[1] - hrs[0];
            if (hrs.every((h, i) => i === 0 || h - hrs[i-1] === interval))
                return 'every ' + interval + 'h at :' + mm;
        }
        return hrs.length + 'x daily at ' + hrs.map(h => String(h).padStart(2, '0') + ':' + mm).join(', ');
    }
    if (/^[\d,]+$/.test(min) && hr === '*') {
        const mins = min.split(',').map(Number).sort((a, b) => a - b);
        if (mins.length >= 2) {
            const interval = mins[1] - mins[0];
            if (mins.every((m, i) => i === 0 || m - mins[i-1] === interval))
                return 'every ' + interval + 'm';
        }
        return 'at :' + mins.map(m => String(m).padStart(2, '0')).join(',:');
    }
    return 'cron ' + value;
}


// --- Stage 0: setters for cross-module mutable state (imported bindings are read-only) ---
function setCurrentTab(v) { currentTab = v; }
function setMsgTarget(v) { msgTargetAgent = v; }
function setAgentsCache(v) { _agentsCache = v; }

// --- Stage 0: ES-module exports ---
export { $, $$, BASE_PATH, REFRESH_MS, TERMINALS_ON, _agentsCache, ageStr, agentDotColor, api, attrEsc, closeModal, currentTab, escHtml, humanSchedule, lucideIcon, msgTargetAgent, relativeTime, setAgentsCache, setCurrentTab, setMsgTarget, shortTime, showModal, updateTabSignal };

// --- Stage 0: window.chela — surface reachable from inline HTML handlers ---
window.chela = window.chela || {};
Object.assign(window.chela, { closeModal, toggleMenu });
