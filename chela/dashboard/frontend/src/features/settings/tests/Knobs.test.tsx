// CMX-221: Settings > Timing and Dispatch (the knob batches, CMX-217 / CMX-220) and Concurrency
// (CMX-164), ported from tests/settings_timing, settings_dispatch, settings_modal_precedence and
// settings_concurrency. The env-wins rule is driven through EVERY control kind (number, text,
// bool): the bool select is a different branch of the same flag (DEFEAT_SHAPES #22).
import { act, fireEvent, screen } from '@testing-library/react';
import { expect, test } from 'vitest';
import { setSettingsQuery } from '../index.ts';
import { must, openOn, pick, posts, routes, settle, type, visible } from './setup.tsx';

const knob = (key: string, label: string, extra: object = {}) => ({ key, env: `CHELA_${key.toUpperCase()}`, label, unit: 's', default: 30, stored: '', effective: 30, source: 'default', restart_required: false, ...extra });
const TIMING = [
  knob('scheduler_poll_interval_seconds', 'Daemon tick'),
  knob('doctor_check_interval_seconds', 'Doctor self-audit cadence', { default: 3600, stored: 1800, effective: 1800 }),
  knob('status_cmd_timeout_seconds', 'Status-feed subprocess timeout', { env: 'CHELA_STATUS_CMD_TIMEOUT_S', default: 45, effective: 60, source: 'env', restart_required: true }),
];
const DISPATCH = [
  knob('max_reworks', 'Rework cap', { kind: 'number', unit: '', default: 2, effective: 2 }),
  knob('merge_base', 'Autonomous base', { kind: 'text', unit: '', default: 'dev', effective: 'release-train', source: 'env', restart_required: true }),
  knob('worktree_disk_budget_bytes', 'Worktree disk budget', { kind: 'size', unit: 'bytes', default: 0, stored: '20G', effective: '20G' }),
  knob('critic_enabled', 'Critic', { kind: 'bool', unit: '', default: false, effective: false }),
  knob('judge_enabled', 'Judge', { kind: 'bool', unit: '', env: 'CHELA_JUDGE', default: true, effective: false, source: 'env', restart_required: true }),
];
const field = (kind: string, key: string) => must(document.getElementById(`${kind}-${key}`), `${kind}-${key}`) as HTMLInputElement;
const save = (section: string) => must(document.querySelector<HTMLButtonElement>(`#settings-${section} .s-row button`), 'Save');
const msgOf = (kind: string) => must(document.getElementById(`${kind}-msg`));

test('Timing: one row per knob, the stored value in the field and the default as its placeholder', async () => {
  routes['/api/config/timing'] = { knobs: TIMING };
  await openOn('timing');
  expect(document.querySelectorAll('#timing-rows .field-row').length).toBe(3);
  expect([field('timing', 'scheduler_poll_interval_seconds').value, field('timing', 'scheduler_poll_interval_seconds').placeholder]).toEqual(['', '30']);
  expect([field('timing', 'doctor_check_interval_seconds').value, field('timing', 'doctor_check_interval_seconds').placeholder]).toEqual(['1800', '3600']);
  expect(must(document.getElementById('timing-rows')).textContent).toMatch(/Daemon tick.*Doctor self-audit cadence/);
});

test('an env knob shows its effective value, disabled, badged "env" and "restart", naming its env var', async () => {
  routes['/api/config/timing'] = { knobs: TIMING };
  await openOn('timing');
  const input = field('timing', 'status_cmd_timeout_seconds');
  expect([input.disabled, input.value]).toEqual([true, '60']);
  const row = must(input.closest('.field-row'));
  const badges = [...row.querySelectorAll('.badge')].map((b) => [b.textContent, b.getAttribute('title')]);
  expect(badges).toEqual([['env', 'Set by CHELA_STATUS_CMD_TIMEOUT_S; env wins over the dashboard.'], ['restart', 'Takes effect after a daemon/dashboard restart, not the next tick.']]);
  // the plain knobs carry neither
  expect(must(field('timing', 'scheduler_poll_interval_seconds').closest('.field-row')).querySelectorAll('.badge').length).toBe(0);
});

test('Save posts every editable row (edited or not), never an env one, and says Saved', async () => {
  routes['/api/config/timing'] = { knobs: TIMING };
  routes['POST /api/config/timing'] = { knobs: TIMING.map((k) => (k.key === 'scheduler_poll_interval_seconds' ? { ...k, stored: 45, effective: 45 } : k)) };
  await openOn('timing');
  await type(field('timing', 'scheduler_poll_interval_seconds'), '45');
  fireEvent.click(save('timing'));
  await settle();
  expect(posts('/api/config/timing')).toEqual([{ scheduler_poll_interval_seconds: '45', doctor_check_interval_seconds: '1800' }]);
  expect([msgOf('timing').textContent, msgOf('timing').className]).toEqual(['Saved.', 's-savemsg ok']);
  expect(field('timing', 'scheduler_poll_interval_seconds').value).toBe('45');
});

