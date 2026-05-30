// ---------------------------------------------------------------------------
// Render: Kanban (global cross-workflow board)
//
// Reuses /api/dispatcher and flattens its per-workflow payload into a single
// board: Open (open_tasks), Claimed / Running / Failed / Done (runs). The
// workflow chip + filter chips let one board surface state across every
// configured workflow. Self-polls every KANBAN_REFRESH_MS, same pattern as
// the Dispatcher tab.
//
// Note: dispatcher.tick() currently deletes done rows on reconcile, so the
// Done column is typically empty — recent_runs surfaces failed runs instead
// (tracked in BACKLOG; see TODO comment for the underlying limitation).
// ---------------------------------------------------------------------------

const KANBAN_REFRESH_MS = 30000;
const KANBAN_DONE_LIMIT = 20;
const KANBAN_COLS = ['backlog', 'open', 'claimed', 'running', 'awaiting_review', 'failed', 'done'];
const KANBAN_COL_LABELS = {
    backlog: 'Backlog',
    open: 'Open',
    claimed: 'Claimed',
    running: 'Running',
    awaiting_review: 'Awaiting Review',
    failed: 'Failed',
    done: 'Done',
};
let _kanbanTimer = null;
let _kanbanFilter = 'all';    // workflow path or 'all'
let _kanbanCol = 'open';      // mobile-only: which column is visible

function _wfName(path) {
    if (!path) return '?';
    // Derive a short chip label: parent-dir basename (e.g. "myproj" from
    // ".../myproj/WORKFLOW.md"). Falls back to file basename, then path.
    const parts = String(path).replace(/\/+$/, '').split('/');
    if (parts.length >= 2) return parts[parts.length - 2] || parts[parts.length - 1];
    return parts[parts.length - 1] || path;
}

// Renders the small `×` in the top-right of a card. Source-line cards
// (Backlog / Open) carry file+text; run-backed cards carry task_id. Empty
// string when there's no actionable target (defensive — every card we
// render currently has one).
function _kCardDeleteBtn(card) {
    if (card.status === 'backlog' || card.status === 'open') {
        if (!card.file || !card.title) return '';
        return `<button class="kanban-delete-btn" type="button"
                       data-del-kind="source-line"
                       data-file="${attrEsc(card.file)}"
                       data-text="${attrEsc(card.title)}"
                       onclick="kanbanDeleteClick(this)"
                       title="Delete this card" aria-label="Delete">&times;</button>`;
    }
    if (!card.task_id) return '';
    return `<button class="kanban-delete-btn" type="button"
                   data-del-kind="run"
                   data-task-id="${attrEsc(card.task_id)}"
                   onclick="kanbanDeleteClick(this)"
                   title="Delete this card" aria-label="Delete">&times;</button>`;
}

