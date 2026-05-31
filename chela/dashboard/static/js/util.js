/* chela dashboard — client-side JS */

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

