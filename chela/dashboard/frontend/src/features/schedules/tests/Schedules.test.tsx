// The Schedules segment and its add-schedule dialog (ported from static/js/schedules.js,
// CMX-231; it had no suite of its own).
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { agents } from '../../../shared/store/agents.ts';
import { Schedules, refreshSchedules, showAddSchedule } from '../index.ts';
import { clip, scheduleHint } from '../model.ts';

const SCHED = [
  { id: 3, agent_name: 'researcher', schedule_type: 'interval', schedule_value: '30m', prompt: 'p'.repeat(61), last_run: null, next_run: null, enabled: true },
  { id: 4, agent_name: 'risk', schedule_type: 'cron', schedule_value: '0 9 * * *', prompt: 'check <risk>', last_run: null, next_run: null, enabled: false },
];
const CRON = { jobs: [
  { schedule: '0 * * * *', command: 'c'.repeat(71), project: 'proj', tz: 'UTC' },
  { schedule: '@daily', command: 'backup', project: null, tz: 'local' },
] };

let calls: { url: string; init?: RequestInit }[] = [];
let answers: Record<string, () => Promise<unknown>> = {};
let confirmAnswer = true;

beforeEach(() => {
  calls = [];
  confirmAnswer = true;
  answers = { '/api/schedules': () => Promise.resolve(SCHED), '/api/cron': () => Promise.resolve(CRON) };
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const body = await (answers[url] ?? (() => Promise.resolve({ ok: true })))();
    return { ok: true, status: 200, json: () => Promise.resolve(body) };
  });
  vi.stubGlobal('confirm', () => confirmAnswer);
  agents.set([{ name: 'zeta' }, { name: 'alpha' }, { window_id: '@1' }]);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const gets = () => calls.filter((c) => !c.init?.method).map((c) => c.url);
const rows = (i: number) => [...(document.querySelectorAll('table')[i]?.querySelectorAll('tbody tr') ?? [])].map((tr) => [...tr.querySelectorAll('td')].map((td) => td.textContent));

async function shown() {
  render(<Schedules />);
  await act(async () => refreshSchedules());
}

test('nothing is fetched until work.js asks; then both tables fill', async () => {
  render(<Schedules />);
  expect(gets()).toEqual([]);
  await act(async () => refreshSchedules());
  expect(gets().sort()).toEqual(['/api/cron', '/api/schedules']);
  const [a, b] = rows(0);
  expect(a?.slice(0, 3)).toEqual(['3', 'researcher', 'every 30m']);
  expect(a?.[3]).toBe(`${'p'.repeat(60)}...`);
  expect(a?.slice(4, 7)).toEqual(['-', '-', 'ON']);
  expect(b?.slice(2, 4)).toEqual(['daily at 09:00', 'check <risk>']);
  expect(document.querySelectorAll('.badge')[1]?.className).toBe('badge badge-off');
  expect(rows(1)).toEqual([['0 * * * * UTC', 'proj', `${'c'.repeat(70)}…`, '-'], ['@daily', '-', 'backup', '-']]);
  expect(document.querySelector('.work-empty')).toBeNull();
  expect(document.querySelector('.cron-empty')).toBeNull();
});

test('no schedules shows the explainer, whose button opens the add dialog; no cron says why', async () => {
  answers['/api/schedules'] = () => Promise.resolve([]);
  answers['/api/cron'] = () => Promise.resolve({ jobs: [], error: 'crontab: not installed' });
  await shown();
  expect(document.querySelector('.work-empty-title')?.textContent).toBe('Put an agent on a schedule');
  expect(document.querySelector('.cron-empty')?.textContent).toBe('crontab: not installed');
  fireEvent.click(screen.getByRole('button', { name: 'Schedule an agent' }));
  expect(screen.getByRole('dialog', { name: 'Add Scheduled Task' })).not.toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
});

test('an empty crontab says "No cron jobs"; a failed read keeps what was shown', async () => {
  answers['/api/cron'] = () => Promise.resolve({ jobs: [] });
  await shown();
  expect(document.querySelector('.cron-empty')?.textContent).toBe('No cron jobs');
  answers['/api/schedules'] = () => Promise.reject(new Error('down'));
  answers['/api/cron'] = () => Promise.reject(new Error('down'));
  await act(async () => refreshSchedules());
  expect(rows(0).length).toBe(2);
  expect(document.querySelector('.cron-empty')?.textContent).toBe('No cron jobs');
});

