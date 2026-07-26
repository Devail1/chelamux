// --- Stage 0: ES-module imports ---
import { REFRESH_MS, TERMINALS_ON, currentTab } from './util.js';
import { refreshSummary } from './header.js';
import { checkContext } from './agents.js';
import { refreshSidebar, renderNav, selectView } from './nav.js';
import { refreshLauncher } from './launcher.js';
import { startWorkPoll } from './work.js';
import { VIEWS } from './views.js';
import { findView } from './viewreg.js';
import { initSSE } from './sse.js';
import { refreshOrchestratorStatus } from './orchestrator.js';
import { enterDecisions, tickDecisions } from './decisions.js';
import { enterResources, tickResources } from './resources.js';
import { enterStatusHealth, tickStatusHealth } from './statushealth.js';

// ---------------------------------------------------------------------------
// Refresh loop
//
// This used to carry a SECOND per-view if/else chain (the first being
// selectView's timers). It now asks the registry what the active view's tick is
// — so a view is declared once, in views.js, and nothing here changes when one
// is added or removed.
// ---------------------------------------------------------------------------

async function refresh() {
    try {
        await refreshSummary();
        // The sidebar (agent list + WORK badges + the Decisions section) is always
        // visible, so refresh it on every tick regardless of which canvas view is
        // active. The badges ride work.js's single /api/dispatcher poll, started
        // below; Decisions is the fallback poll under its own SSE `log`/
        // `orchestrator` deltas (sse.js — no longer tab-gated, see decisions.js).
        await refreshSidebar();
        await tickDecisions();
        // Header resources strip — plain poll, no SSE delta (there is nothing
        // event-driven to push here, unlike Decisions).
        await tickResources();
        // Native status feed health marker — same reasoning: no push side, a plain poll.
        await tickStatusHealth();
        if (typeof refreshLauncher === 'function') refreshLauncher();
        const view = findView(VIEWS, currentTab);
        if (view && view.tick) await view.tick();
        // checkContext drives both the agent-card bars and the header 5h-RL
        // pill, so it runs on every view.
        checkContext();
    } catch (e) {
        console.error('Refresh error:', e);
    }
}

renderNav();   // the sidebar's view list, from the registry — before anything selects one

// Wall is the flagship default canvas when terminals are enabled.
if (typeof TERMINALS_ON !== 'undefined' && TERMINALS_ON) selectView('terminals');

refresh().then(() => {
    if (currentTab === 'agents') checkContext();
});
setInterval(refresh, REFRESH_MS);
// The ONE /api/dispatcher poll (board + runs + the always-visible sidebar badges).
// App-level, not view-level: the badges are on screen from every view.
startWorkPoll();
initSSE();
// Seeds the pane-title toggle (terminals.js) with who owns the decisions inbox
// right now — without this, every button reads "off" until a takeover fires
// the SSE `orchestrator` delta.
refreshOrchestratorStatus();
// Seeds the sidebar Decisions section (chip + rows) on load — it is always
// visible now (cmx-107), not gated behind opening the Personas tab, so this is
// the ONLY thing that paints it before the first SSE `log`/`orchestrator` delta
// or the next refresh() tick.
enterDecisions();
// Seeds the header resources strip (CPU/RAM/Disk) on load, same reasoning as
// enterDecisions() above — otherwise it stays blank until the first refresh().
enterResources();
// Seeds the native status feed health marker on load — otherwise a fleet that is
// ALREADY down on page load stays unmarked until the first refresh() tick.
enterStatusHealth();

// --- Stage 0: ES-module exports ---
export { refresh };
