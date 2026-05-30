// ---------------------------------------------------------------------------
// Refresh loop
// ---------------------------------------------------------------------------

async function refresh() {
    try {
        await refreshSummary();
        if (currentTab === 'agents') await refreshAgents();
        else if (currentTab === 'schedules') await refreshSchedules();
        else if (currentTab === 'terminals') await renderTerminals();
        // Dispatcher and Kanban tabs own their own polling timers; the global
        // refresh just updates the header summary while those tabs are active.
        // checkContext drives both the agent-card bars and the header 5h-RL
        // pill, so it runs on every tab.
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