test('Disable / Enable PATCH the flipped state and refetch', async () => {
  await shown();
  calls = [];
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Disable' })));
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Enable' })));
  const patches = calls.filter((c) => c.init?.method === 'PATCH').map((c) => [c.url, c.init?.body]);
  expect(patches).toEqual([['/api/schedules/3', '{"enabled":false}'], ['/api/schedules/4', '{"enabled":true}']]);
  expect(gets().filter((u) => u === '/api/schedules').length).toBe(2);
});

test('Del asks first; declined, nothing is sent', async () => {
  await shown();
  calls = [];
  confirmAnswer = false;
  await act(async () => fireEvent.click(screen.getAllByRole('button', { name: 'Del' })[0] as HTMLElement));
  expect(calls).toEqual([]);
  confirmAnswer = true;
  await act(async () => fireEvent.click(screen.getAllByRole('button', { name: 'Del' })[1] as HTMLElement));
  expect(calls.filter((c) => c.init?.method).map((c) => [c.url, c.init?.method])).toEqual([['/api/schedules/4', 'DELETE']]);
  expect(gets()).toContain('/api/schedules');
});

test('the add dialog: agents sorted by name, the value hint follows the type, an add POSTs', async () => {
  await shown();
  fireEvent.click(screen.getByRole('button', { name: '+ Add Task' }));
  const agent = screen.getByRole('combobox', { name: 'Agent' });
  expect(agent.textContent).toBe('alpha');
  fireEvent.keyDown(agent, { key: 'Enter' });
  expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual(['alpha', 'zeta']);
  fireEvent.click(screen.getByRole('option', { name: 'zeta' }));
  const value = screen.getByRole('textbox', { name: 'Interval (e.g. 30s, 5m, 1h)' }) as HTMLInputElement;
  expect(value.placeholder).toBe('5m');
  fireEvent.keyDown(screen.getByRole('combobox', { name: 'Schedule type' }), { key: 'Enter' });
  fireEvent.click(screen.getByRole('option', { name: 'Cron' }));
  expect((screen.getByRole('textbox', { name: 'Cron expression (5 fields: min hr dom mon dow)' }) as HTMLInputElement).placeholder).toBe('0 */8 * * *');
  calls = [];
  // A missing value or prompt sends nothing.
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Add' })));
  expect(calls).toEqual([]);
  fireEvent.change(screen.getByRole('textbox', { name: 'Cron expression (5 fields: min hr dom mon dow)' }), { target: { value: ' 0 9 * * * ' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'Prompt' }), { target: { value: ' refresh ' } });
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Add' })));
  const post = calls.find((c) => c.init?.method === 'POST');
  expect([post?.url, post?.init?.body]).toEqual(['/api/schedules', JSON.stringify({ agent_name: 'zeta', schedule_type: 'cron', schedule_value: '0 9 * * *', prompt: 'refresh' })]);
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(gets()).toContain('/api/schedules');
});

test('showAddSchedule opens the dialog from outside (the ⌘K palette); Cancel closes it', () => {
  render(<Schedules />);
  act(() => showAddSchedule());
  expect(screen.getByRole('dialog', { name: 'Add Scheduled Task' })).not.toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('model: each type has its own hint and an unknown one falls back to interval; clip', () => {
  expect(scheduleHint('once')).toEqual({ label: 'Run at (ISO 8601, e.g. 2026-01-15T09:00)', placeholder: 'YYYY-MM-DDTHH:MM' });
  expect(scheduleHint('bogus')).toEqual(scheduleHint('interval'));
  expect([clip('abc', 3), clip('abcd', 3), clip('abcd', 3, '...')]).toEqual(['abc', 'abc…', 'abc...']);
});

test('with no agent to pick, Add sends nothing', async () => {
  agents.set([]);
  await shown();
  act(() => showAddSchedule());
  fireEvent.change(screen.getByRole('textbox', { name: 'Interval (e.g. 30s, 5m, 1h)' }), { target: { value: '5m' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'Prompt' }), { target: { value: 'go' } });
  calls = [];
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Add' })));
  expect(calls).toEqual([]);
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
});
