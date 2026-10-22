// CMX-221: Settings' pure half (model.ts). The Concurrency half is tests/concurrency_model.test.mjs,
// ported (CMX-164).
import { expect, test } from 'vitest';
import {
  agentModeLabel, agentModelLabel, boolChoice, costRows, costTable, countText, fmtTokens, fmtWhen, knobPayload, limitView, memNote,
  projectOf, rejection, searchMatches, searchTerms, sparkBars, stepPayload, switchState, updateView, workflowNote, type Concurrency, type Knob,
} from '../model.ts';

const GiB = 1024 ** 3;
const A = { name: 'a', path: '/a/WORKFLOW.md', effective: 3, override: null, file_value: 3 };
const B = { name: 'b', path: '/b/WORKFLOW.md', effective: 8, override: 8, file_value: 1 };
const state: Concurrency = {
  min: 1, max: 8, judge_peak_bytes: GiB, available_bytes: 4 * GiB, available_source: 'x.slice',
  judges: { effective: 2, source: 'env' },
  workflows: [A, B],
};

test('stepPayload: + and - step the right workflow, by path; clamped to the bounds; 0 resets', () => {
  expect(stepPayload(state, 'agents', 0, 1)).toEqual({ workflow: '/a/WORKFLOW.md', value: 4 });
  expect(stepPayload(state, 'agents', 0, -1)).toEqual({ workflow: '/a/WORKFLOW.md', value: 2 });
  expect(stepPayload(state, 'agents', 1, 1)).toEqual({ workflow: '/b/WORKFLOW.md', value: 8 });
  expect(stepPayload({ ...state, judges: { effective: 1, source: 'default' } }, 'judges', 0, -1)).toEqual({ judges: 1 });
  expect(stepPayload(state, 'judges', 0, 1)).toEqual({ judges: 3 });
  expect(stepPayload(state, 'judges', 0, 0)).toEqual({ judges: '' });
  expect(stepPayload(state, 'agents', 1, 0)).toEqual({ workflow: '/b/WORKFLOW.md', value: '' });
});

test('memNote and workflowNote: N × the judge peak against what is free; the file or an override', () => {
  expect(memNote(state, 3)).toBe('Memory: up to 3 × 1.0 GB judge peak = 3.0 GB. Available now: 4.0 GB (x.slice).');
  expect(memNote({ ...state, available_bytes: null }, 1)).toMatch(/Available now: unknown\.$/);
  expect(workflowNote(state, A)).toMatch(/^From WORKFLOW\.md\. Memory: up to 3 /);
  expect(workflowNote(state, B)).toMatch(/^Override \(WORKFLOW\.md says 1\)\. Memory: up to 8 /);
});

test('agent labels: the default and the documented modes say so, the rest are the raw name', () => {
  expect(agentModeLabel('auto', 'default')).toBe('auto · safe ops auto-approved, risky ones gated');
  expect(agentModeLabel('default', 'default')).toBe('default · built-in default');
  expect(agentModeLabel('plan', 'default')).toBe('plan');
  expect(agentModelLabel('sonnet', 'sonnet')).toBe('sonnet · default');
  expect(agentModelLabel('opus', 'sonnet')).toBe('opus');
});

test('switchState: env-locked names the env var; else this setting or the built-in default', () => {
  expect(switchState({ remote_control: true, remote_control_env_locked: true, remote_control_env: 'X_RC' }, 'remote_control', 'CHELA_REMOTE_CONTROL'))
    .toEqual({ on: true, locked: true, env: 'X_RC', from: 'env' });
  expect(switchState({ file_drop: false, file_drop_source: 'dashboard' }, 'file_drop', 'CHELA_FILE_DROP'))
    .toEqual({ on: false, locked: false, env: 'CHELA_FILE_DROP', from: 'this setting' });
  expect(switchState(null, 'share_typing', 'CHELA_SHARE_TYPING')).toEqual({ on: false, locked: false, env: 'CHELA_SHARE_TYPING', from: 'the built-in default' });
});

test('updateView: behind, stale, unreadable, up to date, unknown, each with its button or none', () => {
  expect(updateView(null)).toEqual({ kind: 'unknown', badge: 'Unknown', detail: 'unavailable', button: null });
  expect(updateView({ ok: false, note: 'no upstream' })).toMatchObject({ kind: 'unknown', detail: 'no upstream' });
  expect(updateView({ ok: true, behind: 2, branch: 'dev', stale_services: ['a'] })).toMatchObject({ kind: 'behind', badge: '2 behind', button: { mode: 'update', label: 'Update now' } });
  expect(updateView({ ok: true, behind: 0, branch: 'dev', stale_services: ['a', 'b'] })).toMatchObject({ kind: 'stale', badge: '2 stale', button: { mode: 'restart', label: 'Restart stale services (2)' } });
  expect(updateView({ ok: true, behind: 0, stale_ok: false })).toMatchObject({ kind: 'unreadable', badge: 'Unknown', button: null });
  expect(updateView({ ok: true, behind: 0, stale_ok: true, unknown_services: ['x'] })).toMatchObject({ kind: 'ok', badge: 'Up to date', unknown: ['x'], button: null });
});