function _kCard(card) {
    const title = escHtml((card.title || '').slice(0, 200));
    const wf = escHtml(_wfName(card.workflow_path));
    const delBtn = _kCardDeleteBtn(card);
    if (card.status === 'backlog') {
        // Backlog cards have no task_id, no branch, no PR — just text + section.
        const section = card.section
            ? `<span class="kanban-section-chip">${escHtml(card.section)}</span>`
            : '';
        // The Promote button moves the bullet from BACKLOG.md to TODO.md's
        // ## Open section via a single commit pushed to master; the next
        // dispatcher tick picks it up like any other TODO line.
        const promote = (card.workflow_path && card.title)
            ? `<button class="kanban-promote-btn" type="button"
                       data-wf="${attrEsc(card.workflow_path)}"
                       data-text="${attrEsc(card.title)}"
                       onclick="kanbanPromoteBacklog(this)">Promote</button>`
            : '';
        return `
    <div class="kanban-card kanban-card-backlog">
        ${delBtn}
        <div class="kanban-card-title">${title}</div>
        <div class="kanban-card-meta">
            <span class="kanban-wf-chip">${wf}</span>
            ${section}
            ${promote}
        </div>
    </div>`;
    }
    const tid = escHtml(card.task_id);
    const displayId = escHtml(_runDisplayId(card));
    const err = card.last_error
        ? `<div class="kanban-card-error" title="${attrEsc(card.last_error)}">${escHtml(card.last_error.slice(0, 120))}</div>`
        : '';
    // Under the project-key scheme the branch is just the lowercase display id
    // (e.g. PCLW-11 → pclw-11), so a branch chip would duplicate kanban-card-id.
    // Render it only when the branch differs — keeps it for legacy dogfood/<sha>
    // rows whose branch ≠ short-hash display id. Rows with no branch still fall
    // back to file:line untouched; rows whose branch == display id show neither.
    let branchOrLine = '';
    if (card.branch_name) {
        if (card.branch_name.toLowerCase() !== _runDisplayId(card).toLowerCase()) {
            branchOrLine = `<span class="ts">${escHtml(card.branch_name)}</span>`;
        }
    } else if (card.file) {
        branchOrLine = `<span class="ts">${escHtml(card.file.split('/').pop())}:${card.line_number}</span>`;
    }
    const pr = _runPrCell(card.pr_url);
    // Merge button rides next to the PR badge on Awaiting Review cards —
    // that's where cards with open, unmerged PRs live. dispatcher.tick()
    // refreshes pr_state via `gh pr view` for any row carrying a pr_url, so the
    // renderer gates on it: 'open' (or NULL — older rows / transient gh
    // failure) shows the button, 'merged' shows a badge, 'closed' shows nothing.
    const mergeable = (card.status === 'awaiting_review' && card.pr_url && card.task_id);
    let merge = '';
    if (mergeable) {
        if (card.pr_state === 'merged') {
            merge = `<span class="kanban-merged-badge" title="PR already merged">Merged ✓</span>`;
        } else if (card.pr_state === 'closed') {
            merge = '';
        } else {
            merge = `<button class="kanban-merge-btn" type="button"
                   data-task-id="${tid}"
                   data-pr-url="${attrEsc(card.pr_url)}"
                   onclick="kanbanMergePR(this)">Merge</button>`;
        }
    }
    return `
    <div class="kanban-card kanban-card-${card.status}" data-task-id="${tid}">
        ${delBtn}
        <div class="kanban-card-title">${title}</div>
        <div class="kanban-card-meta">
            <span class="kanban-wf-chip">${wf}</span>
            <span class="kanban-card-id" title="${tid}">${displayId}</span>
            ${branchOrLine}
            ${pr}
            ${merge}
        </div>
        ${err}
    </div>`;
}

// Inline confirm UI replaces window.confirm. Click ×  →  card sprouts a
// confirm strip; the × hides until the strip is dismissed. On success the
// next refresh drops the card; on failure the strip surfaces the server
// error in-place so the user sees it.
function kanbanDeleteClick(btn) {
    const card = btn.closest('.kanban-card');
    if (!card || card.querySelector('.kanban-confirm')) return;
    const confirmEl = document.createElement('div');
    confirmEl.className = 'kanban-confirm';
    confirmEl.dataset.delKind = btn.dataset.delKind || '';
    confirmEl.dataset.taskId = btn.dataset.taskId || '';
    confirmEl.dataset.file = btn.dataset.file || '';
    confirmEl.dataset.text = btn.dataset.text || '';
    confirmEl.innerHTML = `
        <span class="kanban-confirm-msg">Delete this?</span>
        <button class="btn-confirm" type="button" onclick="kanbanDeleteConfirm(this, true)">Delete</button>
        <button type="button" onclick="kanbanDeleteConfirm(this, false)">Cancel</button>`;
    card.appendChild(confirmEl);
    btn.style.visibility = 'hidden';
}

async function kanbanDeleteConfirm(actionBtn, ok) {
    const confirmEl = actionBtn.closest('.kanban-confirm');
    if (!confirmEl) return;
    const card = confirmEl.closest('.kanban-card');
    const xBtn = card ? card.querySelector('.kanban-delete-btn') : null;
    if (!ok) {
        confirmEl.remove();
        if (xBtn) xBtn.style.visibility = '';
        return;
    }
    const kind = confirmEl.dataset.delKind;
    const payload = { kind };
    if (kind === 'run') payload.task_id = confirmEl.dataset.taskId;
    else if (kind === 'source-line') {
        payload.file = confirmEl.dataset.file;
        payload.text = confirmEl.dataset.text;
    }
    confirmEl.innerHTML = '<span class="kanban-confirm-msg">Deleting…</span>';
    let resp, data = {};
    try {
        resp = await fetch(BASE_PATH + '/api/dispatcher/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        try { data = await resp.json(); } catch (_) { data = {}; }
    } catch (e) {
        _kanbanDeleteShowError(confirmEl, xBtn, String(e));
        return;
    }
    if (!resp.ok || !data.ok) {
        _kanbanDeleteShowError(confirmEl, xBtn, data.error || `HTTP ${resp.status}`);
        return;
    }
    // Idempotent on the server, so we don't distinguish "no match" / "already
    // gone" from a real delete here — let the next poll redraw the board.
    refreshKanban();
}

function _kanbanDeleteShowError(confirmEl, xBtn, msg) {
    confirmEl.innerHTML = `
        <span class="kanban-confirm-msg" style="color:var(--red);">${escHtml(msg)}</span>
        <button type="button" onclick="kanbanDeleteConfirm(this, false)">Close</button>`;
    // Leave the × hidden — Close button drives dismissal.
    if (xBtn) xBtn.style.visibility = 'hidden';
}

async function kanbanMergePR(btn) {
    const taskId = btn.dataset.taskId;
    const prUrl = btn.dataset.prUrl || '';
    const m = prUrl.match(/\/pull\/(\d+)/);
    const prN = m ? m[1] : '?';
    if (!confirm(`Squash-merge PR #${prN} and delete branch?`)) return;
    const originalLabel = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Merging…';
    let resp, data = {};
    try {
        resp = await fetch(`/api/dispatcher/runs/${encodeURIComponent(taskId)}/merge`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
        });
        try { data = await resp.json(); } catch (_) { data = {}; }
    } catch (e) {
        _kanbanMergeToast(btn, String(e));
        btn.disabled = false;
        btn.textContent = originalLabel;
        return;
    }
    if (!resp.ok || !data.ok) {
        _kanbanMergeToast(btn, data.error || `HTTP ${resp.status}`);
        btn.disabled = false;
        btn.textContent = originalLabel;
        return;
    }
    // Don't optimistically mutate UI state — let the next poll move the card.
    refreshKanban();
}

