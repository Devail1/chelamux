// --- Stage 0: ES-module imports ---
import { $, attrEsc, escHtml, closeModal, lucideIcon, shortTime, showModal } from './util';
import { _runDisplayId, _runPrCell } from './dispatcher';
import { runStatusBadgeClass } from './runstate';
import { briefHtml, briefSource, defaultTab, displayTitle, riskChip, timelineSteps, trackerHref } from './taskmodalmodel';
import { knMd, knInline } from './knowledge';

// ---------------------------------------------------------------------------
// The task-detail modal — a Jira-like "issue" view a kanban card click opens
// (Work-view redesign). LEFT (wide): tabs — Brief (the task's brief rendered as
// markdown, reusing knowledge.js's knMd) | Review (N) (one collapsible card per
// `review_history` round, newest first and open). RIGHT (narrow): the compact
// details sidebar (workflow / branch / PR / CI / attempts / rework / judge /
// timestamps / error) and a round list that jumps to a round's card.
//
// History, so it doesn't get re-litigated: the timeline sat in the sidebar
// (2026-07-25) so the brief could lead. CMX-122 (Liav, 2026-10-13): after 13
// rounds a sidebar of full judge reports was unreadable, so the reports moved
// to the wide column behind a tab, and the default tab follows the run — Review
// while a verdict is what needs reading, Brief otherwise (defaultTab()).
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

