// PER-SESSION DIFF PANEL MODEL — pure summary/status/patch-line guards
// (chela/dashboard/static/js/diffpanelmodel.js). No DOM: straight
// function-of-inputs checks, each written to go RED under one specific
// corruption of the real logic.
//
// Run: node --test tests/diffpanel_model.test.mjs (tests/test_js_suites.py
// runs every .test.mjs inside pytest, by discovery).
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { patchLineClass, statusMeta, summaryLabel } from '../chela/dashboard/static/js/diffpanelmodel.js';

// --- summaryLabel ------------------------------------------------------

test('summaryLabel: not a git repo', () => {
    // 🔴 GUARD: swapping this branch's condition (or its message) for the
    // has_head/files checks below would make a non-git session's diff modal
    // claim "no changes" instead of naming the real reason there is nothing
    // to show.
    assert.equal(summaryLabel({ is_git: false, has_head: false, files: [] }), 'Not a git repository');
    assert.equal(summaryLabel(null), 'Not a git repository');
    assert.equal(summaryLabel(undefined), 'Not a git repository');
});

test('summaryLabel: a git repo with no commits yet', () => {
    assert.equal(summaryLabel({ is_git: true, has_head: false, files: [] }), 'No commits yet');
});

test('summaryLabel: a repo with a HEAD but nothing changed', () => {
    assert.equal(summaryLabel({ is_git: true, has_head: true, files: [] }), 'No changes');
});

test('summaryLabel: singular file count reads "1 file", not "1 files"', () => {
    const state = { is_git: true, has_head: true, files: [{ path: 'a.py' }], additions: 3, deletions: 1 };
    assert.equal(summaryLabel(state), '1 file changed · +3 −1');
});

test('summaryLabel: plural file count and totals', () => {
    // 🔴 GUARD: reading state.files.length instead of the summed additions/
    // deletions (or vice versa) would silently swap which number reports
    // which thing.
    const state = {
        is_git: true, has_head: true,
        files: [{ path: 'a.py' }, { path: 'b.py' }, { path: 'c.py' }],
        additions: 42, deletions: 7,
    };
    assert.equal(summaryLabel(state), '3 files changed · +42 −7');
});

// --- statusMeta ----------------------------------------------------------

test('statusMeta: known statuses map to their own label + class', () => {
    assert.deepEqual(statusMeta('added'), { label: 'A', cls: 'diff-status-added' });
    assert.deepEqual(statusMeta('modified'), { label: 'M', cls: 'diff-status-modified' });
    assert.deepEqual(statusMeta('deleted'), { label: 'D', cls: 'diff-status-deleted' });
    assert.deepEqual(statusMeta('untracked'), { label: 'U', cls: 'diff-status-untracked' });
});

test('statusMeta: an unrecognized status falls back rather than throwing', () => {
    assert.deepEqual(statusMeta('bogus'), { label: '?', cls: 'diff-status-unknown' });
    assert.deepEqual(statusMeta(undefined), { label: '?', cls: 'diff-status-unknown' });
});

// --- patchLineClass --------------------------------------------------------

test('patchLineClass: file-header lines are meta, not add/del', () => {
    // 🔴 GUARD: checking the single-char '+'/'-' tests before the '+++'/'---'
    // tests would misclassify every unified diff's own file-header lines as
    // an added/removed line.
    assert.equal(patchLineClass('+++ b/chela/diffsurface.py'), 'diff-line-meta');
    assert.equal(patchLineClass('--- a/chela/diffsurface.py'), 'diff-line-meta');
});

test('patchLineClass: hunk headers, added, removed, and context lines', () => {
    assert.equal(patchLineClass('@@ -1,4 +1,6 @@'), 'diff-line-hunk');
    assert.equal(patchLineClass('+new line'), 'diff-line-add');
    assert.equal(patchLineClass('-old line'), 'diff-line-del');
    assert.equal(patchLineClass(' unchanged line'), 'diff-line-ctx');
});
