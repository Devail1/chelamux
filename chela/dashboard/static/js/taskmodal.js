// --- Stage 0: ES-module imports ---
import { $, escHtml, closeModal, shortTime, showModal } from './util.js';
import { _runDisplayId, _runPrCell } from './dispatcher.js';
import { runStatusBadgeClass } from './runstate.js';
import { briefHtml, briefSource, displayTitle, timelineSteps } from './taskmodalmodel.js';
import { knMd, knInline } from './knowledge.js';

// ---------------------------------------------------------------------------
// The task-detail modal — a Jira-like "issue" view a kanban card click opens
// (Work-view redesign). LEFT (wide): the task's brief rendered as markdown
// (reusing knowledge.js's knMd — no new markdown dependency). RIGHT (narrow):
// the compact details sidebar (workflow / branch / PR / CI / attempts / rework
// / judge / timestamps / error) followed by the review timeline built from
// `review_history`.
//
// The timeline lived in the LEFT column for part of 2026-07-25 and was moved
// back. Worth knowing why, so it doesn't get re-litigated: at the time almost
// every run predated brief-capture-at-claim, so `_briefPane` rendered a
// one-line "No brief recorded" note and the wide column was dead space while
// the markdown-heavy timeline was squeezed into the sidebar. Once the daemon
// picked up brief capture and real briefs started landing, the wide column had
// its intended occupant back and the timeline returned to the sidebar. The
// trade is deliberate: a long verdict is narrower here, but the brief — the
// thing the ticket is actually about — leads.
//
// openTaskModal(item) is intentionally "pure enough": it takes the SAME card
// object kanban.js already has in hand (from the one /api/dispatcher poll) and
// never re-fetches. `item` is one of:
//   - a run dict (dict(r) from chela.dispatcher.list_runs(), full column set —
//     carries `brief`, `review_history`, `judge_state`, `pr_url`, ...), or
//   - an open-task dict (`{id, title, file, line_number, raw, body, ...}` — no
//     run yet, so no PR/judge/timeline fields), or
//   - a backlog item (`{title, section, file, ...}` — no id at all).
// Every field access below is defensive for exactly that reason.
// ---------------------------------------------------------------------------

// Short workflow label — same derivation as kanban.js's _wfName (not exported,
// and small enough that duplicating it beats a cross-module coupling for one
// five-line display helper).
function _wfShort(path) {
    if (!path) return '';
    const parts = String(path).replace(/\/+$/, '').split('/');
    if (parts.length >= 2) return parts[parts.length - 2] || parts[parts.length - 1];
    return parts[parts.length - 1] || path;
}

function _statusLabel(status) {
    return String(status || '?').replace(/_/g, ' ');
}

// Mirrors kanban.js's CI_CHIPS table (not exported) — reuses the SAME
// `.kanban-ci-chip`/`.ci-*` CSS classes so no new styling is needed. Kept
// local rather than exported from kanban.js: the Board's card renderer and
// this modal are two independent consumers of one small display table, not
// two halves of one feature.
const _CI_LABELS = {
    passing: { label: '✓ ci green', cls: 'ci-passing' },
    failing: { label: '✗ CI RED', cls: 'ci-failing' },
    pending: { label: '● ci pending', cls: 'ci-pending' },
    none: { label: '– no ci', cls: 'ci-none' },
    unknown: { label: '? ci unknown', cls: 'ci-unknown' },
};

function _ciChip(item) {
    if (!item.pr_url) return '';
    const meta = _CI_LABELS[item.pr_checks] || _CI_LABELS.unknown;
    return `<span class="kanban-ci-chip ${meta.cls}">${escHtml(meta.label)}</span>`;
}

// judge_state is one of chela.judge's J_* values (running/clean/blocked/
// cannot_verify/blocked_race) — reuse the existing severity-ladder badge classes so a
// blocked judgement reads exactly as loud as a failed run, not a new color.
const _JUDGE_BADGE = {
    clean: 'badge-on',
    blocked: 'badge-off',
    running: 'badge-awaiting',
    cannot_verify: 'badge-priority-low',
    // CMX-239: a CONFIRMED blocking finding the run row never recorded (the CAS-refused
    // race) — as loud as an ordinary `blocked`, never `cannot_verify`'s low-priority tier.
    blocked_race: 'badge-off',
};

function _judgeHtml(item) {
    if (!item.judge_state) return '';
    const cls = _JUDGE_BADGE[item.judge_state] || 'badge-priority-low';
    const detail = item.judge_detail ? ` <span class="ts">${escHtml(item.judge_detail)}</span>` : '';
    return `<span class="badge ${cls}">${escHtml(item.judge_state)}</span>${detail}`;
}

function _sideRow(label, valueHtml) {
    if (valueHtml == null || valueHtml === '') return '';
    return `<div class="task-modal-row"><div class="task-modal-row-label">${escHtml(label)}</div><div class="task-modal-row-value">${valueHtml}</div></div>`;
}

function _prRow(item) {
    if (!item.pr_url) return '';
    // event.stopPropagation() here is NOT about the modal (it's already open) —
    // it just keeps a click on the PR link from also toggling anything else the
    // link happens to sit inside of, mirroring the same guard the kanban card
    // needs for its own PR badge.
    const link = `<span onclick="event.stopPropagation()">${_runPrCell(item.pr_url)}</span>`;
    const state = item.pr_state ? `<span class="ts">${escHtml(item.pr_state)}</span>` : '';
    return [link, state, _ciChip(item)].filter(Boolean).join(' ');
}