function _kanbanMergeToast(btn, msg) {
    // Surface failure on the card itself with the raw stderr — never swallow.
    const card = btn.closest('.kanban-card');
    if (!card) { console.error('kanban merge:', msg); return; }
    const old = card.querySelector('.kanban-merge-toast');
    if (old) old.remove();
    const toast = document.createElement('div');
    toast.className = 'kanban-merge-toast';
    toast.textContent = msg;
    card.appendChild(toast);
    setTimeout(() => { toast.remove(); }, 15000);
}

async function kanbanMergeAll(btn) {
    // N comes from the button's data-count (the value the user saw); the
    // backend re-derives the eligible set itself so a stale count is harmless.
    const n = btn.dataset.count || '';
    if (!confirm(`Squash-merge ${n} mergeable PRs?`)) return;
    const payload = {};
    // Pass the active workflow filter unless we're viewing all workflows.
    if (_kanbanFilter !== 'all') payload.workflow_path = _kanbanFilter;
    const originalLabel = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Merging…';
    let resp, data = {};
    try {
        resp = await fetch(BASE_PATH + '/api/dispatcher/merge-all', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        try { data = await resp.json(); } catch (_) { data = {}; }
    } catch (e) {
        _kanbanToast(`Merge all failed: ${e}`);
        btn.disabled = false; btn.textContent = originalLabel;
        return;
    }
    if (!resp.ok || !data.ok) {
        _kanbanToast(`Merge all failed: ${data.error || 'HTTP ' + resp.status}`);
        btn.disabled = false; btn.textContent = originalLabel;
        return;
    }
    // Single summary toast covering merged / skipped / failed counts, with the
    // per-task error+reason detail appended so nothing is swallowed.
    const merged = data.merged || [], skipped = data.skipped || [], failed = data.failed || [];
    const parts = [`Merged ${merged.length}`];
    if (skipped.length) parts.push(`skipped ${skipped.length}`);
    if (failed.length) parts.push(`failed ${failed.length}`);
    let msg = parts.join(', ');
    const detail = [
        ...failed.map(f => `✗ ${f.task_id}: ${f.error}`),
        ...skipped.map(s => `– ${s.task_id}: ${s.reason}`),
    ];
    if (detail.length) msg += '\n' + detail.join('\n');
    _kanbanToast(msg);
    // Don't optimistically mutate cards — let the next poll redraw the board.
    refreshKanban();
}

function _kanbanToast(msg) {
    // Floating summary toast for toolbar-level actions (merge-all) that aren't
    // anchored to a single card. Click or wait 15s to dismiss.
    const toast = document.createElement('div');
    toast.className = 'kanban-toast';
    toast.textContent = msg;
    toast.onclick = () => toast.remove();
    document.body.appendChild(toast);
    setTimeout(() => { toast.remove(); }, 15000);
}

async function kanbanPromoteBacklog(btn) {
    const wf = btn.dataset.wf || '';
    const text = btn.dataset.text || '';
    if (!wf || !text) return;
    const preview = text.length > 80 ? text.slice(0, 77) + '...' : text;
    if (!confirm(`Promote backlog item to TODO?\n\n"${preview}"\n\nThis commits to master and pushes.`)) return;
    const originalLabel = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Promoting…';
    let resp, data = {};
    try {
        resp = await fetch(BASE_PATH + '/api/dispatcher/backlog/promote', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ workflow_path: wf, text: text }),
        });
        try { data = await resp.json(); } catch (_) { data = {}; }
    } catch (e) {
        _kanbanPromoteToast(btn, String(e));
        btn.disabled = false;
        btn.textContent = originalLabel;
        return;
    }
    if (!resp.ok || !data.ok) {
        _kanbanPromoteToast(btn, data.error || `HTTP ${resp.status}`);
        btn.disabled = false;
        btn.textContent = originalLabel;
        return;
    }
    // Don't optimistically mutate — let the next /api/dispatcher poll move the
    // card from Backlog → Open once the daemon re-reads BACKLOG.md + TODO.md.
    refreshKanban();
}

