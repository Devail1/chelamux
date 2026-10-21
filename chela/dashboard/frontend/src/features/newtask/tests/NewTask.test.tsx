// ➕📐 CMX-6: the New-task dialog (ported from tests/new_task.test.mjs's form half, CMX-231).
// The toolbar button, the ⌘K row and the card a created issue becomes stay in that file: they
// drive the legacy page.
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { setWorkPoll, work, type WorkPayload } from '../../../shared/store/work.ts';
import { NewTask, openNewTask } from '../index.ts';
import { PRIORITIES, blockerChoices, linearWorkflows } from '../model.ts';

const WF = '/repo/WORKFLOW.md';
const OTHER = '/other/WORKFLOW.md';
function payload(kind = 'linear'): WorkPayload {
  return { configured: true, workflows: [
    { path: WF, project_key: 'CMX', tracker_kind: kind, open_tasks: [{ id: 'CMX-1', title: 'first open task' }],
      active_runs: [{ task_id: 'CMX-2', title: 'running task', status: 'running' }], awaiting_review_runs: [{ task_id: 'CMX-1', title: 'dup' }, { task_id: 'CMX-3', title: null }], recent_runs: [{ task_id: 'CMX-9' }] },
    { path: OTHER, project_key: null, tracker_kind: 'linear', open_tasks: [{ id: 'OTH-1', title: 'o' }] },
    { path: '/md/WORKFLOW.md', tracker_kind: 'markdown' },
  ] };
}

let calls: { url: string; init?: RequestInit }[] = [];
let reply: () => Promise<{ status: number; body: unknown }>;
const polls = vi.fn(() => Promise.resolve());
const alerts: string[] = [];

beforeEach(() => {
  calls = [];
  alerts.length = 0;
  polls.mockClear();
  setWorkPoll(polls);
  reply = () => Promise.resolve({ status: 200, body: { ok: true, issue: { identifier: 'CMX-100' }, warnings: [] } });
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const { status, body } = await reply();
    return { ok: status < 400, status, json: () => (body === undefined ? Promise.reject(new SyntaxError('<')) : Promise.resolve(body)) };
  });
  vi.stubGlobal('alert', (m: string) => alerts.push(String(m)));
  work.set(payload());
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const dialog = () => screen.queryByRole('dialog', { name: 'New task' });
const titleBox = () => screen.getByRole('textbox', { name: 'Title' }) as HTMLInputElement;
const descBox = () => screen.getByRole('textbox', { name: 'Description' }) as HTMLTextAreaElement;
const blockers = () => screen.getByRole('listbox', { name: 'Blocked by' }) as HTMLSelectElement;
const error = () => screen.getByRole('alert').textContent;

function open() {
  render(<NewTask />);
  act(() => openNewTask());
}
function pick(name: string, option: string) {
  fireEvent.keyDown(screen.getByRole('combobox', { name }), { key: 'Enter' });
  fireEvent.click(screen.getByRole('option', { name: option }));
}
function fill({ title = 'Add a thing', desc = '## Brief\n\nDo **it**.', blocked = ['CMX-1'] } = {}) {
  fireEvent.change(titleBox(), { target: { value: title } });
  fireEvent.change(descBox(), { target: { value: desc } });
  pick('Priority', 'High');
  for (const o of blockers().options) o.selected = blocked.includes(o.value);
  fireEvent.change(blockers());
}
async function submit() {
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Create' })));
}
const created = () => calls.filter((c) => c.url === '/api/dispatcher/linear/issue');

function assertKept(title: string, desc: string) {
  expect(dialog()).not.toBeNull();
  expect([titleBox().value, descBox().value]).toEqual([title, desc]);
  expect(screen.getByRole('combobox', { name: 'Priority' }).textContent).toBe('High');
  expect([...blockers().selectedOptions].map((o) => o.value)).toEqual(['CMX-1']);
  expect((screen.getByRole('button', { name: 'Create' }) as HTMLButtonElement).disabled).toBe(false);
  expect(polls).not.toHaveBeenCalled();
  expect(alerts).toEqual([]);
}

test('model: linear workflows only; blockers are open tasks then in-flight runs, each once', () => {
  expect(linearWorkflows(payload()).map((w) => w.path)).toEqual([WF, OTHER]);
  expect(linearWorkflows(null)).toEqual([]);
  expect(blockerChoices(payload().workflows?.[0])).toEqual([
    { id: 'CMX-1', title: 'first open task' }, { id: 'CMX-2', title: 'running task' }, { id: 'CMX-3', title: '' }]);
  expect(blockerChoices(undefined)).toEqual([]);
  expect(PRIORITIES.map((p) => p.value)).toEqual(['0', '1', '2', '3', '4']);
});

test('opens with the first linear workflow, focus in Title, and its open issues as blockers', () => {
  render(<NewTask />);
  expect(dialog()).toBeNull();
  act(() => openNewTask());
  expect(dialog()?.className).toBe('modal modal-newtask');
  expect(screen.getByRole('combobox', { name: 'Workflow' }).textContent).toBe('CMX');
  expect(document.activeElement).toBe(titleBox());
  expect([...blockers().options].map((o) => o.textContent)).toEqual(['CMX-1: first open task', 'CMX-2: running task', 'CMX-3: ']);
  expect(error()).toBe('');
});