test('a blanked field posts "" (fall back to env or the default)', async () => {
  routes['/api/config/timing'] = { knobs: TIMING };
  routes['POST /api/config/timing'] = { knobs: TIMING };
  await openOn('timing');
  await type(field('timing', 'doctor_check_interval_seconds'), '');
  fireEvent.click(save('timing'));
  await settle();
  expect(posts('/api/config/timing')).toEqual([{ scheduler_poll_interval_seconds: '', doctor_check_interval_seconds: '' }]);
});

test('a rejected batch names each field error, never says Saved, and re-reads what is stored', async () => {
  routes['/api/config/timing'] = { knobs: TIMING };
  routes['POST /api/config/timing'] = { error: 'invalid timing setting(s)', errors: { scheduler_poll_interval_seconds: 'Daemon tick must be a number' } };
  await openOn('timing');
  await type(field('timing', 'scheduler_poll_interval_seconds'), '7');
  fireEvent.click(save('timing'));
  await settle();
  expect([msgOf('timing').textContent, msgOf('timing').className]).toEqual(['Nothing saved: scheduler_poll_interval_seconds: Daemon tick must be a number', 's-savemsg err']);
  expect(field('timing', 'scheduler_poll_interval_seconds').value).toBe(''); // the stored value, not the rejected draft
});

test('a failed Save says nothing changed; a failed load says unavailable', async () => {
  routes['/api/config/timing'] = { knobs: TIMING };
  routes['POST /api/config/timing'] = new Error('down');
  await openOn('timing');
  fireEvent.click(save('timing'));
  await settle();
  expect(msgOf('timing').textContent).toBe('Save failed. Nothing changed.');
  routes['/api/config/dispatch'] = new Error('down');
  fireEvent.click(screen.getByRole('button', { name: 'Close' }));
  await openOn('dispatch');
  expect(must(document.getElementById('dispatch-rows')).textContent).toBe('(unavailable)');
});

test('Dispatch: each kind its control: number, text, size, and a 3-way bool (Default / On / Off)', async () => {
  routes['/api/config/dispatch'] = { knobs: DISPATCH };
  await openOn('dispatch');
  expect(field('dispatch', 'max_reworks').type).toBe('number');
  expect([field('dispatch', 'worktree_disk_budget_bytes').type, field('dispatch', 'worktree_disk_budget_bytes').value]).toEqual(['text', '20G']);
  const critic = field('dispatch', 'critic_enabled');
  expect([critic.getAttribute('role'), critic.textContent]).toEqual(['combobox', 'Default (off)']);
  fireEvent.keyDown(critic, { key: 'Enter' });
  expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual(['Default (off)', 'On', 'Off']);
});

test('an env knob is disabled on EVERY kind: the text field and the bool select both', async () => {
  routes['/api/config/dispatch'] = { knobs: DISPATCH };
  await openOn('dispatch');
  const base = field('dispatch', 'merge_base');
  expect([base.disabled, base.value]).toEqual([true, 'release-train']);
  const judge = field('dispatch', 'judge_enabled');
  expect(judge.getAttribute('role')).toBe('combobox');
  expect(judge.disabled).toBe(true);
  expect(judge.textContent).toBe('Off'); // the effective (env) value
  for (const el of [base, judge]) {
    expect(must(el.closest('.field-row')).querySelector('.badge[title*="CHELA_"]')?.getAttribute('title')).toMatch(el === base ? /CHELA_MERGE_BASE/ : /CHELA_JUDGE/);
  }
});

test('Dispatch Save: bools post "true"/"false"/"" (Default), env rows are left out', async () => {
  routes['/api/config/dispatch'] = { knobs: DISPATCH };
  routes['POST /api/config/dispatch'] = { knobs: DISPATCH };
  await openOn('dispatch');
  fireEvent.click(save('dispatch'));
  await settle();
  await pick('dispatch-critic_enabled', 'On');
  fireEvent.click(save('dispatch'));
  await settle();
  expect(posts('/api/config/dispatch')).toEqual([
    { max_reworks: '', worktree_disk_budget_bytes: '20G', critic_enabled: '' },
    { max_reworks: '', worktree_disk_budget_bytes: '20G', critic_enabled: 'true' },
  ]);
});

// --- Concurrency (CMX-164) ----------------------------------------------------------------