const knobs: Knob[] = [
  { key: 'n', label: 'N', kind: 'number', default: 3, stored: 5, effective: 5, source: 'dashboard' },
  { key: 't', label: 'T', kind: 'text', default: 'dev', stored: '', effective: 'dev', source: 'default' },
  { key: 'e', label: 'E', default: 1, stored: '', effective: 9, source: 'env' },
  { key: 'b', label: 'B', kind: 'bool', default: true, stored: false, effective: false, source: 'dashboard' },
  { key: 'u', label: 'U', kind: 'bool', default: true, stored: '', effective: true, source: 'default' },
];

test('knobPayload: every editable row, drafts over stored, trimmed; env rows never; bools true/false/""', () => {
  expect(knobPayload(knobs, {})).toEqual({ n: '5', t: '', b: 'false', u: '' });
  expect(knobPayload(knobs, { n: ' 7 ', e: '1', u: 'true', b: 'default' })).toEqual({ n: '7', t: '', b: '', u: 'true' });
});

test('boolChoice: an env knob shows its effective value, a stored one its value, unset the Default', () => {
  expect(knobs.slice(3).map(boolChoice)).toEqual(['false', 'default']);
  expect(boolChoice({ key: 'j', label: 'J', kind: 'bool', default: true, effective: false, source: 'env' })).toBe('false');
  expect(boolChoice({ key: 'j', label: 'J', kind: 'bool', default: true, effective: true, source: 'env' })).toBe('true');
});

test('rejection: each field error, or "rejected"', () => {
  expect(rejection({ errors: { a: 'bad', b: 'worse' } })).toBe('a: bad; b: worse');
  expect(rejection({ error: 'x' })).toBe('rejected');
  expect(rejection(null)).toBe('rejected');
});

test('costRows / costTable: the project is the cwd basename; unknown costs stay unknown', () => {
  expect(projectOf('/srv/app/')).toBe('app');
  expect(projectOf('')).toBeNull();
  const rows = costRows([{ name: 'a', cwd: '/p/x' }], [{ name: 'a', cost_usd: 1 }, { name: 'b', cost_usd: null }]);
  expect(rows).toEqual([{ name: 'a', model: undefined, cost_usd: 1, project: 'x' }, { name: 'b', model: undefined, cost_usd: null, project: '(unknown)' }]);
  expect(costRows({}, {})).toEqual([]);
  const t = costTable(rows);
  expect(t.projects.map((p) => [p.name, p.total, p.known])).toEqual([['x', 1, true], ['(unknown)', 0, false]]);
  expect(t.fleet).toBe(1);
  expect(costTable([{ name: 'z', project: 'p', cost_usd: null }]).fleet).toBeNull();
});

test('fmtTokens and fmtWhen at their boundaries', () => {
  expect([null, 999, 1000, 1e6, 1e9].map((n) => fmtTokens(n))).toEqual(['-', '999', '1.0k', '1.0M', '1.0B']);
  expect(fmtWhen(null, 0)).toBe('');
  expect(fmtWhen(100, 100)).toBe('now');
  expect(fmtWhen(100 + 59 * 60, 100)).toBe('59m');
  expect(fmtWhen(100 + 2 * 3600 + 300, 100)).toBe('2h 5m');
  expect(fmtWhen(100 + 24 * 3600, 100)).toBe('1d 0h');
});

test('limitView: unknown carries its reason; levels at 60 and 80; the fill is clamped', () => {
  expect(limitView(undefined)).toEqual({ unknown: true, reason: 'no data' });
  expect(limitView({ used_pct: 59.9 })).toMatchObject({ unknown: false, level: '', burn: 'burn n/a' });
  expect(limitView({ used_pct: 60 })).toMatchObject({ level: 'warn' });
  expect(limitView({ used_pct: 80, burn_pct_per_h: 2 })).toMatchObject({ level: 'high', burn: 'burn 2.0%/h' });
  expect(limitView({ used_pct: 140 })).toMatchObject({ pct: 100, used: 140 });
});

test('sparkBars: nothing for no data; a zero draws no bar, any activity at least 1px', () => {
  expect(sparkBars(undefined)).toEqual([]);
  expect(sparkBars([0, 1, 100]).map((b) => b.h)).toEqual(['0.00', '1.00', '16.00']);
});

test('search: whitespace-collapsed, case-blind, every word must match (AND); the count reads as words', () => {
  expect(searchTerms('  Remote   PHONE ')).toEqual(['remote', 'phone']);
  expect(searchTerms('   ')).toEqual([]);
  expect(searchMatches('Remote Control: phone', ['remote', 'phone'])).toBe(true);
  expect(searchMatches('Remote Control', ['remote', 'zzq'])).toBe(false);
  expect([countText(1, 'x'), countText(2, 'x'), countText(0, ' x ')]).toEqual(['1 setting', '2 settings', "No settings match 'x'"]);
});
