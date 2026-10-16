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

// CMX-166: the first refresh() IS the boot (once: from selectView, or below when
// terminals are off) and seeds every view-independent read; there is no second pass.
let _booted = false;

async function refresh() {
    const boot = !_booted;
    _booted = true;
    // Independent lanes, not a chain (a throw no longer skips the rest), and ONE
    // /api/agents/context read for its three readers (sidebar, wall tiles, header pills).
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

// Wall is the flagship default canvas when terminals are enabled (selectView refreshes).
if (typeof TERMINALS_ON !== 'undefined' && TERMINALS_ON) selectView('terminals');
else refresh();
setInterval(refresh, REFRESH_MS);
// The ONE /api/dispatcher poll (board + runs + the always-visible sidebar badges).
// App-level, not view-level: the badges are on screen from every view.
startWorkPoll();
initSSE();

// --- Stage 0: ES-module exports ---
export { refresh };
