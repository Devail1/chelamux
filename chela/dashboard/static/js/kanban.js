// --- Stage 0: ES-module imports ---
import { $, BASE_PATH, attrEsc, escHtml } from './util.js';
import { _runDisplayId, _runPrCell } from './dispatcher.js';
import { pollWork, postWorkDelete } from './work.js';
import { openTaskModal } from './taskmodal.js';

// ---------------------------------------------------------------------------
// Render: the Board segment of WORK (the global cross-workflow kanban)
//
// Flattens the /api/dispatcher payload into a single board: Backlog, Open
// (open_tasks), Claimed / Running / Awaiting Review / Failed / Done (runs). The
// workflow chip + filter chips let one board surface state across every
// configured workflow.
//
// It no longer FETCHES: work.js owns the one poll of /api/dispatcher and hands the
// same payload here and to the runs tables (this module used to run a second timer
// against that endpoint, and the sidebar badges a third).
//
// Note: dispatcher.tick() currently deletes done rows on reconcile, so the
// Done column is typically empty — recent_runs surfaces failed runs instead
// (tracked in BACKLOG; see TODO comment for the underlying limitation).
// ---------------------------------------------------------------------------

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
// The rework loop's states (CMX-68). They ride in the Awaiting Review column — a run the
// reviewer sent back has an open PR and is nowhere near done — but they are NOT awaiting
// review, and the card must not pretend otherwise. Text, deliberately: a border colour is
// a secondary cue, never the whole message.
const REVIEW_STATE_CHIPS = {
    changes_requested: '🔁 changes requested',
    needs_human: '🛑 needs a human',
};
// What GitHub says about a card's checks. Every one of these is a WORD plus a glyph — the
// colour is a secondary cue and never the signal (Liav is red-weak, and "is this PR red?"
// is precisely the question a hue-only answer would get wrong). The three non-failing
// states are the interesting ones and each says something different: `pending` has not
// settled, `none` has no CI at all (which is NOT the same as passing), and a card with no
// recorded state at all renders as `ci ?` — not-yet-read is never a pass.
const CI_CHIPS = {
    passing: { label: '✓ ci green',   cls: 'ci-passing' },
    failing: { label: '✗ CI RED',     cls: 'ci-failing' },
    pending: { label: '● ci pending', cls: 'ci-pending' },
    none:    { label: '– no ci',      cls: 'ci-none' },
    unknown: { label: '? ci unknown', cls: 'ci-unknown' },
};
// Columns that start collapsed in the mobile "Rows" accordion — low-traffic
// buckets the user rarely needs open at a glance. Overridden + persisted per
// user once they tap a caret.
const KANBAN_DEFAULT_COLLAPSED = ['backlog', 'failed', 'done'];

let _kanbanFilter = 'all';    // workflow path or 'all'

// Every card object rendered THIS pass, in DOM order — indexed via a card's
// own `data-kidx`. Rebuilt from scratch at the top of every renderKanban()
// call (see there), so a click always resolves against the payload that is
// actually on screen, never a stale one from a previous poll. This is what
// lets a card click hand openTaskModal() the SAME object the board already
// has (from the one /api/dispatcher poll) instead of re-fetching.
let _kanbanCardIndex = [];

function openTaskModalFromCard(el) {
    const idx = Number(el && el.dataset && el.dataset.kidx);
    const card = Number.isInteger(idx) ? _kanbanCardIndex[idx] : null;
    if (card) openTaskModal(card);
}

// Mobile-only layout: 'swipe' (default, scroll-snap carousel) or 'rows'
// (collapsible accordion). Persisted so it survives the 30s self-poll and
// return visits. Desktop ignores it entirely (the 7-col grid is unchanged).
function _loadKanbanLayout() {
    try { return localStorage.getItem('chela_kanban_mlayout') === 'rows' ? 'rows' : 'swipe'; }
    catch (e) { return 'swipe'; }
}
let _kanbanLayout = _loadKanbanLayout();

