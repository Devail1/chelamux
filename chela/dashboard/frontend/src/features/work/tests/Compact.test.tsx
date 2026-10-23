// CMX-315: the compact Work card and the Rows row. The face is the title, one meta line (id,
// PR, state with its icon), one flags line and a two-line note; the branch and every action,
// the destructive ones included, are in the ⋮. Orange (`warn`) only on a real warning.
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { setWorkPoll } from '../../../shared/store/work.ts';
import { TaskModal, closeTaskModal } from '../../taskmodal/index.ts';
import type { Card } from '../board.ts';
import { CompactCard, RowItem } from '../index.ts';
import { must, stubFetch } from './setup.ts';

let calls: ReturnType<typeof stubFetch> = [];
let reply: (url: string) => { status?: number; body?: unknown } = () => ({ body: { ok: true } });
beforeEach(() => {
  setWorkPoll(() => Promise.resolve());
  reply = () => ({ body: { ok: true } });
  calls = stubFetch((url) => reply(url));
});
afterEach(() => act(() => closeTaskModal()));

const card = (o: Partial<Card> & Record<string, unknown> = {}): Card =>
  ({ status: 'running', task_id: 'CMX-303', project_key: 'CMX', task_number: 303, title: 'Nightly sweep 1/3', workflow_path: '/p/chelamux/WORKFLOW.md',
    branch_name: 'liavacc/cmx-303-nightly-sweep', pr_url: 'https://github.com/o/r/pull/838', pr_checks: 'failing', rework_count: 1, risk: 'high',
    tracker_url: 'https://linear.app/x/issue/CMX-303', ...o }) as Card;

function show(c: Card, Item: typeof CompactCard = CompactCard) {
  return render(<><Item card={c} /><TaskModal /></>);
}
const face = () => must(document.querySelector<HTMLElement>('.wcard, .wrow'));
const texts = (sel: string) => [...face().querySelectorAll(sel)].map((e) => e.textContent?.trim());
function menu() {
  const trigger = must(face().querySelector<HTMLElement>('.wcard-menu-btn'));
  if (trigger.getAttribute('aria-expanded') !== 'true') fireEvent.keyDown(trigger, { key: 'Enter' });
  return must(document.querySelector<HTMLElement>('.wcard-overflow-menu'), 'the open ⋮ menu');
}
const items = () => [...menu().querySelectorAll('[role="menuitem"], [role="separator"]')].map((i) => (i.getAttribute('role') === 'separator' ? '---' : i.textContent));
const item = (label: string) => must([...menu().querySelectorAll<HTMLElement>('[role="menuitem"]')].find((i) => i.textContent === label), `menu item ${label}`);
const posts = () => calls.filter((c) => c.method === 'POST').map((c) => [c.url, c.body]);
const modalOpen = () => !!document.querySelector('[role="dialog"]');

test('the face: a two-line title, id / PR / state, one flags line, the note; no branch, no ✕, no requeue', () => {
  show(card({ handoff: 'Look first at the window gate' }));
  expect(texts('.wcard-title')).toEqual(['Nightly sweep 1/3']);
  expect(texts('.wcard-meta > span')).toEqual(['CMX-303', '#838', 'running']);
  expect(must(face().querySelector('.wcard-state svg')).getAttribute('class')).toContain('lucide-play');
  expect([...face().querySelectorAll('.wcard-flag')].map((f) => [f.textContent?.trim(), f.className])).toEqual([
    ['rework 1', 'wcard-flag warn'], ['risk high', 'wcard-flag warn'], ['CI failing', 'wcard-flag warn'],
  ]);
  expect(texts('.wcard-note')).toEqual(['Look first at the window gate']);
  expect(face().textContent).not.toContain('liavacc/cmx-303');
  expect(face().querySelectorAll('.kanban-delete-btn, .kanban-requeue-btn, .kanban-merge-btn').length).toBe(0);
  expect([...face().querySelectorAll('button')].map((b) => b.getAttribute('aria-label'))).toEqual(['Card actions']);
});

test('orange only for a real warning: risk normal, CI passing and pending are plain words', () => {
  show(card({ rework_count: 0, risk: 'normal', pr_checks: 'passing' }));
  expect([...face().querySelectorAll('.wcard-flag')].map((f) => [f.textContent?.trim(), f.className])).toEqual([['risk normal', 'wcard-flag'], ['CI passing', 'wcard-flag']]);
  expect(face().querySelectorAll('.warn').length).toBe(0);
});

