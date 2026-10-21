/* chela dashboard — client-side JS */

import { ICONS } from './icons';
// CMX-217: api/BASE_PATH/staticUrl/ASSET_VERSION are TypeScript now; re-exported below so
// their importers need no edit.
import { ASSET_VERSION, BASE_PATH, BOOT, api, staticUrl } from '../../frontend/src/shared/api';
import { onEvent } from '../../frontend/src/shared/events';
import { relativeTime, shortTime } from '../../frontend/src/shared/format';
import { agents as agentsStore } from '../../frontend/src/shared/store/agents';
import { view as viewStore } from '../../frontend/src/shared/store/view';

// Inline Lucide icons (https://lucide.dev), vendored as SVG path strings — NO CDN,
// matching the topbar's inline SVGs and terminals.js _lockGlyph. One shared set so
// the command palette, share controls, and menus stay visually consistent. Stroke
// inherits currentColor; `lucideIcon(name, size)` returns an <svg> string.
const _LUCIDE = {
    'layout-grid': '<rect width="7" height="7" x="3" y="3" rx="1"/><rect width="7" height="7" x="14" y="3" rx="1"/><rect width="7" height="7" x="14" y="14" rx="1"/><rect width="7" height="7" x="3" y="14" rx="1"/>',
    'share-2': '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" x2="15.42" y1="13.51" y2="17.49"/><line x1="15.41" x2="8.59" y1="6.51" y2="10.49"/>',
    'x': '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    'external-link': '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
    'play': '<polygon points="6 3 20 12 6 21 6 3"/>',
    'rss': '<path d="M4 11a9 9 0 0 1 9 9"/><path d="M4 4a16 16 0 0 1 16 16"/><circle cx="5" cy="19" r="1"/>',
    'columns-3': '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M9 3v18"/><path d="M15 3v18"/>',
    'book-open': '<path d="M12 7v14"/><path d="M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z"/>',
    'bot': '<path d="M12 8V4H8"/><rect width="16" height="12" x="4" y="8" rx="2"/><path d="M2 14h2"/><path d="M20 14h2"/><path d="M15 13v2"/><path d="M9 13v2"/>',
    'terminal': '<path d="m4 17 6-6-6-6"/><path d="M12 19h8"/>',
    'clock': '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    'more-vertical': '<circle cx="12" cy="12" r="1"/><circle cx="12" cy="5" r="1"/><circle cx="12" cy="19" r="1"/>',
    'bell': '<path d="M10.268 21a2 2 0 0 0 3.464 0"/><path d="M3.262 15.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673C19.41 13.956 18 12.499 18 8A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326"/>',
    'settings': '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
    // `drama` — the comedy/tragedy theatre masks: the Personas view (the persona layer).
    'drama': '<path d="M10 11h.01"/><path d="M14 6h.01"/><path d="M18 6h.01"/><path d="M6.5 13.1h.01"/><path d="M22 5c0 9-4 12-6 12s-6-3-6-12c0-2 2-3 6-3s6 1 6 3"/><path d="M17.4 9.9c-.8.8-2 .8-2.8 0"/><path d="M10.1 7.1C9 7.2 7.7 7.7 6 8.6c-3.5 2-4.7 3.9-3.7 5.6 4.5 7.8 9.5 8.4 11.2 7.4.9-.5 1.9-2.1 1.9-4.7"/><path d="M9.1 16.5c.3-1.1 1.4-1.7 2.4-1.4"/>',
    // `dollar-sign` — the Cost view (fleet spend, from the cost chela already ingests).
    'dollar-sign': '<line x1="12" x2="12" y1="1" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>',
    // `pin` — the per-pane layout pin toggle (terminals.js _pinBtnHTML), replacing
    // the old pushpin emoji so it matches the rest of the pane header's icon set.
    'pin': '<path d="M12 17v5"/><path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"/>',
    // `link-2` — the per-pane "Wire to…" row (terminals.js paneHead's `port`
    // button), replacing the bare PORT_GLYPH circle now that the overflow menu
    // is a labeled row list, not an icon-only strip (CMX-114).
    'link-2': '<path d="M9 17H7A5 5 0 0 1 7 7h2"/><path d="M15 7h2a5 5 0 1 1 0 10h-2"/><line x1="8" x2="16" y1="12" y2="12"/>',
    // `circle-dot` — the per-pane "Orchestrator" row (terminals.js _orchBtnHTML),
    // replacing the bare "⊙" text glyph for the same reason (CMX-114).
    'circle-dot': '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="1"/>',
    // `keyboard` — the command palette's "Keyboard shortcuts" row (nav.js
    // _paletteItems), opening the shortcuts cheatsheet overlay (CMX-121).
    'keyboard': '<rect width="20" height="16" x="2" y="4" rx="2"/><path d="M6 8h.01M10 8h.01M14 8h.01M18 8h.01M8 12h.01M12 12h.01M16 12h.01M7 16h10"/>',
    // `maximize-2` / `minimize-2` — the pane header's maximize/restore toggle
    // (terminals.js termMaxFor) and the min-dock chip's restore icon, replacing
    // the 🗖/🗗 window-chrome emoji (U+1F5D6/U+1F5D7): a rare Unicode block most
    // fonts, including macOS defaults, don't cover — they rendered as tofu (CMX-154).
    'maximize-2': '<polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" x2="14" y1="3" y2="10"/><line x1="3" x2="10" y1="21" y2="14"/>',
    'minimize-2': '<polyline points="4 14 10 14 10 20"/><polyline points="20 10 14 10 14 4"/><line x1="14" x2="21" y1="10" y2="3"/><line x1="3" x2="10" y1="21" y2="14"/>',
    // `minus` — the pane header's "minimize to dock" button and the min-dock
    // chip's minimize icon, replacing the 🗕 window-chrome emoji (U+1F5D5) for
    // the same tofu reason as maximize-2/minimize-2 above (CMX-154).
    'minus': '<path d="M5 12h14"/>',
    // `crown` — the sidebar's orchestrator role badge (nav.js _agentRowHtml,
    // CMX-302), replacing the old "Orchestrator" text pill: that word was wide
    // enough to truncate the session name sitting next to it on a real row.
    'crown': '<path d="M11.562 3.266a.5.5 0 0 1 .876 0L15.39 8.87a1 1 0 0 0 1.516.294L21.183 5.5a.5.5 0 0 1 .798.519l-2.834 10.246a1 1 0 0 1-.956.734H5.81a1 1 0 0 1-.957-.734L2.02 6.02a.5.5 0 0 1 .798-.519l4.276 3.664a1 1 0 0 0 1.516-.294z"/><path d="M5 21h14"/>',
    // `git-compare` — the wall tile bottom bar's "Files" chip (terminals.js
    // _ctxBarHTML), opening the per-session changed-files/diff modal
    // (frontend/src/features/diffmodal, CMX-299).
    'git-compare': '<circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M13 6h3a2 2 0 0 1 2 2v7"/><path d="M11 18H8a2 2 0 0 1-2-2V9"/>',
    // `chevron-down` — the Wall's collapsible layout toolbar (terminals.js
    // _reflectGridRow, CMX-397); rotated a half-turn while the row is open.
    'chevron-down': '<path d="m6 9 6 6 6-6"/>',
    // `chevron-right` / `ellipsis` — a sidebar folder group's collapse chevron and its
    // ⋯ group-menu button (nav.js _groupHtml, CMX-35).
    'chevron-right': '<path d="m9 18 6-6-6-6"/>',
    'ellipsis': '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
    // `plus` — the sidebar's "New session" button (CMX-377, index.html).
    'plus': '<path d="M5 12h14"/><path d="M12 5v14"/>',
    // `search` — the sidebar's jump-to-session input (CMX-377, index.html).
    'search': '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    // The icons behind ICONS (icons.js, CMX-199): one per meaning a dashboard emoji
    // used to carry. Path data from lucide 0.574.0 (ISC), copied verbatim.
    'scale': '<path d="M12 3v18"/><path d="m19 8 3 8a5 5 0 0 1-6 0zV7"/><path d="M3 7h1a17 17 0 0 0 8-2 17 17 0 0 0 8 2h1"/><path d="m5 8 3 8a5 5 0 0 1-6 0zV7"/><path d="M7 21h10"/>',
    'triangle-alert': '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
    'check': '<path d="M20 6 9 17l-5-5"/>',
    'ban': '<circle cx="12" cy="12" r="10"/><path d="M4.929 4.929 19.07 19.071"/>',
    'octagon-x': '<path d="m15 9-6 6"/><path d="M2.586 16.726A2 2 0 0 1 2 15.312V8.688a2 2 0 0 1 .586-1.414l4.688-4.688A2 2 0 0 1 8.688 2h6.624a2 2 0 0 1 1.414.586l4.688 4.688A2 2 0 0 1 22 8.688v6.624a2 2 0 0 1-.586 1.414l-4.688 4.688a2 2 0 0 1-1.414.586H8.688a2 2 0 0 1-1.414-.586z"/><path d="m9 9 6 6"/>',
    'hand': '<path d="M18 11V6a2 2 0 0 0-2-2a2 2 0 0 0-2 2"/><path d="M14 10V4a2 2 0 0 0-2-2a2 2 0 0 0-2 2v2"/><path d="M10 10.5V6a2 2 0 0 0-2-2a2 2 0 0 0-2 2v8"/><path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15"/>',
    'eye': '<path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0"/><circle cx="12" cy="12" r="3"/>',
    'eye-off': '<path d="M10.733 5.076a10.744 10.744 0 0 1 11.205 6.575 1 1 0 0 1 0 .696 10.747 10.747 0 0 1-1.444 2.49"/><path d="M14.084 14.158a3 3 0 0 1-4.242-4.242"/><path d="M17.479 17.499a10.75 10.75 0 0 1-15.417-5.151 1 1 0 0 1 0-.696 10.75 10.75 0 0 1 4.446-5.143"/><path d="m2 2 20 20"/>',
    'repeat': '<path d="m17 2 4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/>',
    'plug': '<path d="M12 22v-5"/><path d="M15 8V2"/><path d="M17 8a1 1 0 0 1 1 1v4a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V9a1 1 0 0 1 1-1z"/><path d="M9 8V2"/>',
    'lock': '<rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    'server': '<rect width="20" height="8" x="2" y="2" rx="2" ry="2"/><rect width="20" height="8" x="2" y="14" rx="2" ry="2"/><line x1="6" x2="6.01" y1="6" y2="6"/><line x1="6" x2="6.01" y1="18" y2="18"/>',
    'globe': '<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><path d="M2 12h20"/>',
    'git-branch': '<path d="M15 6a9 9 0 0 0-9 9V3"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/>',
    'flag': '<path d="M4 22V4a1 1 0 0 1 .4-.8A6 6 0 0 1 8 2c3 0 5 2 7.333 2q2 0 3.067-.8A1 1 0 0 1 20 4v10a1 1 0 0 1-.4.8A6 6 0 0 1 16 16c-3 0-5-2-8-2a6 6 0 0 0-4 1.528"/>',
    'snowflake': '<path d="m10 20-1.25-2.5L6 18"/><path d="M10 4 8.75 6.5 6 6"/><path d="m14 20 1.25-2.5L18 18"/><path d="m14 4 1.25 2.5L18 6"/><path d="m17 21-3-6h-4"/><path d="m17 3-3 6 1.5 3"/><path d="M2 12h6.5L10 9"/><path d="m20 10-1.5 2 1.5 2"/><path d="M22 12h-6.5L14 15"/><path d="m4 10 1.5 2L4 14"/><path d="m7 21 3-6-1.5-3"/><path d="m7 3 3 6h4"/>',
    'notebook-pen': '<path d="M13.4 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-7.4"/><path d="M2 6h4"/><path d="M2 10h4"/><path d="M2 14h4"/><path d="M2 18h4"/><path d="M21.378 5.626a1 1 0 1 0-3.004-3.004l-5.01 5.012a2 2 0 0 0-.506.854l-.837 2.87a.5.5 0 0 0 .62.62l2.87-.837a2 2 0 0 0 .854-.506z"/>',
    'square-check': '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="m9 12 2 2 4-4"/>',
    'circle-check': '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>',
};
function lucideIcon(name, size = 16) {
    if (!(name in _LUCIDE)) throw new Error(`lucideIcon: unknown icon "${name}" (not in _LUCIDE)`);
    return `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" `
        + `stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${_LUCIDE[name]}</svg>`;
}
// `icon(meaning)` draws a MEANING from icons.js ('judge'), not a raw Lucide name.
function icon(meaning, size = 16) {
    if (!Object.hasOwn(ICONS, meaning)) throw new Error(`icon: unknown meaning "${meaning}" (not in ICONS)`);
    return lucideIcon(ICONS[meaning], size);
}

