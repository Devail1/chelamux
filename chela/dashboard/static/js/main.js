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
        // The sidebar (agent list + WORK badges) is always visible, so refresh
        // it on every tick regardless of which canvas view is active. The badges
        // ride work.js's single /api/dispatcher poll, started below.
        await refreshSidebar();
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
// right now — without this, every button reads "off" until the Personas tab
// (whose enter() does the same fetch) is first opened or a takeover fires the
// SSE `orchestrator` delta.
refreshOrchestratorStatus();

// --- Stage 0: ES-module exports ---
export { refresh };