// Per-user collapsed-column set for the Rows accordion. Missing key → the
// KANBAN_DEFAULT_COLLAPSED defaults (backlog / failed / done).
function _loadKanbanCollapsed() {
    try {
        const raw = localStorage.getItem('chela_kanban_collapsed');
        if (raw === null) return new Set(KANBAN_DEFAULT_COLLAPSED);
        return new Set(JSON.parse(raw));
    } catch (e) { return new Set(KANBAN_DEFAULT_COLLAPSED); }
}
let _kanbanCollapsed = _loadKanbanCollapsed();

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
// Every onclick below stops propagation FIRST: the card itself now has an
// onclick (openTaskModalFromCard, added once card rendering carries a
// data-kidx — see _kCard) — same guard the Wall draws between a tile's own
// click and its action buttons.
function _kCardDeleteBtn(card) {
    if (card.status === 'backlog' || card.status === 'open') {
        if (!card.file || !card.title) return '';
        return `<button class="kanban-delete-btn" type="button"
                       data-del-kind="source-line"
                       data-file="${attrEsc(card.file)}"
                       data-text="${attrEsc(card.title)}"
                       onclick="event.stopPropagation();chela.kanbanDeleteClick(this)"
                       title="Delete this card" aria-label="Delete">&times;</button>`;
    }
    if (!card.task_id) return '';
    return `<button class="kanban-delete-btn" type="button"
                   data-del-kind="run"
                   data-task-id="${attrEsc(card.task_id)}"
                   onclick="event.stopPropagation();chela.kanbanDeleteClick(this)"
                   title="Delete this card" aria-label="Delete">&times;</button>`;
}

function _kCard(card) {
    // Register this card in render order so a click can resolve it back to the
    // FULL object (task-detail modal — CMX task-modal) without a second fetch.
    const kidx = _kanbanCardIndex.push(card) - 1;
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
                       onclick="event.stopPropagation();chela.kanbanPromoteBacklog(this)">Promote</button>`
            : '';
        return `
    <div class="kanban-card kanban-card-backlog" data-kidx="${kidx}" onclick="chela.openTaskModalFromCard(this)">
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
    // Wrapped so a click on the PR link (opens a new tab) doesn't ALSO open the
    // task modal underneath it — same stopPropagation guard as the action
    // buttons below, without touching dispatcher.js's shared _runPrCell.
    const prRaw = _runPrCell(card.pr_url);
    const pr = prRaw ? `<span class="kanban-pr-wrap" onclick="event.stopPropagation()">${prRaw}</span>` : '';
    // The Awaiting Review column also holds the rework loop's other two states, so a card
    // that is NOT awaiting review says which one it is — in words. (Liav is red-weak: hue
    // is never the only signal, here or anywhere.)
    const stateChip = REVIEW_STATE_CHIPS[card.status]
        ? `<span class="kanban-state-chip">${escHtml(REVIEW_STATE_CHIPS[card.status])}</span>`
        : '';
    // Merge button rides next to the PR badge on Awaiting Review cards —
    // that's where cards with open, unmerged PRs live. dispatcher.tick()
    // refreshes pr_state via `gh pr view` for any row carrying a pr_url, so the
    // renderer gates on it: 'open' (or NULL — older rows / transient gh
    // failure) shows the button, 'merged' shows a badge, 'closed' shows nothing.
    const mergeable = (card.status === 'awaiting_review' && card.pr_url && card.task_id);
    // The CI chip rides on every card that has a PR — including the merged ones, where it
    // is the receipt: this is what shipped, and this is what its checks said.
    const ciState = card.pr_url ? (card.pr_checks || 'unread') : '';
    const ciMeta = CI_CHIPS[ciState] || { label: '? ci', cls: 'ci-unknown' };
    const ci = card.pr_url
        ? `<span class="kanban-ci-chip ${ciMeta.cls}" title="GitHub's checks on this PR">${escHtml(ciMeta.label)}</span>`
        : '';
    let merge = '';
    if (mergeable) {
        if (card.pr_state === 'merged') {
            merge = `<span class="kanban-merged-badge" title="PR already merged">Merged ✓</span>`;
        } else if (card.pr_state === 'closed') {
            merge = '';
        } else if (ciState === 'passing' || ciState === 'none') {
            merge = `<button class="kanban-merge-btn" type="button"
                   data-task-id="${tid}"
                   data-pr-url="${attrEsc(card.pr_url)}"
                   onclick="event.stopPropagation();chela.kanbanMergePR(this)">Merge</button>`;
        }
        // ⛔ No button at all while CI is red, pending or unread. The server refuses those
        // merges too (it re-reads the checks from GitHub at merge time — this button is a
        // cache and the gate is not), but the orchestrator must not be ABLE to click it by
        // accident, because on 2026-07-14 it did exactly that and the base branch broke.
        // The chip beside it says which of the three it is.
    }
    return `
    <div class="kanban-card kanban-card-${card.status}" data-task-id="${tid}" data-kidx="${kidx}" onclick="chela.openTaskModalFromCard(this)">
        ${delBtn}
        <div class="kanban-card-title">${title}</div>
        <div class="kanban-card-meta">
            <span class="kanban-wf-chip">${wf}</span>
            <span class="kanban-card-id" title="${tid}">${displayId}</span>
            ${branchOrLine}
            ${stateChip}
            ${pr}
            ${ci}
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
        <button class="btn-confirm" type="button" onclick="chela.kanbanDeleteConfirm(this, true)">Delete</button>
        <button type="button" onclick="chela.kanbanDeleteConfirm(this, false)">Cancel</button>`;
    // The card itself now opens the task modal on click (openTaskModalFromCard)
    // — this whole strip (its text, and both buttons, including the ones the
    // error path swaps in via _kanbanDeleteShowError) must not also trigger it.
    confirmEl.addEventListener('click', e => e.stopPropagation());
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
    // Shared with the runs table's × (work.js) — one delete action, two confirm UIs.
    const err = await postWorkDelete(payload);
    if (err) _kanbanDeleteShowError(confirmEl, xBtn, err);
}