function _timelineHtml(item) {
    const steps = timelineSteps(item.review_history);
    if (!steps.length) return '<div class="task-modal-empty">No review history yet.</div>';
    return '<ol class="task-modal-timeline">' + steps.map(s => `
        <li class="task-modal-timeline-step">
          <div class="task-modal-timeline-head">
            <span class="badge ${runStatusBadgeClass(s.state)}">${escHtml(_statusLabel(s.state))}</span>
            ${s.round != null ? `<span class="ts">round ${escHtml(String(s.round))}</span>` : ''}
            ${s.at ? `<span class="ts">${escHtml(shortTime(s.at))}</span>` : ''}
          </div>
          ${s.detail ? `<div class="task-modal-timeline-body md">${knMd(s.detail)}</div>` : ''}
        </li>`).join('') + '</ol>';
}

function _briefPane(item) {
    // briefSource() picks brief > body > raw (see its own doc comment in
    // taskmodalmodel.js). None of the three existing (a backlog item, a
    // legacy pre-migration run row, or a bare one-line task with no
    // continuation) degrades to a plain note, never a blank pane.
    const src = briefSource(item);
    if (!src) return '<div class="task-modal-empty">No brief recorded for this task.</div>';
    return briefHtml(src);
}

function _sideRows(item) {
    const wf = _wfShort(item.workflow_path);
    return [
        _sideRow('Workflow', wf ? escHtml(wf) : ''),
        _sideRow('Branch', item.branch_name ? escHtml(item.branch_name) : ''),
        _sideRow('Model', item.model ? escHtml(item.model) : ''),
        _sideRow('PR', _prRow(item)),
        _sideRow('Attempt', item.attempt != null ? escHtml(String(item.attempt)) : ''),
        _sideRow('Reworks', item.rework_count ? escHtml(String(item.rework_count)) : ''),
        _sideRow('Judge', _judgeHtml(item)),
        _sideRow('Started', item.started_at ? escHtml(shortTime(item.started_at)) : ''),
        _sideRow('Ended', item.ended_at ? escHtml(shortTime(item.ended_at)) : ''),
        _sideRow('Error', item.last_error ? `<span class="task-modal-error">${escHtml(item.last_error)}</span>` : ''),
    ].filter(Boolean).join('');
}

// Fills #modal-task from a card object kanban.js already holds (no fetch) and
// shows it. Safe to call with a sparse/partial item — every field is guarded.
function openTaskModal(item) {
    if (!item) return;
    const wf = _wfShort(item.workflow_path);
    const displayId = _runDisplayId(item);
    // displayTitle() strips the leading bold-span brief down to its concise
    // inner text (display-only — the PARSED item.title is never touched, see
    // taskmodalmodel.js's doc comment); knInline renders what's left (any
    // remaining markdown/emoji) instead of leaving literal `**`/backticks.
    const title = knInline(displayTitle(item.title || '(untitled)').slice(0, 300));
    const rows = _sideRows(item);

    const html = `
      <div class="task-modal-head">
        <div class="task-modal-headline">
          ${wf ? `<span class="kanban-wf-chip">${escHtml(wf)}</span>` : ''}
          ${displayId ? `<span class="kanban-card-id">${escHtml(displayId)}</span>` : ''}
          <span class="badge ${runStatusBadgeClass(item.status)}">${escHtml(_statusLabel(item.status))}</span>
        </div>
        <h3 class="task-modal-title">${title}</h3>
      </div>
      <div class="task-modal-body">
        <div class="task-modal-brief md">${_briefPane(item)}</div>
        <div class="task-modal-side">
          <div class="task-modal-sub">Details</div>
          ${rows || '<div class="task-modal-empty">No details recorded.</div>'}
          <div class="task-modal-sub">Review timeline</div>
          ${_timelineHtml(item)}
        </div>
      </div>`;

    const content = $('#task-modal-content');
    if (content) content.innerHTML = html;
    showModal('modal-task');
    _bindTaskModalDismiss();
}

function closeTaskModal() {
    closeModal('modal-task');
    _unbindTaskModalDismiss();
}

function _taskModalKey(e) {
    if (e.key === 'Escape') closeTaskModal();
}

function _taskModalBackdrop(e) {
    // Only a click on the overlay ITSELF (outside .modal) dismisses — same
    // e.target === backdrop test terminals.js's shares sheet uses.
    if (e.target && e.target.id === 'modal-task') closeTaskModal();
}

function _bindTaskModalDismiss() {
    document.addEventListener('keydown', _taskModalKey, true);
    const overlay = $('#modal-task');
    if (overlay) overlay.addEventListener('click', _taskModalBackdrop);
}

function _unbindTaskModalDismiss() {
    document.removeEventListener('keydown', _taskModalKey, true);
    const overlay = $('#modal-task');
    if (overlay) overlay.removeEventListener('click', _taskModalBackdrop);
}

// --- Stage 0: ES-module exports ---
// `_JUDGE_BADGE` is exported (like dispatcher.js's `_runDisplayId`/`_runPrCell`) so a
// guard test can pin its severity mapping without driving the full modal DOM.
export { openTaskModal, closeTaskModal, _JUDGE_BADGE };

// --- Stage 0: window.chela — surface reachable from inline HTML handlers ---
window.chela = window.chela || {};
Object.assign(window.chela, { closeTaskModal, openTaskModal });
