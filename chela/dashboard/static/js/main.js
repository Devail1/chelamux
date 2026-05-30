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
        if (currentTab === 'agents') await refreshAgents();
        else if (currentTab === 'schedules') await refreshSchedules();
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

refresh().then(() => {
    if (currentTab === 'agents') checkContext();
});
setInterval(refresh, REFRESH_MS);
initSSE();
