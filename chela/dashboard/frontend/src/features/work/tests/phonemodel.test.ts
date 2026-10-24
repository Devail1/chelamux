// CMX-315: the phone Work view's pure model (phone.ts).
import { expect, test } from 'vitest';
import type { WorkPayload, Workflow } from '../../../shared/store/work.ts';
import type { Card, Column } from '../board.ts';
import { barSubtitle, cardFlags, cardIds, cardNote, cardState, countsLine, loadRecent, projectCounts, projectRows, projectTitle, rowFlag, rowGroups, SEARCH_AFTER, RECENT_KEY, totalCounts, touchRecent } from '../phone.ts';

const wf = (path: string, o: Partial<Workflow> = {}): Workflow => ({ path, ...o });
const A = wf('/p/alpha/WORKFLOW.md', {
  active_runs: [{ status: 'running' }, { status: 'claimed' }],
  open_tasks: [{ id: 'A-1' }, { id: 'A-2' }, { id: 'A-3' }],
  awaiting_review_runs: [{ status: 'needs_human' }, { status: 'awaiting_review' }],
  recent_runs: [{ status: 'needs_human' }, { status: 'done' }],
});
const B = wf('/p/beta/WORKFLOW.md', { open_tasks: [{ id: 'B-1' }] });
const C = wf('/p/gamma/WORKFLOW.md');
const DATA: WorkPayload = { configured: true, workflows: [A, B, C] };

test('a project\'s counts: runs in flight, open tasks, and every needs_human run wherever it is filed', () => {
  expect(projectCounts(A)).toEqual({ running: 2, queued: 3, needsYou: 2 });
  expect(projectCounts(C)).toEqual({ running: 0, queued: 0, needsYou: 0 });
  expect(projectCounts(wf('/x', { active_runs: [null as never] }))).toEqual({ running: 1, queued: 0, needsYou: 0 });
});

test('the totals follow the filter; empty is every project', () => {
  expect(totalCounts(DATA, [])).toEqual({ running: 2, queued: 4, needsYou: 2 });
  expect(totalCounts(DATA, [B.path])).toEqual({ running: 0, queued: 1, needsYou: 0 });
  expect(totalCounts(null, [])).toEqual({ running: 0, queued: 0, needsYou: 0 });
});

test('a counts line, or "nothing queued" when both are zero', () => {
  expect(countsLine({ running: 2, queued: 15, needsYou: 0 })).toBe('2 running · 15 queued');
  expect(countsLine({ running: 1, queued: 0, needsYou: 0 })).toBe('1 running · 0 queued');
  expect(countsLine({ running: 0, queued: 1, needsYou: 0 })).toBe('0 running · 1 queued');
  expect(countsLine({ running: 0, queued: 0, needsYou: 3 })).toBe('nothing queued');
});

test('the bar\'s subtitle: Work, the counts once a poll landed, then a held queue or a paused fleet', () => {
  expect(barSubtitle(null, [])).toBe('Work');
  expect(barSubtitle(DATA, [])).toBe('Work · 2 running · 4 queued');
  expect(barSubtitle({ ...DATA, workflows: [] }, [])).toBe('Work · 0 running · 0 queued');
  expect(barSubtitle({ ...DATA, dispatch_hold: {} }, [B.path])).toBe('Work · 0 running · 1 queued · dispatch paused');
  expect(barSubtitle({ ...DATA, dispatch_hold: {}, fleet_pause: {} }, [])).toBe('Work · 2 running · 4 queued · agents paused');
});

test('the bar\'s title: All projects, the one project, or how many', () => {
  expect(projectTitle([])).toBe('All projects');
  expect(projectTitle(['/p/chelamux/WORKFLOW.md'])).toBe('chelamux');
  expect(projectTitle([''])).toBe('?');
  expect(projectTitle(['/a/W.md', '/b/W.md', '/c/W.md'])).toBe('3 projects');
});

test('the recent list: read back newest first; anything unreadable is none; a pick moves to the front, capped at 20', () => {
  const store = (v: string | null, boom = false) => ({ getItem: (k: string) => { if (boom) throw new Error('denied'); return k === RECENT_KEY ? v : null; } });
  expect(RECENT_KEY).toBe('chela_work_recent_projects');
  expect(loadRecent(store('["/a","/b",3]'))).toEqual(['/a', '/b']);
  for (const bad of [store(null), store('{bad'), store('{"a":1}'), store('x', true)]) expect(loadRecent(bad)).toEqual([]);
  expect(touchRecent(['/a', '/b', '/c'], '/c')).toEqual(['/c', '/a', '/b']);
  expect(touchRecent([], '/a')).toEqual(['/a']);
  const many = Array.from({ length: 25 }, (_, i) => `/p${i}`);
  expect(touchRecent(many, '/new')).toEqual(['/new', ...many.slice(0, 19)]);
});

