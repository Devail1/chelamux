// --- Stage 0: ES-module imports ---
import { REFRESH_MS, TERMINALS_ON, currentTab } from './util.js';
import { refreshSummary } from './header.js';
import { checkContext, refreshAgents } from './agents.js';
import { refreshSchedules } from './schedules.js';
import { refreshKnowledge } from './knowledge.js';
import { renderTerminals } from './terminals.js';
import { refreshSidebar, renderAgentDetail, selectView, updateWorkBadges } from './nav.js';
import { refreshLauncher } from './launcher.js';
import { initSSE } from './sse.js';

// ---------------------------------------------------------------------------
// Refresh loop
// ---------------------------------------------------------------------------

async function refresh() {
    try {
        await refreshSummary();
        // The sidebar (agent list + WORK badges) is always visible, so refresh
        // it on every tick regardless of which canvas view is active.
        await refreshSidebar();
        updateWorkBadges();
        if (typeof refreshLauncher === 'function') refreshLauncher();
        if (currentTab === 'agents') await refreshAgents();
        else if (currentTab === 'schedules') await refreshSchedules();
        else if (currentTab === 'knowledge') await refreshKnowledge();
        else if (currentTab === 'agent-detail') renderAgentDetail();
        else if (currentTab === 'terminals') await renderTerminals();
        // Dispatcher and Kanban views own their own polling timers; the global
        // refresh just updates the summary/sidebar while those views are active.
        // checkContext drives both the agent-card bars and the header 5h-RL
        // pill, so it runs on every view.
        checkContext();
    } catch (e) {
        console.error('Refresh error:', e);
    }
}

// Wall is the flagship default canvas when terminals are enabled.
if (typeof TERMINALS_ON !== 'undefined' && TERMINALS_ON) selectView('terminals');

refresh().then(() => {
    if (currentTab === 'agents') checkContext();
});
setInterval(refresh, REFRESH_MS);
initSSE();

// --- Stage 0: ES-module exports ---
export { refresh };
