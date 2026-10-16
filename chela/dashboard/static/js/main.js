// --- Stage 0: ES-module imports ---
import { REFRESH_MS, TERMINALS_ON, api, currentTab } from './util.js';
import { refreshSummary } from './header.js';
import { checkContext } from './agents.js';
import { refreshSidebar, renderNav, selectView } from './nav.js';
import { refreshLauncher } from './launcher.js';
import { startWorkPoll } from './work.js';
import './newtask.js';
import { VIEWS } from './views.js';
import { findView } from './viewreg.js';
import { initSSE } from './sse.js';
import { tickDecisions } from './decisions.js';
import { tickResources } from './resources.js';
import { tickStatusHealth } from './statushealth.js';
import { checkForUpdate } from './version.js';
import { tickAccessRequests } from './accessreq.js';
import { tickApprovals } from './approvals.js';

// ---------------------------------------------------------------------------
// Refresh loop
//
// This used to carry a SECOND per-view if/else chain (the first being
// selectView's timers). It now asks the registry what the active view's tick is
// — so a view is declared once, in views.js, and nothing here changes when one
// is added or removed.
// ---------------------------------------------------------------------------

// CMX-166: the first refresh() IS the boot. It runs once, from selectView('terminals')
// or (terminals off) from the bottom of this file, and seeds what used to be seeded by a
// second pass of enter*() calls. Measured on a 7-tile wall: that double boot fetched
// nearly every endpoint twice, and the wall's iframes waited ~1.3 s behind it.
let _booted = false;

async function refresh() {
    const boot = !_booted;
    _booted = true;
    // Independent lanes, not a chain: one read waiting on another it does not use is
    // what kept the wall's terminals waiting behind /api/restore, overrides and the
    // launcher. A lane that throws no longer skips the lanes after it.
    // ONE /api/agents/context read per tick, handed to its three readers (sidebar rows,
    // wall tiles, header pills); each fetched its own before.
    const ctxRead = TERMINALS_ON ? api('/api/agents/context') : null;
    let paneListKnown;
    const paneList = new Promise(resolve => { paneListKnown = resolve; });
    const view = findView(VIEWS, currentTab);
    // The active view's tick starts as soon as /api/agents is cached (the wall needs
    // the pane list, nothing else), not after every other read below.
    const viewTick = paneList.then(() => (view && view.tick ? view.tick(ctxRead) : null));
    const lanes = [
        viewTick,
        refreshSummary(),
        // The sidebar (agent list + WORK badges + the Decisions section) is always
        // visible, so it refreshes on every tick regardless of the active view.
        refreshSidebar(paneListKnown, ctxRead).finally(paneListKnown),
        // Decisions is the fallback poll under its own SSE `log`/`orchestrator` deltas
        // (sse.js). Its first tick IS the seed: the cursor starts empty.
        tickDecisions(),
        // Header resources strip and native status feed health marker: plain polls.
        tickResources(),
        tickStatusHealth(),
        // 🙋 Sandboxed guests' access requests (CMX-7) and ⚖️ pending approvals
        // (CMX-103): pills shown on every tab.
        tickAccessRequests(),
        tickApprovals(),
        refreshLauncher(),
    ];
    // CMX-426: a deploy since this page loaded? Offer a reload (never forces one). On
    // boot it waits for the active view's first paint: /api/update/status is the
    // slowest boot read (~0.8 s) and only contends with the wall for the server.
    if (boot) viewTick.catch(() => {}).then(() => checkForUpdate().catch(() => {}));
    else checkForUpdate().catch(() => {});
    const results = await Promise.allSettled(lanes);
    for (const r of results) if (r.status === 'rejected') console.error('Refresh error:', r.reason);
    // checkContext drives both the agent-card bars and the header 5h-RL pill, so it
    // runs on every view.
    checkContext(ctxRead).catch(e => console.error('Refresh error:', e));
}

renderNav();   // the sidebar's view list, from the registry — before anything selects one

// Wall is the flagship default canvas when terminals are enabled. selectView runs the
// boot refresh() itself; it must not run twice (CMX-166).
if (typeof TERMINALS_ON !== 'undefined' && TERMINALS_ON) selectView('terminals');
else refresh();
setInterval(refresh, REFRESH_MS);
// The ONE /api/dispatcher poll (board + runs + the always-visible sidebar badges).
// App-level, not view-level: the badges are on screen from every view.
startWorkPoll();
initSSE();
// The pane-title toggle's orchestrator owner, the sidebar Decisions section, the header
// resources strip and the status feed health marker are all seeded by the boot
// refresh() above, in parallel lanes: a separate enter*() call here fetched each twice.

// --- Stage 0: ES-module exports ---
export { refresh };