test('the switcher\'s rows: recent first (newest first), the rest in payload order; a search keeps the names containing it', () => {
  expect(SEARCH_AFTER).toBe(6);
  const row = (name: string) => projectRows(DATA, []).find((r) => r.name === name);
  expect(projectRows(DATA, []).map((r) => r.name)).toEqual(['alpha', 'beta', 'gamma']);
  expect(row('alpha')).toEqual({ path: A.path, name: 'alpha', counts: '2 running · 3 queued', needsYou: 2 });
  expect(row('gamma')?.counts).toBe('nothing queued');
  expect(projectRows(DATA, [C.path, '/gone', B.path]).map((r) => r.name)).toEqual(['gamma', 'beta', 'alpha']);
  expect(projectRows(DATA, [], '  ETA ').map((r) => r.name)).toEqual(['beta']);
  expect(projectRows(DATA, [], 'a').map((r) => r.name)).toEqual(['alpha', 'beta', 'gamma']);
  expect(projectRows(null, [])).toEqual([]);
  expect(projectRows({ workflows: [wf('W')] }, []).map((r) => r.name)).toEqual(['W']);
});

const col = (key: string, n = 1): Column => ({ key, label: key, cards: Array.from({ length: n }, () => ({ status: 'open' }) as Card) });

test('Rows reads in-flight work first, then review, the queue, the backlog, anything else, done, then archived; empty groups drop', () => {
  const lanes = ['backlog', 'todo', 'in_progress', 'review', 'done', 'archived'].map((k) => col(k));
  expect(rowGroups(lanes).map((c) => c.key)).toEqual(['in_progress', 'review', 'todo', 'backlog', 'done', 'archived']);
  const linear = ['tr-backlog', 'tr-todo', 'tr-ready', 'tr-in-progress', 'tr-in-review', 'tr-done', 'tr-completed', 'tr-canceled', 'tr-duplicate', 'tr-unmapped', 'tr-qa'].map((k) => col(k));
  expect(rowGroups(linear).map((c) => c.key)).toEqual(['tr-in-progress', 'tr-in-review', 'tr-todo', 'tr-ready', 'tr-backlog', 'tr-unmapped', 'tr-qa', 'tr-done', 'tr-completed', 'tr-canceled', 'tr-duplicate']);
  expect(rowGroups([col('todo', 0), col('review', 2)]).map((c) => c.key)).toEqual(['review']);
  expect(rowGroups([])).toEqual([]);
});

const card = (o: Partial<Card> & Record<string, unknown>): Card => ({ status: 'running', ...o }) as Card;

test('a card\'s flags: rework, risk, then CI only with a PR; warn only for rework, risk high, CI failing', () => {
  expect(cardFlags(card({ rework_count: 2, risk: 'HIGH', pr_url: 'u', pr_checks: 'failing' }))).toEqual([
    { label: 'rework 2', warn: true, icon: 'rework' }, { label: 'risk high', warn: true }, { label: 'CI failing', warn: true, icon: 'ciFailing' },
  ]);
  expect(cardFlags(card({ rework_count: 0, risk: 'normal', pr_url: 'u', pr_checks: 'passing' }))).toEqual([{ label: 'risk normal', warn: false }, { label: 'CI passing', warn: false, icon: 'ciPassing' }]);
  expect(cardFlags(card({ risk: 'low', pr_url: 'u', pr_checks: 'pending' }))).toEqual([{ label: 'risk low', warn: false }, { label: 'CI pending', warn: false, icon: 'ciPending' }]);
  expect(cardFlags(card({ pr_url: 'u', pr_checks: 'none' }))).toEqual([{ label: 'no CI', warn: false, icon: 'ciNone' }]);
  expect(cardFlags(card({ pr_url: 'u', pr_checks: 'weird' }))).toEqual([{ label: 'CI unknown', warn: false, icon: 'ciUnknown' }]);
  expect(cardFlags(card({ rework_count: 'x' as unknown as number, risk: 'extreme', pr_checks: 'failing' }))).toEqual([]);
  expect(cardFlags(card({ risk: 7 }))).toEqual([]);
});