function _kanbanPromoteToast(btn, msg) {
    // Surface failure on the card itself with the raw error from the backend —
    // never swallow. Mirrors the merge-button toast pattern.
    const card = btn.closest('.kanban-card');
    if (!card) { console.error('kanban promote:', msg); return; }
    const old = card.querySelector('.kanban-promote-toast');
    if (old) old.remove();
    const toast = document.createElement('div');
    toast.className = 'kanban-promote-toast';
    toast.textContent = msg;
    card.appendChild(toast);
    setTimeout(() => { toast.remove(); }, 15000);
}

function _kCol(key, label, cards) {
    const body = cards.length
        ? cards.map(_kCard).join('')
        : `<div class="kanban-empty-col">—</div>`;
    // kanban-col-mobile-active flips the column on at phone widths; ignored
    // by the desktop grid layout, which shows all six.
    const mobileActive = _kanbanCol === key ? ' kanban-col-mobile-active' : '';
    return `
    <div class="kanban-col kanban-col-${key}${mobileActive}" data-col="${key}">
        <div class="kanban-col-head">
            <span>${label}</span>
            <span class="col-count">${cards.length}</span>
        </div>
        <div class="kanban-cards">${body}</div>
    </div>`;
}

function _kanbanFlatten(data) {
    // Build seven buckets across all workflows: Backlog (BACKLOG.md bullets,
    // read-only) + six run/task statuses. workflow_path is injected onto
    // open_tasks + backlog_items so cards in those columns still get a
    // workflow chip (the API exposes it only at the workflow level).
    const buckets = { backlog: [], open: [], claimed: [], running: [], awaiting_review: [], failed: [], done: [] };
    const wfs = [];
    for (const wf of (data.workflows || [])) {
        wfs.push(wf.path);
        for (const b of (wf.backlog_items || [])) {
            buckets.backlog.push({
                status: 'backlog',
                title: b.text,
                section: b.section,
                file: b.file,
                workflow_path: wf.path,
                project_key: wf.project_key || null,
            });
        }
        for (const t of (wf.open_tasks || [])) {
            buckets.open.push({
                status: 'open',
                task_id: t.id,
                title: t.title,
                file: t.file,
                line_number: t.line_number,
                workflow_path: wf.path,
                project_key: wf.project_key || null,
            });
        }
        for (const r of (wf.active_runs || [])) {
            const status = (r.status === 'claimed' || r.status === 'running') ? r.status : 'claimed';
            buckets[status].push({ ...r, status });
        }
        for (const r of (wf.awaiting_review_runs || [])) {
            buckets.awaiting_review.push({ ...r, status: 'awaiting_review' });
        }
        for (const r of (wf.recent_runs || [])) {
            const status = (r.status === 'done' || r.status === 'failed') ? r.status : 'done';
            buckets[status].push({ ...r, status });
        }
    }
    // Most-recent-first ordering for run-derived columns; Open keeps source order.
    const byEnded = (a, b) => String(b.ended_at || '').localeCompare(String(a.ended_at || ''));
    const byStarted = (a, b) => String(b.started_at || '').localeCompare(String(a.started_at || ''));
    buckets.claimed.sort(byStarted);
    buckets.running.sort(byStarted);
    buckets.awaiting_review.sort(byEnded);
    buckets.failed.sort(byEnded);
    buckets.done.sort(byEnded);
    buckets.done = buckets.done.slice(0, KANBAN_DONE_LIMIT);
    return { buckets, workflows: wfs };
}