const REFRESH_MS = 30000;
// CMX-217: a facade over the page's store (frontend/src/shared/store), kept so the modules
// reading these live bindings need no edit: the store is the truth, these mirror it. The view
// is 'work' until one is selected (boot.ts selects the Wall when TERMINALS_ON).
let currentTab = viewStore.get();
viewStore.subscribe(() => { currentTab = viewStore.get(); });
let msgTargetAgent = '';
let _agentsCache = agentsStore.get();
agentsStore.subscribe(() => { _agentsCache = agentsStore.get(); });
// The window set changed (an agent window appeared or vanished): any render refetches it.
onEvent('windows', () => setAgentsCache([]));

// The boot config (index.html's #chela-boot block). With terminals off the template draws no
// Wall, so every terminals code path must no-op cleanly: #term-stage etc. do not exist.
const TERMINALS_ON = BOOT.terminalsEnabled;
// CHELA_WALL_TILE_DISPATCHED: true = a dispatched worker gets a full tile on spawn, like a
// human's session. Default false = it opens minimized and pops out when it blocks.
const WALL_TILE_DISPATCHED = BOOT.wallTileDispatched;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// THE fleet-wide "this agent is blocked on YOU" predicate. Every attention surface
// reads it — the wall's pane dot, the dock chip, the tab title/favicon, the sidebar's
// "Needs you" cluster — so a gated agent can never be amber on one and calm on another.
//
// It is two sources OR'd, because `session_status` alone cannot see a **permission
// gate** on a Bash/Edit: that prompt is never in the transcript, only on the pane, and
// the server probes for it (`needs_human` from /api/agents — see app.py::_needs_human).
// `session_status === 'waiting'` stays in the predicate because it is free and it is
// the answer for every window the server does not pane-probe.
function wantsHuman(a) {
    return !!a && (a.needs_human === true || a.session_status === 'waiting');
}