test('a card\'s state word and icon', () => {
  expect(cardState(card({ status: 'backlog' }))).toEqual({ label: 'backlog', icon: 'statusOpen' });
  expect(cardState(card({ status: 'parked' }))).toEqual({ label: 'parked', icon: 'parked' });
  expect(cardState(card({ status: 'needs_human' }))).toEqual({ label: 'needs a human', icon: 'needsHuman' });
  expect(cardState(card({ status: 'awaiting_review' }))).toEqual({ label: 'awaiting review', icon: 'awaitingReview' });
  expect(cardState(card({ status: 'some_new_state' }))).toEqual({ label: 'some new state', icon: 'ciUnknown' });
  expect(cardState(card({ status: 'toString' }))).toEqual({ label: 'toString', icon: 'ciUnknown' });
});

test('a row\'s one flag: every warning; else a run\'s state and its other flags; a queued task its risk', () => {
  expect(rowFlag(card({ rework_count: 1, risk: 'high', pr_url: 'u', pr_checks: 'passing' }))).toEqual({ label: 'rework 1 · risk high', warn: true });
  expect(rowFlag(card({ pr_url: 'u', pr_checks: 'pending' }))).toEqual({ label: 'running · CI pending', warn: false });
  expect(rowFlag(card({ status: 'open', risk: 'normal' }))).toEqual({ label: 'risk normal', warn: false });
  for (const status of ['open', 'backlog', 'parked']) expect(rowFlag(card({ status }))).toEqual({ label: '', warn: false });
  expect(rowFlag(card({ status: 'done' }))).toEqual({ label: 'done', warn: false });
});

test('a card\'s id and PR label', () => {
  expect(cardIds(card({ project_key: 'CMX', task_number: 7, task_id: 'abc', pr_url: 'https://github.com/o/r/pull/12' }))).toEqual({ id: 'CMX-7', pr: '#12' });
  expect(cardIds(card({ task_id: 'abc' }))).toEqual({ id: 'abc', pr: '' });
  expect(cardIds(card({}))).toEqual({ id: '', pr: '' });
});

test('a card\'s one note: a failure (warns) or close reason first, then a stall (warns), the handoff, a parked reason', () => {
  const all = { last_error: 'boom', closed_run_stall: 'stalled', handoff: 'look here', reason: 'blocked' };
  expect(cardNote(card(all))).toEqual({ text: 'boom', warn: true });
  expect(cardNote(card({ ...all, status: 'closed', close_reason: 'dup' }))).toEqual({ text: 'Closed: dup', warn: false });
  expect(cardNote(card({ ...all, last_error: undefined }))).toEqual({ text: 'stalled', warn: true });
  expect(cardNote(card({ handoff: ' look\nhere ' }))).toEqual({ text: 'look here', warn: false });
  expect(cardNote(card({ status: 'parked', reason: 'blocked' }))).toEqual({ text: 'blocked', warn: false });
  expect(cardNote(card({ status: 'running', reason: 'blocked' }))).toBeNull();
  expect(cardNote(card({ status: 'parked' }))).toBeNull();
});

test('CMX-318: a held run\'s flag (first, never a warning) carries its reason and since-when; its state stays its own; no stall note', () => {
  const now = 10_000;
  const manual = { kind: 'manual' as const, reason: 'waiting for the flaky fix', since: now - 7200, until: 'run:CMX-9', by: 'orchestrator' };
  const slot = { kind: 'slot' as const, reason: 'waiting for a suite slot (holder: CMX-7)', since: now - 300 };
  expect(cardFlags(card({ park: manual, rework_count: 1 }), now)).toEqual([
    { label: 'parked · waiting for the flaky fix · 2h ago', warn: false, icon: 'pause', title: 'waiting for the flaky fix · 2h ago · until run:CMX-9 · by orchestrator' },
    { label: 'rework 1', warn: true, icon: 'rework' },
  ]);
  expect(cardFlags(card({ park: slot }), now)).toEqual([
    { label: 'waiting · waiting for a suite slot (holder: CMX-7) · 5m ago', warn: false, icon: 'slotWait', title: 'waiting for a suite slot (holder: CMX-7) · 5m ago' },
  ]);
  expect(cardFlags(card({ park: { kind: 'manual', reason: 'x'.repeat(60), since: null } }), now)[0]?.label).toBe(`parked · ${'x'.repeat(39)}… · since ?`);
  expect(cardFlags(card({ park: null }), now)).toEqual([]);
  expect(cardState(card({ park: manual }))).toEqual(cardState(card({})));
  expect(cardNote(card({ park: manual, closed_run_stall: 'stalled' }))).toBeNull();
  expect(cardNote(card({ closed_run_stall: 'stalled' }))).toEqual({ text: 'stalled', warn: true });
  expect(cardNote(card({ park: manual, last_error: 'boom' }))).toEqual({ text: 'boom', warn: true });
  expect(rowFlag(card({ park: manual }))).toEqual({ label: expect.stringContaining('parked · waiting for the flaky fix'), warn: false });
});