function _renderKanbanFilters(workflows, mergeableCount = 0) {
    const wrap = $('#kanban-filters');
    if (!workflows.length) { wrap.innerHTML = ''; return; }
    // Drop the active filter if its workflow no longer exists in the payload.
    if (_kanbanFilter !== 'all' && !workflows.includes(_kanbanFilter)) {
        _kanbanFilter = 'all';
    }
    const chip = (val, label) => {
        const active = _kanbanFilter === val ? ' active' : '';
        return `<button class="kanban-filter-chip${active}" data-wf="${escHtml(val)}"
                       onclick="setKanbanFilter('${escHtml(val).replace(/'/g, "\\'")}')">${escHtml(label)}</button>`;
    };
    let html = chip('all', `All (${workflows.length})`);
    for (const wf of workflows) {
        html += chip(wf, _wfName(wf));
    }
    // Batch-merge button: only when there's something mergeable in the active
    // filter. Reuses .kanban-merge-btn styling; pushed to the toolbar's right
    // edge via .kanban-merge-all-btn (margin-left:auto).
    if (mergeableCount > 0) {
        html += `<button class="kanban-merge-btn kanban-merge-all-btn" type="button"
                       data-count="${mergeableCount}"
                       onclick="kanbanMergeAll(this)">Merge all mergeable (${mergeableCount})</button>`;
    }
    wrap.innerHTML = html;
}

function setKanbanFilter(wf) {
    _kanbanFilter = wf || 'all';
    // Re-render against the last fetched payload via a fresh fetch — keeps
    // the filter responsive without caching the prior response shape.
    refreshKanban();
}

// Mobile column selector: pick which of the five status columns the phone
// view shows. No-op on desktop, where all columns are visible by default.
function setKanbanCol(col) {
    if (!KANBAN_COLS.includes(col)) col = 'open';
    _kanbanCol = col;
    document.querySelectorAll('.kanban-col').forEach(el => {
        el.classList.toggle('kanban-col-mobile-active', el.dataset.col === col);
    });
}

function _renderKanbanColSelect(buckets) {
    // Rebuild the mobile <select> with counts in the labels so the user knows
    // which buckets have anything before picking. Driven off the *filtered*
    // buckets so workflow filter + column selector compose correctly. Includes
    // the Backlog column so phones get to it via the same single-col selector.
    const sel = $('#kanban-col-select');
    if (!sel) return;
    const apply = arr => _kanbanFilter === 'all' ? arr : arr.filter(c => c.workflow_path === _kanbanFilter);
    sel.innerHTML = KANBAN_COLS.map(k => {
        const n = apply(buckets[k] || []).length;
        const sel = k === _kanbanCol ? ' selected' : '';
        return `<option value="${k}"${sel}>${KANBAN_COL_LABELS[k]} (${n})</option>`;
    }).join('');
}

async function refreshKanban() {
    let data;
    try {
        data = await api('/api/dispatcher');
    } catch (e) {
        console.error('refreshKanban', e);
        return;
    }
    const board = $('#kanban-board');
    const empty = $('#kanban-empty');
    const filters = $('#kanban-filters');
    if (!data.configured || !data.workflows || !data.workflows.length) {
        board.innerHTML = '';
        filters.innerHTML = '';
        empty.style.display = 'block';
        return;
    }
    empty.style.display = 'none';

    const { buckets, workflows } = _kanbanFlatten(data);

    // Apply the workflow filter to every column (Open included).
    const apply = arr => _kanbanFilter === 'all' ? arr : arr.filter(c => c.workflow_path === _kanbanFilter);

    // Merge-all count = awaiting_review cards that GitHub reports MERGEABLE in
    // the active filter. Drives the toolbar button's label + visibility.
    const mergeableCount = apply(buckets.awaiting_review)
        .filter(c => c.pr_mergeable === 'MERGEABLE').length;
    _renderKanbanFilters(workflows, mergeableCount);
    _renderKanbanColSelect(buckets);

    board.innerHTML = [
        _kCol('backlog',         'Backlog',         apply(buckets.backlog)),
        _kCol('open',            'Open',            apply(buckets.open)),
        _kCol('claimed',         'Claimed',         apply(buckets.claimed)),
        _kCol('running',         'Running',         apply(buckets.running)),
        _kCol('awaiting_review', 'Awaiting Review', apply(buckets.awaiting_review)),
        _kCol('failed',          'Failed',          apply(buckets.failed)),
        _kCol('done',            'Done',            apply(buckets.done)),
    ].join('');
}

function startKanbanTimer() {
    stopKanbanTimer();
    _kanbanTimer = setInterval(refreshKanban, KANBAN_REFRESH_MS);
}

function stopKanbanTimer() {
    if (_kanbanTimer) { clearInterval(_kanbanTimer); _kanbanTimer = null; }
}