// ⚖️🎚️ CMX-405: the run's risk level, with WHY it has it (marker / label / inferred / default).
function _riskHtml(item) {
    const meta = riskChip(item);
    if (!meta) return '';
    const why = meta.reason ? ` <span class="ts">${escHtml(meta.reason)}</span>` : '';
    return `<span class="kanban-risk-chip ${meta.cls}">${escHtml(meta.label)}</span>${why}`;
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

function _verdictChip(s) {
    return `<span class="badge ${runStatusBadgeClass(s.state)}">${escHtml(_statusLabel(s.state))}</span>`;
}

function _roundNo(s, i) {
    return escHtml(String(s.round != null ? s.round : i + 1));
}

// The Review tab: one <details> per round, NEWEST first, only the newest open. `data-ri`
// is the round's index in review_history (oldest = 0) — the round list's jump target.
function _roundsHtml(steps) {
    if (!steps.length) return '<div class="task-modal-empty">No review history yet.</div>';
    return steps.map((s, i) => {
        const survived = s.visible || s.heldOut
            ? `<span class="ts">${s.visible} survived · ${s.heldOut} held-out</span>` : '';
        return `
        <details class="task-modal-round" data-ri="${i}"${i === steps.length - 1 ? ' open' : ''}>
          <summary class="task-modal-timeline-head">
            <span class="task-modal-round-no">Round ${_roundNo(s, i)}</span> ${_verdictChip(s)} ${survived}
            ${s.at ? `<span class="ts">${escHtml(shortTime(s.at))}</span>` : ''}
          </summary>
          ${s.detail ? `<div class="task-modal-timeline-body md">${knMd(s.detail)}</div>` : ''}
        </details>`;
    }).reverse().join('');
}

function _roundListHtml(steps) {
    return steps.map((s, i) => `<li><button type="button" class="task-modal-roundlink" onclick="chela.taskModalRound(${i})">Round ${_roundNo(s, i)} ${_verdictChip(s)}</button></li>`)
        .reverse().join('');
}

// The header's task key: a link to the Linear issue when the tracker handed us its URL
// (trackerHref), else the plain chip it always was.
function _keyHtml(item, displayId) {
    if (!displayId) return '';
    const href = trackerHref(item);
    if (!href) return `<span class="kanban-card-id">${escHtml(displayId)}</span>`;
    return `<a class="kanban-card-id kanban-tracker-link" href="${attrEsc(href)}" target="_blank" rel="noopener noreferrer"
               title="Open ${attrEsc(displayId)} in Linear">${escHtml(displayId)} ${lucideIcon('external-link', 12)}</a>`;
}

// Per-task last tab, for this page session only (a module Map — gone on reload, by design).
const _tabByTask = new Map();
let _curKey = null;
const _TABS = ['brief', 'review'];

// `remember`: only a viewer's own choice is kept — never the default the modal opened on,
// or a run that later moves to awaiting_review would stay pinned to Brief.
function _selectTab(name, focus, remember = true) {
    if (remember && _curKey) _tabByTask.set(_curKey, name);
    for (const t of _TABS) {
        const tab = $(`#tm-tab-${t}`);
        const panel = $(`#tm-panel-${t}`);
        if (!tab || !panel) continue;
        const on = t === name;
        tab.setAttribute('aria-selected', String(on));
        tab.tabIndex = on ? 0 : -1;
        panel.hidden = !on;
        if (on && focus) tab.focus();
    }
}

// WAI-ARIA tabs: ←/→ move between tabs (wrapping), Home/End jump to the ends.
function taskModalTabKey(e) {
    const cur = _TABS.indexOf(e.target && e.target.dataset ? e.target.dataset.tab : '');
    const next = { ArrowRight: cur + 1, ArrowLeft: cur - 1, Home: 0, End: _TABS.length - 1 }[e.key];
    if (cur < 0 || next === undefined) return;
    e.preventDefault();
    _selectTab(_TABS[(next + _TABS.length) % _TABS.length], true);
}

function taskModalTab(name) {
    _selectTab(name, false);
}

// A round-list click: switch to Review, open that round's card, scroll it into view.
function taskModalRound(i) {
    _selectTab('review', false);
    const card = $(`#tm-panel-review .task-modal-round[data-ri="${i}"]`);
    if (!card) return;
    card.open = true;
    card.scrollIntoView({ block: 'start', behavior: 'smooth' });
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
        _sideRow('Risk', _riskHtml(item)),
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
    const steps = timelineSteps(item.review_history);
    _curKey = item.task_id || item.id || null;

    const html = `
      <div class="task-modal-head">
        <div class="task-modal-headline">
          ${wf ? `<span class="kanban-wf-chip">${escHtml(wf)}</span>` : ''}
          ${_keyHtml(item, displayId)}
          <span class="badge ${runStatusBadgeClass(item.status)}">${escHtml(_statusLabel(item.status))}</span>
        </div>
        <h3 class="task-modal-title">${title}</h3>
      </div>
      <div class="task-modal-body">
        <div class="task-modal-main">
          <div class="task-modal-tabs" role="tablist" aria-label="Task" onkeydown="chela.taskModalTabKey(event)">
            <button type="button" class="task-modal-tab" role="tab" id="tm-tab-brief" data-tab="brief" aria-controls="tm-panel-brief" onclick="chela.taskModalTab('brief')">Brief</button>
            <button type="button" class="task-modal-tab" role="tab" id="tm-tab-review" data-tab="review" aria-controls="tm-panel-review" onclick="chela.taskModalTab('review')">Review (${steps.length})</button>
          </div>
          <div class="task-modal-brief md" role="tabpanel" id="tm-panel-brief" aria-labelledby="tm-tab-brief">${_briefPane(item)}</div>
          <div class="task-modal-review" role="tabpanel" id="tm-panel-review" aria-labelledby="tm-tab-review">${_roundsHtml(steps)}</div>
        </div>
        <div class="task-modal-side">
          <div class="task-modal-sub">Details</div>
          ${rows || '<div class="task-modal-empty">No details recorded.</div>'}
          ${steps.length ? `<div class="task-modal-rounds"><div class="task-modal-sub">Rounds</div><ol class="task-modal-roundlist">${_roundListHtml(steps)}</ol></div>` : ''}
        </div>
      </div>`;

    const content = $('#task-modal-content');
    if (content) content.innerHTML = html;
    _selectTab(_tabByTask.get(_curKey) || defaultTab(item, steps.length), false, false);
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
Object.assign(window.chela, { closeTaskModal, openTaskModal, taskModalRound, taskModalTab, taskModalTabKey });