function _kanbanDeleteShowError(confirmEl, xBtn, msg) {
    confirmEl.innerHTML = `
        <span class="kanban-confirm-msg" style="color:var(--red);">${escHtml(msg)}</span>
        <button type="button" onclick="chela.kanbanDeleteConfirm(this, false)">Close</button>`;
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
    pollWork();
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
    pollWork();
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
    pollWork();
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
    // kanban-col-collapsed drives the mobile "Rows" accordion (CSS hides
    // .kanban-cards when set, scoped to ≤768px + rows layout). Desktop and the
    // swipe carousel ignore it, so the class is harmless everywhere else.
    const collapsed = _kanbanCollapsed.has(key) ? ' kanban-col-collapsed' : '';
    // The head is a tap toggle in the Rows accordion; toggleKanbanCol no-ops
    // above 768px so desktop/swipe clicks do nothing. aria-expanded reflects
    // collapsed state for the accordion; the caret is a pure CSS ▸/▾ marker.
    return `
    <div class="kanban-col kanban-col-${key}${collapsed}" data-col="${key}">
        <div class="kanban-col-head" role="button" tabindex="0"
             aria-expanded="${_kanbanCollapsed.has(key) ? 'false' : 'true'}"
             onclick="chela.toggleKanbanCol('${key}')"
             onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();chela.toggleKanbanCol('${key}');}">
            <span class="kanban-col-caret" aria-hidden="true"></span>
            <span class="kanban-col-label">${label}</span>
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
                // The tracker's own text for this task — `body` (chela.sources.
                // Task.body: the FULL title + dedented OBJECTIVE/BOUNDARIES/
                // GUARDS/VERIFY continuation, when the markdown source captured
                // one) is an un-dispatched task's richest brief; `raw` (the bare
                // bullet line / issue URL) is the fallback. The task-detail modal
                // reads both — there's no run (and so no `brief` column) yet.
                raw: t.raw,
                body: t.body,
                workflow_path: wf.path,
                project_key: wf.project_key || null,
            });
        }
        for (const r of (wf.active_runs || [])) {
            const status = (r.status === 'claimed' || r.status === 'running') ? r.status : 'claimed';
            buckets[status].push({ ...r, status });
        }
        for (const r of (wf.awaiting_review_runs || [])) {
            // The column holds the whole review loop — awaiting_review, changes_requested
            // (sent back by the reviewer) and needs_human (the loop hit its cap). Each card
            // KEEPS ITS OWN STATUS: overwriting it with 'awaiting_review' would put a
            // Merge button on a PR that just failed review and tell the reader a run that
            // stopped is still waiting on them.
            buckets.awaiting_review.push({ ...r, status: r.status || 'awaiting_review' });
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
                       onclick="chela.setKanbanFilter('${escHtml(val).replace(/'/g, "\\'")}')">${escHtml(label)}</button>`;
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
                       onclick="chela.kanbanMergeAll(this)">Merge all mergeable (${mergeableCount})</button>`;
    }
    wrap.innerHTML = html;
}

function setKanbanFilter(wf) {
    _kanbanFilter = wf || 'all';
    // Re-render through the one poll — the filter is a client-side view of the
    // payload, so this is a redraw, not a second data source.
    pollWork();
}

// --- Mobile layout: Swipe carousel vs. Rows accordion ---
//
// The mobile board offers two layouts, gated entirely behind the ≤768px media
// query; desktop (≥769px) keeps its 7-column grid untouched. The active layout
// is a class on #work-board so both the board and the nav strip react to it.

function _applyKanbanLayout() {
    const panel = $('#work-board');
    if (panel) {
        panel.classList.toggle('kanban-mobile-swipe', _kanbanLayout === 'swipe');
        panel.classList.toggle('kanban-mobile-rows', _kanbanLayout === 'rows');
    }
    // Reflect the active toggle button (colorblind-safe: aria + a fill/weight
    // style class, never hue alone).
    document.querySelectorAll('.kanban-layout-btn').forEach(btn => {
        const on = btn.dataset.layout === _kanbanLayout;
        btn.classList.toggle('active', on);
        btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
}

// Swipe ⇄ Rows toggle. Persists the choice so it survives the 30s self-poll
// and return visits. No board re-fetch needed — both layouts render from the
// same DOM, so we just re-apply the class + refresh the nav strip.
function setKanbanLayout(layout) {
    _kanbanLayout = layout === 'rows' ? 'rows' : 'swipe';
    try { localStorage.setItem('chela_kanban_mlayout', _kanbanLayout); } catch (e) { /* ignore */ }
    _applyKanbanLayout();
}

// Rows accordion: collapse/expand a column's cards. No-ops above 768px so a
// stray desktop/swipe click on a head does nothing. Persists per user.
function toggleKanbanCol(col) {
    if (typeof window.matchMedia === 'function'
        && !window.matchMedia('(max-width: 768px)').matches) return;
    if (_kanbanCollapsed.has(col)) _kanbanCollapsed.delete(col);
    else _kanbanCollapsed.add(col);
    try { localStorage.setItem('chela_kanban_collapsed', JSON.stringify([..._kanbanCollapsed])); }
    catch (e) { /* ignore */ }
    const el = document.querySelector(`.kanban-col[data-col="${col}"]`);
    if (el) {
        const collapsed = _kanbanCollapsed.has(col);
        el.classList.toggle('kanban-col-collapsed', collapsed);
        const head = el.querySelector('.kanban-col-head');
        if (head) head.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    }
}

// Quick-nav strip (swipe layout only): one chip per column with its live
// count; tapping snap-scrolls the carousel to that column.
function _renderKanbanNav(buckets) {
    const strip = $('#kanban-nav-strip');
    if (!strip) return;
    const apply = arr => _kanbanFilter === 'all' ? arr : arr.filter(c => c.workflow_path === _kanbanFilter);
    strip.innerHTML = KANBAN_COLS.map(k => {
        const n = apply(buckets[k] || []).length;
        return `<button class="kanban-nav-chip" type="button" data-col="${k}"
                       onclick="chela.kanbanNavTo('${k}')">
                    <span class="kanban-nav-label">${KANBAN_COL_LABELS[k]}</span>
                    <span class="kanban-nav-count">${n}</span>
                </button>`;
    }).join('');
}

// Snap the carousel to a column and mark its nav chip active (colorblind-safe:
// active chip gets fill + weight + border, not hue alone).
function kanbanNavTo(col) {
    const board = $('#kanban-board');
    const el = board && board.querySelector(`.kanban-col[data-col="${col}"]`);
    if (el && typeof el.scrollIntoView === 'function') {
        el.scrollIntoView({ behavior: 'smooth', inline: 'start', block: 'nearest' });
    }
    document.querySelectorAll('.kanban-nav-chip').forEach(chip => {
        const on = chip.dataset.col === col;
        chip.classList.toggle('active', on);
        chip.setAttribute('aria-current', on ? 'true' : 'false');
    });
}

// Render the board from a payload work.js already fetched — the SAME object the
// runs tables render and the sidebar badges count.
function renderKanban(data) {
    const board = $('#kanban-board');
    const empty = $('#kanban-empty');
    const filters = $('#kanban-filters');
    if (!board || !empty || !filters) return;
    if (!data || !data.configured || !data.workflows || !data.workflows.length) {
        board.innerHTML = '';
        filters.innerHTML = '';
        empty.style.display = 'block';
        return;
    }
    empty.style.display = 'none';

    // Rebuilt every render — see _kanbanCardIndex's own comment. A card clicked
    // from a previous render's DOM (a poll landed between paint and click) will
    // resolve against WHATEVER now occupies that index, or nothing; both are
    // harmless (openTaskModalFromCard no-ops on a miss) and the next 30s poll
    // repaints the board anyway.
    _kanbanCardIndex.length = 0;

    const { buckets, workflows } = _kanbanFlatten(data);

    // Apply the workflow filter to every column (Open included).
    const apply = arr => _kanbanFilter === 'all' ? arr : arr.filter(c => c.workflow_path === _kanbanFilter);

    // Merge-all count = awaiting_review cards that GitHub reports MERGEABLE in
    // the active filter. Drives the toolbar button's label + visibility. The status test
    // is load-bearing since the rework loop shares this column: a `changes_requested` PR
    // is perfectly MERGEABLE and must never be counted into a Merge-all — it is the PR a
    // reviewer just REJECTED. (The server refuses it too; this keeps the count honest.)
    // ⛔ And a PR whose CI is RED (or pending, or never read) is never counted either — the
    // server skips it in the batch, and a count that included it would be promising a merge
    // that cannot happen. `none` (a repo with no CI at all) still counts: no checks is not
    // the same as failing checks.
    const mergeableCount = apply(buckets.awaiting_review)
        .filter(c => c.status === 'awaiting_review' && c.pr_mergeable === 'MERGEABLE'
                     && (c.pr_checks === 'passing' || c.pr_checks === 'none')).length;
    _renderKanbanFilters(workflows, mergeableCount);
    _renderKanbanNav(buckets);
    _applyKanbanLayout();

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


// --- Stage 0: ES-module exports ---
export { renderKanban };

// --- Stage 0: window.chela — surface reachable from inline HTML handlers ---
window.chela = window.chela || {};
Object.assign(window.chela, { kanbanDeleteClick, kanbanDeleteConfirm, kanbanMergeAll, kanbanMergePR, kanbanNavTo, kanbanPromoteBacklog, openTaskModalFromCard, setKanbanFilter, setKanbanLayout, toggleKanbanCol });
