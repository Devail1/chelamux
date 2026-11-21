// CMX-241: the pane header's words, as straight functions of an /api/agents row.
import { expect, test } from 'vitest';
import { claudeTitle, displayLabel, dotState, DOT_FOR_STATE, headLabel, headState, isShell, paneSubtitle, wantsHuman, type HeadAgent } from '../index.ts';
import { canRetitle, titleDraft } from '../model.ts';

const A: HeadAgent = { window_id: '@1', name: 'win-1', claude_running: true };

test('wantsHuman: a pane-probed gate or Claude waiting on a reply; nothing else', () => {
  expect(wantsHuman({ ...A, needs_human: true })).toBe(true);
  expect(wantsHuman({ ...A, session_status: 'waiting' })).toBe(true);
  expect(wantsHuman({ ...A, needs_human: false, session_status: 'busy' })).toBe(false);
  expect(wantsHuman(undefined)).toBe(false);
  expect(wantsHuman(null)).toBe(false);
});

test('claudeTitle: ai_title, else the session name, else empty', () => {
  expect(claudeTitle({ ...A, ai_title: 'T', session_name: 'S' })).toBe('T');
  expect(claudeTitle({ ...A, session_name: 'S' })).toBe('S');
  expect(claudeTitle(A)).toBe('');
  expect(claudeTitle(undefined)).toBe('');
});

test('CMX-434: claudeTitle and paneSubtitle: a /rename title (custom_title) outranks ai_title', () => {
  expect(claudeTitle({ ...A, custom_title: 'Tipstack', ai_title: 'Tetris-style blocks', session_name: 'S' })).toBe('Tipstack');
  expect(displayLabel({ ...A, custom_title: 'Tipstack', ai_title: 'Tetris-style blocks' }, '@1')).toBe('Tipstack');
  expect(paneSubtitle({ ...A, manual_name: true, custom_title: 'Tipstack', ai_title: 'Tetris-style blocks' }, '@1')).toBe('Tipstack');
});

test('displayLabel: manual name > Claude title > window name > "claude window"; unknown is the id', () => {
  expect(displayLabel({ ...A, manual_name: true, ai_title: 'T' }, '@1')).toBe('win-1');
  expect(displayLabel({ ...A, ai_title: 'T' }, '@1')).toBe('T');
  expect(displayLabel(A, '@1')).toBe('win-1');
  expect(displayLabel({ window_id: '@1' }, '@1')).toBe('claude window');
  expect(displayLabel({ window_id: '@1', manual_name: true }, '@1')).toBe('@1');
  expect(displayLabel(undefined, '@9')).toBe('@9');
});

// CMX-459: a deliberate change from CMX-62 (the subtitle was the window name when a title led).
test('paneSubtitle: Claude\'s title beside the name, manual or not, unless it repeats the name', () => {
  expect(paneSubtitle({ ...A, ai_title: 'T' }, '@1')).toBe('T');
  expect(paneSubtitle({ ...A, manual_name: true, ai_title: 'T' }, '@1')).toBe('T');
  expect(paneSubtitle({ ...A, session_name: 'S' }, '@1')).toBe('S');
  expect(paneSubtitle({ ...A, manual_name: true, ai_title: 'win-1' }, '@1')).toBe('');
  expect(paneSubtitle({ ...A, ai_title: 'win-1' }, '@1')).toBe('');
  expect(paneSubtitle(A, '@1')).toBe('');
  // no window name: the title still differs from the id the header shows
  expect(paneSubtitle({ window_id: '@1', ai_title: 'T' }, '@1')).toBe('T');
  expect(paneSubtitle({ window_id: '@1', ai_title: '@1' }, '@1')).toBe('');
  expect(paneSubtitle(undefined, '@1')).toBe('');
});

test('isShell: only an explicit claude_running false', () => {
  expect(isShell({ ...A, claude_running: false })).toBe(true);
  expect(isShell(A)).toBe(false);
  expect(isShell({ window_id: '@1' })).toBe(false);
  expect(isShell(undefined)).toBe(false);
});