function $(sel) { return document.querySelector(sel); }
function $$(sel) { return document.querySelectorAll(sel); }

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
    if (wantsHuman(a)) return 'yellow';   // blocked on you — incl. a pane-only permission gate
    if (a && a.session_status === 'busy') return 'green';
    return 'grey';   // idle, or no Claude in the window — matches the wall
}

// Project key for grouping = basename of the session's cwd. Shells / sessions
// with no resolved cwd have no project (callers pick their own "unknown" bucket
// label). Used by the sidebar's project groups (nav.js).
function _agentProject(a) {
    if (!a || !a.cwd) return null;
    const parts = String(a.cwd).replace(/\/+$/, '').split('/');
    return parts[parts.length - 1] || null;
}

// --- Tab signal: surface "agents waiting on input" in the title + favicon ----
// Ambient at-a-glance for a backgrounded/pinned tab: when one or more Claude
// panes block on a prompt (wantsHuman), prefix the title with a count and paint
// a badged favicon so the tab itself shows it needs you — the same signal the
// sidebar's "Needs you" triage, the wall's amber pane, and the daemon's push
// notification fire on. Idempotent and driven off the existing polls
// (renderSidebarAgents every 30s + SSE; _applyTermStatus every 4s on the wall),
// so it only touches the DOM / redraws the favicon when the count changes.
const _TAB_BASE_TITLE = document.title;   // captured before any prefix is applied
// The page ships a relative favicon href; capture it so we can restore the
// brand mark when nothing is waiting (and so a base-path deploy still resolves).
const _favLink0 = document.querySelector('link[rel~="icon"]');
const _FAVICON_DEFAULT = _favLink0 ? _favLink0.getAttribute('href') : 'static/img/favicon.svg';
let _tabWaiting = -1;   // last applied count; -1 forces the first paint

