// CMX-241: the pane header's words, as straight functions of an /api/agents row.
import { expect, test } from 'vitest';
import { claudeTitle, displayLabel, dotState, DOT_FOR_STATE, headLabel, headState, isShell, paneSubtitle, wantsHuman, type HeadAgent } from '../index.ts';

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

test('displayLabel: manual name > Claude title > window name > "claude window"; unknown is the id', () => {
  expect(displayLabel({ ...A, manual_name: true, ai_title: 'T' }, '@1')).toBe('win-1');
  expect(displayLabel({ ...A, ai_title: 'T' }, '@1')).toBe('T');
  expect(displayLabel(A, '@1')).toBe('win-1');
  expect(displayLabel({ window_id: '@1' }, '@1')).toBe('claude window');
  expect(displayLabel({ window_id: '@1', manual_name: true }, '@1')).toBe('@1');
  expect(displayLabel(undefined, '@9')).toBe('@9');
});

test('paneSubtitle: the window name when a title leads, else a distinct ai_title, else nothing', () => {
  expect(paneSubtitle({ ...A, ai_title: 'T' }, '@1')).toBe('win-1');
  expect(paneSubtitle({ ...A, manual_name: true, ai_title: 'T' }, '@1')).toBe('T');
  expect(paneSubtitle({ ...A, manual_name: true, ai_title: 'win-1' }, '@1')).toBe('');
  expect(paneSubtitle(A, '@1')).toBe('');
  expect(paneSubtitle({ window_id: '@1', ai_title: 'T' }, '@1')).toBe('');
  expect(paneSubtitle(undefined, '@1')).toBe('');
});

test('isShell: only an explicit claude_running false', () => {
  expect(isShell({ ...A, claude_running: false })).toBe(true);
  expect(isShell(A)).toBe(false);
  expect(isShell({ window_id: '@1' })).toBe(false);
  expect(isShell(undefined)).toBe(false);
});

test('headLabel: a shell reads Shell and its cwd; a named shell keeps its name; a session is unchanged', () => {
  const shell = { ...A, claude_running: false, shell_cwd: '~/x' };
  expect(headLabel(shell, '@1')).toEqual({ title: 'Shell', subtitle: '~/x', cwd: true });
  expect(headLabel({ ...shell, shell_cwd: null }, '@1')).toEqual({ title: 'Shell', subtitle: '', cwd: true });
  expect(headLabel({ ...shell, manual_name: true }, '@1')).toEqual({ title: 'win-1', subtitle: '~/x', cwd: true });
  expect(headLabel({ ...A, ai_title: 'T' }, '@1')).toEqual({ title: 'T', subtitle: 'win-1', cwd: false });
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