test('no flags and no note draw no lines; a failure note leads with the warning icon', () => {
  const { unmount } = show(card({ rework_count: 0, risk: undefined, pr_url: undefined }));
  expect(face().querySelector('.wcard-flags')).toBeNull();
  expect(face().querySelector('.wcard-note')).toBeNull();
  unmount();
  show(card({ status: 'failed', last_error: 'agent died', handoff: 'ignored' }));
  expect(texts('.wcard-note')).toEqual(['agent died']);
  expect(must(face().querySelector('.wcard-note svg')).getAttribute('class')).toContain('lucide-triangle-alert');
});

test('⋮ on a run: details, the PR, Linear, the branch, then the destructive ones after a separator', () => {
  show(card());
  expect(items()).toEqual(['Open details', 'Open PR #838', 'Open CMX-303 in Linear', 'liavacc/cmx-303-nightly-sweep', '---', 'Close & requeue', 'Delete']);
});

test('⋮ Open PR and Open in Linear open a new tab, no opener; the branch row copies it', async () => {
  const open = vi.spyOn(window, 'open').mockReturnValue(null);
  const writeText = vi.fn(() => Promise.resolve());
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  show(card());
  fireEvent.click(item('Open PR #838'));
  fireEvent.click(item('Open CMX-303 in Linear'));
  expect(open.mock.calls).toEqual([['https://github.com/o/r/pull/838', '_blank', 'noopener,noreferrer'], ['https://linear.app/x/issue/CMX-303', '_blank', 'noopener,noreferrer']]);
  fireEvent.click(item('liavacc/cmx-303-nightly-sweep'));
  expect(writeText).toHaveBeenCalledWith('liavacc/cmx-303-nightly-sweep');
  expect(modalOpen()).toBe(false);
});

test('⋮ Delete asks on the card first: Cancel posts nothing; Delete posts the run', async () => {
  show(card());
  fireEvent.click(item('Delete'));
  expect(face().querySelector('.kanban-confirm-msg')?.textContent).toBe('Delete this?');
  expect(item('Delete').hasAttribute('data-disabled')).toBe(true);
  fireEvent.keyDown(menu(), { key: 'Escape' });
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(face().querySelector('.kanban-confirm')).toBeNull();
  expect(posts()).toEqual([]);
  fireEvent.click(item('Delete'));
  fireEvent.keyDown(menu(), { key: 'Escape' });
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Delete' })));
  expect(posts()).toEqual([['/api/dispatcher/delete', { kind: 'run', task_id: 'CMX-303' }]]);
  expect(modalOpen()).toBe(false);
});

test('a refused delete shows the server\'s words on the card', async () => {
  reply = () => ({ status: 409, body: { ok: false, error: 'run is live' } });
  show(card());
  fireEvent.click(item('Delete'));
  fireEvent.keyDown(menu(), { key: 'Escape' });
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Delete' })));
  expect(face().querySelector('.kanban-confirm-msg')?.textContent).toBe('run is live');
});

test('⋮ Close & requeue asks why: a blank answer posts nothing, a reason posts it; a failure stays on the card', async () => {
  const ask = vi.spyOn(window, 'prompt').mockReturnValue('   ');
  show(card());
  fireEvent.click(item('Close & requeue'));
  expect(posts()).toEqual([]);
  ask.mockReturnValue(' stuck ');
  let row = item('Close & requeue');
  await act(async () => fireEvent.click(row));
  expect(posts()).toEqual([['/api/dispatcher/runs/CMX-303/close', { reason: 'stuck', requeue: true }]]);
  reply = () => ({ status: 500, body: { ok: false, error: 'tracker down' } });
  row = item('Close & requeue');
  await act(async () => fireEvent.click(row));
  expect(must(face().querySelector('[role="alert"]')).textContent).toBe('tracker down');
});

test('an action in flight shows on the card and disables the others', async () => {
  vi.spyOn(window, 'prompt').mockReturnValue('why');
  let release: (v: unknown) => void = () => {};
  vi.stubGlobal('fetch', () => new Promise((r) => (release = r)));
  show(card());
  const requeue = item('Close & requeue');
  act(() => fireEvent.click(requeue));
  expect(must(face().querySelector('[role="status"]')).textContent).toBe('Working…');
  expect(['Close & requeue', 'Delete'].map((l) => item(l).hasAttribute('data-disabled'))).toEqual([true, true]);
  expect(item('Open details').hasAttribute('data-disabled')).toBe(false);
  await act(async () => release({ ok: true, status: 200, json: () => Promise.resolve({ ok: true }) }));
  expect(face().querySelector('[role="status"]')).toBeNull();
});