function updateTabSignal(agents) {
    const n = (agents || []).filter(wantsHuman).length;
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



// --- Stage 0: setters for cross-module mutable state (imported bindings are read-only) ---
function setCurrentTab(v) { viewStore.set(v); }
function setMsgTarget(v) { msgTargetAgent = v; }
// The agents store is typed Agent[]: a failed fetch's non-array body caches [].
function setAgentsCache(v) { agentsStore.set(Array.isArray(v) ? v : []); }

// CMX-178: hand a value legacy still owns to the React islands' store of that name
// (frontend/src/shared/bridge; docs/FRONTEND.md). No bridge (the build did not load) is a no-op.
function publish(name, value) {
    const store = window.chelaBridge && window.chelaBridge[name];
    if (store) store.set(value);
}

// --- Stage 0: ES-module exports ---
export { $, $$, ASSET_VERSION, BASE_PATH, REFRESH_MS, TERMINALS_ON, WALL_TILE_DISPATCHED, _agentProject, _agentsCache, ageStr, agentDotColor, api, attrEsc, closeModal, currentTab, escHtml, icon, lucideIcon, msgTargetAgent, publish, relativeTime, setAgentsCache, setCurrentTab, setMsgTarget, shortTime, showModal, staticUrl, updateTabSignal, wantsHuman };

// --- Stage 0: window.chela — surface reachable from inline HTML handlers ---
window.chela = window.chela || {};
Object.assign(window.chela, { closeModal, toggleMenu });
