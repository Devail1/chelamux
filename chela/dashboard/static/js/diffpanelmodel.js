// ---------------------------------------------------------------------------
// PER-SESSION DIFF PANEL MODEL — pure helpers for the changed-files surface
// (CMX-299). No DOM: diffpanel.js owns the render (the modal a session's
// "Files" chip opens); this file only turns the raw /api/agents/<wid>/diff
// response and a patch's line text into what the modal displays, so both are
// directly unit-testable (tests/diffpanel_model.test.mjs), same split
// taskmodalmodel.js draws for its own modal.
// ---------------------------------------------------------------------------

// Per-file status letter + CSS class, mirroring diffsurface.py's
// `_STATUS_NAMES` (added/modified/deleted/conflicted) plus the one status
// diffsurface mints itself for a path `git diff` never reports on: untracked.
// An unrecognized status (a future diffsurface.py addition this file hasn't
// caught up to yet) falls back to '?' rather than throwing on a sparse row.
const _STATUS_META = {
    added: { label: 'A', cls: 'diff-status-added' },
    modified: { label: 'M', cls: 'diff-status-modified' },
    deleted: { label: 'D', cls: 'diff-status-deleted' },
    untracked: { label: 'U', cls: 'diff-status-untracked' },
    conflicted: { label: '!', cls: 'diff-status-conflicted' },
};

export function statusMeta(status) {
    return _STATUS_META[status] || { label: '?', cls: 'diff-status-unknown' };
}

// The modal header line — the one-glance summary a session's whole changed-
// files state reduces to. `state` is the raw /api/agents/<wid>/diff response
// (or null/undefined while a fetch is in flight): every branch below is a
// distinct, real state a session can be in, not a generic fallback, so the
// header never says "no changes" when the true reason is "not a git repo" or
// "this session hasn't committed anything yet".
export function summaryLabel(state) {
    if (!state || !state.is_git) return 'Not a git repository';
    if (!state.has_head) return 'No commits yet';
    const files = Array.isArray(state.files) ? state.files : [];
    if (!files.length) return 'No changes';
    const n = files.length;
    const added = state.additions || 0;
    const deleted = state.deletions || 0;
    return `${n} file${n === 1 ? '' : 's'} changed · +${added} −${deleted}`;
}

// Per-patch-line CSS class for the expanded diff view. Order matters: a
// `+++`/`---` file-header line starts with the same char `+`/`-` diff hunk
// lines do, so those two must be checked before the single-char tests below
// them or every unified diff's own header would render as an added/removed
// line.
export function patchLineClass(line) {
    if (typeof line !== 'string') return 'diff-line-ctx';
    if (line.startsWith('+++') || line.startsWith('---')) return 'diff-line-meta';
    if (line.startsWith('@@')) return 'diff-line-hunk';
    if (line.startsWith('+')) return 'diff-line-add';
    if (line.startsWith('-')) return 'diff-line-del';
    return 'diff-line-ctx';
}