test('⋮ Merge only on a reviewed PR whose CI passed, after a confirm', async () => {
  const ask = vi.spyOn(window, 'confirm').mockReturnValue(false);
  const { unmount } = show(card({ status: 'awaiting_review', pr_checks: 'failing' }));
  expect(items()).not.toContain('Merge PR #838');
  unmount();
  show(card({ status: 'awaiting_review', pr_checks: 'passing' }));
  fireEvent.click(item('Merge PR #838'));
  expect([ask.mock.calls[0]?.[0], posts()]).toEqual(['Squash-merge PR #838 and delete branch?', []]);
  ask.mockReturnValue(true);
  const merge = item('Merge PR #838');
  await act(async () => fireEvent.click(merge));
  expect(posts()).toEqual([['/api/dispatcher/runs/CMX-303/merge', undefined]]);
});

test('a backlog bullet: state "backlog", ⋮ Promote (asks) and Delete its line; a parked one has its reason and no Delete', async () => {
  const ask = vi.spyOn(window, 'confirm').mockReturnValue(true);
  const bullet = { status: 'backlog', title: 'an idea', file: '/p/BACKLOG.md', workflow_path: '/p/WORKFLOW.md' } as Card;
  const { unmount } = show(bullet);
  expect(texts('.wcard-meta > span')).toEqual(['backlog']);
  expect(items()).toEqual(['Open details', 'Promote to TODO', '---', 'Delete']);
  const promote = item('Promote to TODO');
  await act(async () => fireEvent.click(promote));
  expect(ask.mock.calls[0]?.[0]).toContain('Promote backlog item to TODO?');
  expect(posts()).toEqual([['/api/dispatcher/backlog/promote', { workflow_path: '/p/WORKFLOW.md', text: 'an idea' }]]);
  unmount();
  show({ status: 'parked', title: 'blocked one', reason: 'waits on CMX-1', file: '/p/TODO.md', line_number: 4 } as Card);
  expect(texts('.wcard-meta > span')).toEqual(['parked']);
  expect(texts('.wcard-note')).toEqual(['waits on CMX-1']);
  expect(items()).toEqual(['Open details', 'TODO.md:4']);
});

test('a tap or Enter on the card opens the task; the ⋮ (click or Enter) never does', () => {
  show(card());
  const trigger = must(face().querySelector<HTMLElement>('.wcard-menu-btn'));
  fireEvent.click(trigger);
  fireEvent.keyDown(trigger, { key: 'Enter' });
  expect(modalOpen()).toBe(false);
  fireEvent.keyDown(menu(), { key: 'Escape' });
  fireEvent.keyDown(face(), { key: 'x' });
  expect(modalOpen()).toBe(false);
  fireEvent.keyDown(face(), { key: 'Enter' });
  expect(modalOpen()).toBe(true);
  act(() => closeTaskModal());
  fireEvent.click(face());
  expect(modalOpen()).toBe(true);
});

test('⋮ Open details opens the task', () => {
  show(card());
  fireEvent.click(item('Open details'));
  expect(modalOpen()).toBe(true);
});

test('a Rows row: one-line title, id, PR and ONE flag (every warning, or the state and CI)', () => {
  const { unmount } = show(card(), RowItem);
  expect(texts('.wrow-title')).toEqual(['Nightly sweep 1/3']);
  expect([...face().querySelectorAll('.wrow-meta > span')].map((s) => [s.textContent, s.className])).toEqual([
    ['CMX-303', 'wrow-id'], ['#838', 'wrow-pr'], ['rework 1 · risk high · CI failing', 'wrow-flag warn'],
  ]);
  expect([...face().querySelectorAll('button')].map((b) => b.getAttribute('aria-label'))).toEqual(['Card actions']);
  unmount();
  show(card({ rework_count: 0, risk: 'normal', pr_checks: 'pending' }), RowItem);
  expect([...face().querySelectorAll('.wrow-flag')].map((s) => [s.textContent, s.className])).toEqual([['running · risk normal · CI pending', 'wrow-flag']]);
  fireEvent.keyDown(face(), { key: ' ' });
  expect(modalOpen()).toBe(true);
});

test('a row with nothing to flag draws no flag', () => {
  show({ status: 'open', task_id: 'CMX-9', title: 'plain' } as Card, RowItem);
  expect(texts('.wrow-meta > span')).toEqual(['CMX-9']);
});
