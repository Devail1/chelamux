// The diff modal's pure helpers (ported from tests/diffpanel_model.test.mjs, CMX-237).
import { expect, test } from 'vitest';
import { diffFiles, patchLineClass, patchLines, statusMeta, summaryLabel } from '../model.ts';

test('summaryLabel names the real reason there is nothing to show', () => {
  expect(summaryLabel({ is_git: false, has_head: false, files: [] })).toBe('Not a git repository');
  expect(summaryLabel(null)).toBe('Not a git repository');
  expect(summaryLabel(undefined)).toBe('Not a git repository');
  expect(summaryLabel({ is_git: true, has_head: false, files: [] })).toBe('No commits yet');
  expect(summaryLabel({ is_git: true, has_head: true, files: [] })).toBe('No changes');
  expect(summaryLabel({ is_git: true, has_head: true })).toBe('No changes');
});

test('summaryLabel counts files (1 file, not 1 files) and reports the summed totals', () => {
  const f = (path: string) => ({ path, status: 'modified', additions: 1, deletions: 1 });
  expect(summaryLabel({ is_git: true, has_head: true, files: [f('a.py')], additions: 3, deletions: 1 })).toBe('1 file changed · +3 −1');
  expect(summaryLabel({ is_git: true, has_head: true, files: [f('a'), f('b'), f('c')], additions: 42, deletions: 7 })).toBe(
    '3 files changed · +42 −7',
  );
  expect(summaryLabel({ is_git: true, has_head: true, files: [f('a'), f('b')] })).toBe('2 files changed · +0 −0');
});

test('statusMeta maps each known status to its own glyph and class', () => {
  expect(statusMeta('added')).toEqual({ label: 'A', cls: 'diff-status-added' });
  expect(statusMeta('modified')).toEqual({ label: 'M', cls: 'diff-status-modified' });
  expect(statusMeta('deleted')).toEqual({ label: 'D', cls: 'diff-status-deleted' });
  expect(statusMeta('untracked')).toEqual({ label: 'U', cls: 'diff-status-untracked' });
  // A real diffsurface status (git's unmerged), not the unknown fallback.
  expect(statusMeta('conflicted')).toEqual({ label: '!', cls: 'diff-status-conflicted' });
});

test('statusMeta falls back for an unknown status, including an inherited property name', () => {
  expect(statusMeta('bogus')).toEqual({ label: '?', cls: 'diff-status-unknown' });
  expect(statusMeta('toString')).toEqual({ label: '?', cls: 'diff-status-unknown' });
});

test('diffFiles reads only an array of files', () => {
  const files = [{ path: 'a', status: 'added', additions: 1, deletions: 0 }];
  expect(diffFiles({ files })).toBe(files);
  expect(diffFiles({ files: 'nope' as never })).toEqual([]);
  expect(diffFiles(null)).toEqual([]);
});

test('patchLineClass: file headers are meta (checked before + and -), then hunk, add, del, context', () => {
  expect(patchLineClass('+++ b/chela/diffsurface.py')).toBe('diff-line-meta');
  expect(patchLineClass('--- a/chela/diffsurface.py')).toBe('diff-line-meta');
  expect(patchLineClass('@@ -1,4 +1,6 @@')).toBe('diff-line-hunk');
  expect(patchLineClass('+new line')).toBe('diff-line-add');
  expect(patchLineClass('-old line')).toBe('diff-line-del');
  expect(patchLineClass(' unchanged line')).toBe('diff-line-ctx');
  expect(patchLineClass(undefined)).toBe('diff-line-ctx');
});

test('patchLines drops exactly one trailing newline', () => {
  expect(patchLines('a\nb\n')).toEqual(['a', 'b']);
  expect(patchLines('a\n\n')).toEqual(['a', '']);
  expect(patchLines('a')).toEqual(['a']);
});