test('headLabel: a shell reads Shell and its cwd; a named shell keeps its name; a session leads with its name', () => {
  const shell = { ...A, claude_running: false, shell_cwd: '~/x' };
  expect(headLabel(shell, '@1')).toEqual({ title: 'Shell', subtitle: '~/x', cwd: true });
  expect(headLabel({ ...shell, shell_cwd: null }, '@1')).toEqual({ title: 'Shell', subtitle: '', cwd: true });
  expect(headLabel({ ...shell, manual_name: true }, '@1')).toEqual({ title: 'win-1', subtitle: '~/x', cwd: true });
  // CMX-459: the name first, the title second (CMX-62 had the title lead an un-renamed window)
  expect(headLabel({ ...A, ai_title: 'T' }, '@1')).toEqual({ title: 'win-1', subtitle: 'T', cwd: false });
  expect(headLabel({ ...A, manual_name: true, ai_title: 'T' }, '@1')).toEqual({ title: 'win-1', subtitle: 'T', cwd: false });
  expect(headLabel({ window_id: '@1', ai_title: 'T' }, '@1')).toEqual({ title: '@1', subtitle: 'T', cwd: false });
  expect(headLabel(undefined, '@3')).toEqual({ title: '@3', subtitle: '', cwd: false });
});

test('headState: a shell turns only `idle` into `shell`', () => {
  const shell = { ...A, claude_running: false };
  expect(headState(shell)).toEqual({ word: 'shell', cls: 'shell', title: 'Shell: no Claude session runs in this pane' });
  expect(headState({ ...shell, needs_human: true }).word).toBe('needs you');
  expect(headState({ ...shell, pr: { url: 'u', state: 'open' } }).word).toBe('PR open');
  expect(headState({ ...A, session_status: 'idle' })).toEqual({ word: 'idle', cls: 'idle', title: 'Idle' });
  expect(headState(undefined)).toEqual({ word: 'idle', cls: 'idle', title: 'Idle' });
});

test('the dot vocabulary maps every tile state onto the four shapes', () => {
  expect(DOT_FOR_STATE).toEqual({
    'needs-you': 'waiting', working: 'working', 'pr-open': 'done', unknown: 'idle', idle: 'idle',
    testing: 'working', died: 'waiting',
  });
  expect(dotState({ ...A, session_status: 'busy' })).toBe('working');
  expect(dotState({ ...A, needs_human: true })).toBe('waiting');
  expect(dotState({ ...A, session_status: 'idle', pr: { url: 'u', state: 'open' } })).toBe('done');
  expect(dotState({ ...A, judge_battery: { state: 'testing', label: 'x' } })).toBe('working');
  expect(dotState({ ...A, judge_battery: { state: 'died', label: 'x' } })).toBe('waiting');
  expect(dotState({ ...A, claude_running: false })).toBe('idle');
  expect(dotState(undefined)).toBe('idle');
});

test('CMX-459 canRetitle: only where the server said Claude runs', () => {
  expect(canRetitle(A)).toBe(true);
  expect(canRetitle({ ...A, claude_running: false })).toBe(false);
  expect(canRetitle({ window_id: '@1' })).toBe(false);
  expect(canRetitle(undefined)).toBe(false);
  expect(canRetitle(null)).toBe(false);
});

test('CMX-459 titleDraft: a queued title, else Claude\'s title (custom > ai > session), else empty', () => {
  expect(titleDraft({ ...A, title_pending: 'Q', custom_title: 'C', ai_title: 'T' })).toBe('Q');
  expect(titleDraft({ ...A, custom_title: 'C', ai_title: 'T' })).toBe('C');
  expect(titleDraft({ ...A, ai_title: 'T', session_name: 'S' })).toBe('T');
  expect(titleDraft({ ...A, session_name: 'S' })).toBe('S');
  expect(titleDraft(A)).toBe('');
  expect(titleDraft(undefined)).toBe('');
});