const STATE = {
  min: 1, max: 8, judge_peak_bytes: 1024 ** 3, available_bytes: 8 * 1024 ** 3, available_source: 'MemAvailable',
  workflows: [
    { name: 'alpha', path: '/r/alpha/WORKFLOW.md', effective: 3, override: null, file_value: 3 },
    { name: 'beta', path: '/r/beta/WORKFLOW.md', effective: 4, override: 4, file_value: 2 },
  ],
  judges: { effective: 2, source: 'env' },
};
const rows = () => [...document.querySelectorAll('#concurrency-rows .field-row')];
const resets = () => rows().map((r) => [...r.querySelectorAll('button')].some((b) => b.textContent === 'Reset'));
const cMsg = () => must(document.getElementById('concurrency-msg')).textContent;
const stepBtn = (i: number, name: string) => must([...(rows()[i]?.querySelectorAll('button') ?? [])].find((b) => (b.getAttribute('aria-label') ?? b.textContent) === name), name);

test('Concurrency: a row per workflow plus judges, each with its memory note; Reset only on an override', async () => {
  routes['/api/config/concurrency'] = STATE;
  await openOn('dispatch');
  expect(rows().map((r) => r.querySelector('.field-label')?.textContent)).toEqual(['Agents at once: alpha', 'Agents at once: beta', 'Judges at once']);
  expect(rows().map((r) => r.querySelector('.s-num')?.textContent)).toEqual(['3', '4', '2']);
  expect(resets()).toEqual([false, true, false]); // judges come from env here
  const text = must(document.getElementById('concurrency-rows')).textContent;
  expect(text).toMatch(/From WORKFLOW\.md\. Memory: up to 3 × 1\.0 GB judge peak = 3\.0 GB\. Available now: 8\.0 GB \(MemAvailable\)\./);
  expect(text).toMatch(/Override \(WORKFLOW\.md says 2\)/);
  expect(text).toMatch(/Source: env\./);
});

test('judges set in Settings get a Reset', async () => {
  routes['/api/config/concurrency'] = { ...STATE, judges: { effective: 3, source: 'settings' } };
  await openOn('dispatch');
  expect(resets()).toEqual([false, true, true]);
});

test('a click POSTs the stepped value, clears the message and re-renders from the reply', async () => {
  routes['/api/config/concurrency'] = STATE;
  routes['POST /api/config/concurrency'] = { ...STATE, workflows: [{ ...STATE.workflows[0], override: 4, effective: 4 }, STATE.workflows[1]] };
  await openOn('dispatch');
  fireEvent.click(stepBtn(0, 'Increase'));
  await settle();
  expect(posts('/api/config/concurrency')).toEqual([{ workflow: '/r/alpha/WORKFLOW.md', value: 4 }]);
  expect(rows()[0]?.querySelector('.s-num')?.textContent).toBe('4');
  expect(resets()).toEqual([true, true, false]);
  expect(cMsg()).toBe('');
  fireEvent.click(stepBtn(1, 'Reset'));
  await settle();
  expect(posts('/api/config/concurrency')[1]).toEqual({ workflow: '/r/beta/WORKFLOW.md', value: '' });
});

test('a rejection is shown and the rows are left as they were; a failed request says nothing changed', async () => {
  routes['/api/config/concurrency'] = STATE;
  routes['POST /api/config/concurrency'] = { error: 'judges at once must be an integer from 1 to 8' };
  await openOn('dispatch');
  fireEvent.click(stepBtn(2, 'Increase'));
  await settle();
  expect(cMsg()).toBe('Not saved: judges at once must be an integer from 1 to 8');
  expect(rows()[2]?.querySelector('.s-num')?.textContent).toBe('2');
  routes['POST /api/config/concurrency'] = new Error('down');
  fireEvent.click(stepBtn(2, 'Decrease'));
  await settle();
  expect(cMsg()).toBe('Save failed. Nothing changed.');
});

test('a failed load says unavailable', async () => {
  routes['/api/config/concurrency'] = new Error('down');
  await openOn('dispatch');
  expect(must(document.getElementById('concurrency-rows')).textContent).toBe('(unavailable)');
});

test('an active search is re-applied to freshly rendered rows', async () => {
  routes['/api/config/concurrency'] = STATE;
  routes['POST /api/config/concurrency'] = { ...STATE, judges: { effective: 3, source: 'settings' } };
  await openOn('dispatch');
  act(() => setSettingsQuery('beta'));
  await settle(1);
  fireEvent.click(stepBtn(2, 'Increase'));
  await settle();
  expect(rows().map(visible)).toEqual([false, true, false]);
});