test('switching workflow offers its blockers (named by path with no key) and drops the old picks', async () => {
  open();
  fill();
  pick('Workflow', OTHER);
  expect([...blockers().options].map((o) => o.value)).toEqual(['OTH-1']);
  expect([...blockers().selectedOptions]).toEqual([]);
  await submit();
  expect(JSON.parse(String(created()[0]?.init?.body))).toMatchObject({ workflow_path: OTHER, blocked_by: [] });
});

test('with no linear workflow the dialog says why', () => {
  work.set({ configured: true, workflows: [{ path: '/md', tracker_kind: 'markdown' }] });
  open();
  expect(error()).toBe('No workflow here uses a Linear tracker (tracker: kind: linear).');
});

test('a submit POSTs exactly the form fields to the dashboard route, then clears, closes, refreshes', async () => {
  open();
  fill();
  await submit();
  expect(created().map((c) => [c.init?.method, (c.init?.headers as Record<string, string>)['Content-Type']])).toEqual([['POST', 'application/json']]);
  expect(JSON.parse(String(created()[0]?.init?.body))).toEqual({ workflow_path: WF, title: 'Add a thing', description: '## Brief\n\nDo **it**.', priority: 2, blocked_by: ['CMX-1'] });
  expect(calls.some((c) => c.url.includes('linear.app'))).toBe(false);
  expect(dialog()).toBeNull();
  expect(polls).toHaveBeenCalledTimes(1);
  expect(alerts).toEqual([]);
  act(() => openNewTask());
  expect([titleBox().value, descBox().value, [...blockers().selectedOptions].length]).toEqual(['', '', 0]);
  expect(screen.getByRole('combobox', { name: 'Priority' }).textContent).toBe('No priority');
});

test('a Linear error is shown and the typed brief is kept', async () => {
  reply = () => Promise.resolve({ status: 502, body: { ok: false, error: 'graphql: Title is too long' } });
  open();
  fill({ title: 'Keep me', desc: 'a long brief I typed' });
  await submit();
  expect(error()).toBe('graphql: Title is too long');
  assertKept('Keep me', 'a long brief I typed');
});

test('a network error keeps every field and says nothing was created', async () => {
  reply = () => Promise.reject(new TypeError('Failed to fetch'));
  open();
  fill({ title: 'Keep me too', desc: 'typed offline' });
  await submit();
  expect(error()).toBe('Request failed. Nothing was created. TypeError: Failed to fetch');
  assertKept('Keep me too', 'typed offline');
});

test('an HTTP error with no JSON body names the status; a 200 with ok:false is a failure', async () => {
  reply = () => Promise.resolve({ status: 500, body: undefined });
  open();
  fill({ title: 'Still here', desc: 'brief' });
  await submit();
  expect(error()).toBe('Linear refused it (HTTP 500).');
  assertKept('Still here', 'brief');
  reply = () => Promise.resolve({ status: 200, body: { ok: false, error: 'nope' } });
  await submit();
  expect(error()).toBe('nope');
  assertKept('Still here', 'brief');
});

test('an empty title never POSTs and keeps the form; the error clears on the next open', async () => {
  open();
  fill({ title: '   ', desc: 'a brief with no title' });
  await submit();
  expect(created()).toEqual([]);
  expect(error()).toBe('A title is required.');
  expect(descBox().value).toBe('a brief with no title');
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(dialog()).toBeNull();
  act(() => openNewTask());
  expect(error()).toBe('');
  expect(descBox().value).toBe('a brief with no title');
});

test('a relation warning on a success is surfaced with the new issue id', async () => {
  reply = () => Promise.resolve({ status: 200, body: { ok: true, issue: { identifier: 'CMX-101' }, warnings: ['could not mark CMX-101 blocked by CMX-1', 'x'] } });
  open();
  fill();
  await submit();
  expect(alerts).toEqual(['CMX-101 created, but: could not mark CMX-101 blocked by CMX-1; x']);
  expect(dialog()).toBeNull();
});

test('Create is disabled while the POST is in flight', async () => {
  let release: (v: { status: number; body: unknown }) => void = () => {};
  reply = () => new Promise((r) => (release = r));
  open();
  fill();
  fireEvent.click(screen.getByRole('button', { name: 'Create' }));
  expect((screen.getByRole('button', { name: 'Create' }) as HTMLButtonElement).disabled).toBe(true);
  await act(async () => release({ status: 200, body: { ok: true, issue: { identifier: 'CMX-1' } } }));
  expect(dialog()).toBeNull();
});

test('a pick whose issue left the list (a poll dropped it) is never sent as a blocker', async () => {
  open();
  fill({ blocked: ['CMX-1', 'CMX-2'] });
  const next = payload();
  const wf = next.workflows?.[0];
  if (wf) wf.active_runs = [];
  act(() => work.set(next));
  expect([...blockers().selectedOptions].map((o) => o.value)).toEqual(['CMX-1']);
  await submit();
  expect(JSON.parse(String(created()[0]?.init?.body)).blocked_by).toEqual(['CMX-1']);
});
