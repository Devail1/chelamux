// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------

$$('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
        $$('.tab').forEach(t => t.classList.remove('active'));
        $$('.panel').forEach(p => p.classList.remove('active'));
        tab.classList.add('active');
        currentTab = tab.dataset.tab;
        $('#panel-' + currentTab).classList.add('active');
        // Terminals and Kanban run full-bleed on desktop (escape the content
        // cap); other tabs stay capped so tables/cards don't stretch on
        // ultrawide. Kanban needs the width because 7 columns at the capped
        // width force aggressive text wrapping inside each card.
        document.querySelector('.content').classList.toggle('full-bleed', currentTab === 'terminals' || currentTab === 'kanban');
        // Dispatcher / Kanban own their own poll timers — start them on tab
        // entry and tear them down on exit so we aren't fetching when invisible.
        if (currentTab === 'dispatcher') { refreshDispatcher(); startDispatcherTimer(); }
        else { stopDispatcherTimer(); }
        if (currentTab === 'kanban') { refreshKanban(); startKanbanTimer(); }
        else { stopKanbanTimer(); }
        // Terminals tab runs a fast reactive poll to drop dead panes promptly.
        // Guarded by TERMINALS_ON so a terminals-disabled build (terminals.js
        // not loaded) never calls into the missing wall module.
        if (TERMINALS_ON && currentTab === 'terminals') startTermTimer();
        else if (TERMINALS_ON) stopTermTimer();
        refresh();
    });
});

// ---------------------------------------------------------------------------
// Modals
// ---------------------------------------------------------------------------

function showModal(id) { $('#' + id).classList.add('active'); }
function closeModal(id) { $('#' + id).classList.remove('active'); }

$$('.modal-overlay').forEach(el => {
    el.addEventListener('click', e => {
        if (e.target === el) el.classList.remove('active');
    });
});

