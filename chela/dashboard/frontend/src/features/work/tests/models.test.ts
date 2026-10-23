// The Work board's pure models (CMX-233; ported from tests/kanban_lane_model.test.mjs,
// tests/kanban_linear_model.test.mjs, the model half of tests/kanban_hidden_columns.test.mjs,
// and the flatten/filter/badge halves of kanban_flatten.test.mjs and dispatch_hold.test.mjs).
import { expect, test } from 'vitest';
import { BUCKET_ORDER, DONE_LIMIT, FILTER_KEY, STATUS_CHIPS, boardColumns, branchOrLine, flatten, inFilter, lanesFromBuckets, loadFilter, mergeControl, pruneFilter, toggleProject, workBadgeCounts, type Card } from '../board.ts';
import { COL_PREFS_KEY, hiddenSummary, loadColPrefs, saveColPrefs, splitColumns } from '../hidden.ts';
import { KANBAN_LANES, KANBAN_LANE_LABELS, laneOf } from '../lanes.ts';
import { UNMAPPED_KEY, columnKey, columnOf, runStateBadges, sampledLabel, trackerBoard, trackerColumns } from '../linear.ts';

const ALL_STATUSES = ['backlog', 'parked', 'open', 'claimed', 'running', 'awaiting_review', 'changes_requested', 'needs_human', 'failed', 'done', 'closed'];
const LINEAR = [
  { name: 'Backlog', type: 'backlog', hidden: false },
  { name: 'Todo', type: 'unstarted', hidden: false },
  { name: 'In Progress', type: 'started', hidden: false },
  { name: 'In Review', type: 'started', hidden: false },
  { name: 'Done', type: 'completed', hidden: false },
  { name: 'Canceled', type: 'canceled', hidden: true },
  { name: 'Duplicate', type: 'duplicate', hidden: true },
];

// --- lanes.ts -------------------------------------------------------------------------------

test('laneOf: every known status lands on its own lane', () => {
  const want = {
    backlog: 'backlog', parked: 'backlog', open: 'todo', claimed: 'in_progress', running: 'in_progress', awaiting_review: 'review',
    changes_requested: 'review', needs_human: 'review', failed: 'review', done: 'done', closed: 'archived',
  };
  expect(Object.fromEntries(ALL_STATUSES.map((s) => [s, laneOf(s)]))).toEqual(want);
});

test('laneOf: an unknown status falls back to review, never hidden in backlog/done', () => {
  for (const s of ['some_unknown_status', '', undefined, null, 'toString', '__proto__']) expect(laneOf(s)).toBe('review');
});

test('the 6 lanes, in order, each labelled; Archived reads differently from Done; every lane is reachable', () => {
  expect(KANBAN_LANES).toEqual(['backlog', 'todo', 'in_progress', 'review', 'done', 'archived']);
  expect(KANBAN_LANES.map((k) => KANBAN_LANE_LABELS[k])).toEqual(['Backlog', 'To Do', 'In Progress', 'Review', 'Done', 'Archived']);
  expect(new Set(ALL_STATUSES.map(laneOf))).toEqual(new Set(KANBAN_LANES));
});

// --- linear.ts ------------------------------------------------------------------------------

const labels = (cols: { label: string }[]) => cols.map((c) => c.label);

test('columns are the tracker states, in its order, with Canceled/Duplicate hidden', () => {
  const cols = trackerColumns([{ tracker_columns: LINEAR }]) ?? [];
  expect(labels(trackerBoard(cols, []).columns)).toEqual(['Backlog', 'Todo', 'In Progress', 'In Review', 'Done']);
});

test('only workflows without tracker states fall back to chela lanes (null)', () => {
  expect(trackerColumns([{ tracker_columns: null }])).toBeNull();
  expect(trackerColumns([{ tracker_columns: null }, {}])).toBeNull();
  expect(trackerColumns([{ tracker_columns: [] }])).toBeNull();
  expect(trackerColumns([{ tracker_columns: [null, { name: '' }, { type: 'started' }] }])).toBeNull();
  expect(trackerColumns([])).toBeNull();
  expect(trackerColumns(null)).toBeNull();
  expect(trackerColumns([{ tracker_columns: LINEAR }])).not.toBeNull();
});

test('CMX-193: a markdown workflow next to a Linear one keeps Linear\'s columns, in either order', () => {
  for (const wfs of [[{ tracker_columns: LINEAR }, { tracker_columns: null }], [{}, { tracker_columns: LINEAR }]]) {
    expect(trackerColumns(wfs)?.map((c) => c.name)).toEqual(LINEAR.map((c) => c.name));
  }
  const board = trackerBoard(LINEAR, [{ status: 'open', tracker_state: 'Backlog' }, { status: 'open' }, { status: 'backlog' }]);
  expect(board.columns.find((c) => c.label === 'Backlog')?.cards).toHaveLength(2);
  expect(board.columns.find((c) => c.label === 'Todo')?.cards).toHaveLength(1);
});

test('several Linear workflows: the first one\'s order, each state ONCE (any casing), extras appended; types and hidden kept', () => {
  const other = [{ name: 'todo', type: 'unstarted', hidden: false }, { name: 'QA', type: 'started' }];
  const cols = trackerColumns([{ tracker_columns: LINEAR }, { tracker_columns: other }]) ?? [];
  expect(cols.map((c) => c.name)).toEqual([...LINEAR.map((c) => c.name), 'QA']);
  expect(cols.at(-1)).toEqual({ name: 'QA', type: 'started', hidden: false });
  expect(cols.find((c) => c.name === 'Canceled')).toEqual({ name: 'Canceled', type: 'canceled', hidden: true });
});

test('the tracker state wins over the run status; the status is only the fallback', () => {
  expect(columnOf({ status: 'running', tracker_state: 'in review' }, LINEAR)?.name).toBe('In Review');
  const want = {
    backlog: 'Backlog', parked: 'Backlog', open: 'Todo', claimed: 'In Progress', running: 'In Progress', failed: 'In Progress',
    awaiting_review: 'In Review', changes_requested: 'In Progress', needs_human: 'In Review', done: 'Done', closed: 'Canceled',
  };
  for (const [status, col] of Object.entries(want)) expect(columnOf({ status }, LINEAR)?.name, status).toBe(col);
  // A died run whose PR is open was in review: it stays there.
  expect(columnOf({ status: 'failed', pr_url: 'https://x/pull/2' }, LINEAR)?.name).toBe('In Review');
  // An unknown state falls to the status; an unknown status maps nowhere, never to a guess.
  expect(columnOf({ status: 'open', tracker_state: 'Triage' }, LINEAR)?.name).toBe('Todo');
  expect(columnOf({ status: 'mystery' }, LINEAR)).toBeNull();
  expect(columnOf({ status: 'toString' }, LINEAR)).toBeNull();
  expect(columnOf({ status: 'open' }, LINEAR.filter((c) => c.type !== 'unstarted'))).toBeNull();
});

test('a closed run lands in the hidden Canceled column, counted not shown; an unmapped card is never dropped', () => {
  const board = trackerBoard(LINEAR, [{ status: 'closed' }, { status: 'done' }]);
  expect(board.hiddenCount).toBe(1);
  expect(board.columns.find((c) => c.label === 'Done')?.cards).toHaveLength(1);
  const un = trackerBoard(LINEAR, [{ status: 'mystery', tracker_state: 'Triage' }]).columns.at(-1);
  expect(un).toEqual({ key: UNMAPPED_KEY, label: 'Unmapped', cards: [{ status: 'mystery', tracker_state: 'Triage' }] });
  expect(trackerBoard(LINEAR, [{ status: 'open' }]).columns.some((c) => c.key === UNMAPPED_KEY)).toBe(false);
});

test('columnKey and sampledLabel', () => {
  expect(columnKey('In Review')).toBe('tr-in-review');
  expect(columnKey(' <b>QA</b> ')).toBe('tr-b-qa-b');
  expect(sampledLabel('Done', true)).toBe('Done · latest');
  expect(sampledLabel('Done', false)).toBe('Done');
});

test('run state rides as badges (a word and an icon): judging, blocked race, rework N from the first round', () => {
  expect(runStateBadges({ judge_state: 'running', rework_count: 2 })).toEqual([
    { label: 'judging', cls: 'st-judging', icon: 'judging' },
    { label: 'rework 2', cls: 'st-rework', icon: 'retry' },
  ]);
  expect(runStateBadges({ judge_state: 'blocked_race' })).toEqual([{ label: 'blocked race', cls: 'st-blocked-race', icon: 'blockedRace' }]);
  expect(runStateBadges({ judge_state: 'clean', rework_count: 1 }).map((b) => b.label)).toEqual(['rework 1']);
  expect(runStateBadges({ judge_state: 'clean', rework_count: 0 })).toEqual([]);
  expect(runStateBadges(null)).toEqual([]);
});

test('CMX-318: a parked run carries its reason and since-when; a slot wait reads as waiting', () => {
  const now = 10_000;
  expect(runStateBadges({ park: { kind: 'manual', reason: 'waiting for the flaky fix', since: now - 7200, until: 'run:CMX-9', by: 'orchestrator' } }, now)).toEqual([
    { label: 'parked 2h ago: waiting for the flaky fix', cls: 'st-parked', icon: 'pause', title: 'waiting for the flaky fix · until run:CMX-9 · by orchestrator' },
  ]);
  expect(runStateBadges({ park: { kind: 'slot', reason: 'waiting for a suite slot (holder: CMX-7)', since: now - 300 } }, now)).toEqual([
    { label: 'waiting 5m ago: waiting for a suite slot (holder: CMX-7)', cls: 'st-slot-wait', icon: 'slotWait', title: 'waiting for a suite slot (holder: CMX-7)' },
  ]);
  const long = 'x'.repeat(60);
  expect(runStateBadges({ park: { kind: 'manual', reason: long, since: null } }, now)[0]?.label).toBe(`parked since ?: ${'x'.repeat(47)}…`);
  expect(runStateBadges({ park: null }, now)).toEqual([]);
});

// --- hidden.ts ------------------------------------------------------------------------------

const col = (key: string, n: number) => ({ key, label: key.toUpperCase(), cards: Array.from({ length: n }) });

test('splitColumns: empty → hidden, carded → visible, both in board order; a pin keeps an empty one', () => {
  const { visible, hidden } = splitColumns([col('a', 0), col('b', 2), col('c', 0), col('d', 1)], {});
  expect([visible.map((c) => c.key), hidden.map((c) => c.key)]).toEqual([['b', 'd'], ['a', 'c']]);
  const pinned = splitColumns([col('a', 0), col('b', 2), col('c', 0)], { c: 'show' });
  expect([pinned.visible.map((c) => c.key), pinned.hidden.map((c) => c.key)]).toEqual([['b', 'c'], ['a']]);
});

test('loadColPrefs: only "show" survives; throwing storage, junk JSON and arrays are the default {}', () => {
  const st = (v: string | null) => ({ getItem: () => v, setItem: () => {} });
  expect(COL_PREFS_KEY).toBe('chela_kanban_colprefs');
  expect(loadColPrefs({ getItem: (k) => (k === 'chela_kanban_colprefs' ? '{"a":"show","b":"hide","c":1}' : null), setItem: () => {} })).toEqual({ a: 'show' });
  expect(loadColPrefs({ getItem: () => { throw new RangeError('denied'); }, setItem: () => {} })).toEqual({});
  for (const v of ['{not json', '["show"]', 'null', null]) expect(loadColPrefs(st(v))).toEqual({});
});

test('saveColPrefs writes the literal key and reports a refused write', () => {
  const mem: Record<string, string> = {};
  expect(saveColPrefs({ getItem: () => null, setItem: (k, v) => { mem[k] = v; } }, { a: 'show' })).toBe(true);
  expect(mem).toEqual({ chela_kanban_colprefs: '{"a":"show"}' });
  expect(saveColPrefs({ getItem: () => null, setItem: () => { throw new TypeError('quota'); } }, {})).toBe(false);
});

test('hiddenSummary is the phone one-liner; nothing hidden → empty', () => {
  expect(hiddenSummary([col('backlog', 0), col('review', 0)])).toBe('Hidden: BACKLOG · REVIEW');
  expect(hiddenSummary([])).toBe('');
});

// --- board.ts -------------------------------------------------------------------------------

const wf = (extra = {}) => ({ path: '/x/WORKFLOW.md', project_key: 'CMX', ...extra });

test('flatten: every source lands in its bucket with its workflow chip, and a run keeps its own status', () => {
  const b = flatten({
    workflows: [wf({
      backlog_items: [{ text: 'idea', section: 'Later', file: 'BACKLOG.md' }],
      parked_tasks: [{ id: 'p1', title: 'parked', file: 'TODO.md', line_number: 3, raw: '- [ ] parked <!-- blocked: x -->', reason: 'x' }],
      open_tasks: [{ id: 'o1', title: 'open', file: 'TODO.md', line_number: 4, raw: 'r', body: 'b', tracker_url: 'https://l/1', tracker_state: 'Todo', closed_run_stall: 's' }],
      active_runs: [{ task_id: 'r1', status: 'running' }, { task_id: 'c1', status: 'claimed' }, { task_id: 'q1', status: 'weird' }],
      awaiting_review_runs: [{ task_id: 'a1', status: 'changes_requested' }, { task_id: 'a2' }],
      recent_runs: [{ task_id: 'd1', status: 'done' }, { task_id: 'f1', status: 'failed' }, { task_id: 'x1', status: 'closed' }, { task_id: 'u1', status: 'mystery' }],
    })],
  });
  const ids = (k: keyof typeof b) => b[k].map((c) => c.task_id ?? c.title);
  expect(BUCKET_ORDER.map((k) => [k, ids(k)])).toEqual([
    ['backlog', ['idea']], ['parked', ['p1']], ['open', ['o1']], ['running', ['r1']], ['claimed', ['c1', 'q1']],
    ['awaiting_review', ['a1', 'a2']], ['failed', ['f1']], ['done', ['d1', 'u1']], ['closed', ['x1']],
  ]);
  expect(b.awaiting_review.map((c) => c.status)).toEqual(['changes_requested', 'awaiting_review']);
  expect(b.closed[0]?.status).toBe('closed');
  expect(b.backlog[0]).toEqual({ status: 'backlog', title: 'idea', section: 'Later', file: 'BACKLOG.md', workflow_path: '/x/WORKFLOW.md', project_key: 'CMX' });
  expect(b.parked[0]).toMatchObject({ status: 'parked', task_id: 'p1', raw: '- [ ] parked <!-- blocked: x -->', reason: 'x', line_number: 3, workflow_path: '/x/WORKFLOW.md' });
  expect(b.open[0]).toMatchObject({ status: 'open', task_id: 'o1', raw: 'r', body: 'b', tracker_url: 'https://l/1', tracker_state: 'Todo', closed_run_stall: 's', workflow_path: '/x/WORKFLOW.md' });
});

test('flatten: run columns newest first; Done and Archived capped at DONE_LIMIT', () => {
  const runs = Array.from({ length: 25 }, (_, i) => ({ task_id: `d${i}`, status: 'done', ended_at: `2026-10-${String(i + 1).padStart(2, '0')}` }));
  const b = flatten({ workflows: [wf({ recent_runs: [...runs, ...runs.map((r) => ({ ...r, task_id: `x${r.task_id}`, status: 'closed' }))],
    active_runs: [{ task_id: 'old', status: 'running', started_at: '2026-01-01' }, { task_id: 'new', status: 'running', started_at: '2026-02-01' }] })] });
  expect(DONE_LIMIT).toBe(20);
  expect(b.done.map((c) => c.task_id)).toEqual(runs.slice(5).reverse().map((r) => r.task_id));
  expect(b.closed).toHaveLength(20);
  expect(b.running.map((c) => c.task_id)).toEqual(['new', 'old']);
});

test('lanesFromBuckets: backlog before parked, running before claimed, review loop before failed', () => {
  const b = flatten({ workflows: [wf({ backlog_items: [{ text: 'b' }], parked_tasks: [{ id: 'p' }], active_runs: [{ task_id: 'c', status: 'claimed' }, { task_id: 'r', status: 'running' }],
    awaiting_review_runs: [{ task_id: 'a', status: 'awaiting_review' }], recent_runs: [{ task_id: 'f', status: 'failed' }] })] });
  const lanes = lanesFromBuckets(b);
  const ids = (cs: Card[]) => cs.map((c) => c.task_id ?? c.title);
  expect([ids(lanes.backlog), ids(lanes.in_progress), ids(lanes.review)]).toEqual([['b', 'p'], ['r', 'c'], ['a', 'f']]);
});

test('boardColumns: chela lanes, or Linear\'s columns; the filter keeps only selected projects; "latest" only when capped', () => {
  const a = wf({ path: '/a/WORKFLOW.md', open_tasks: [{ id: 'A-1' }] });
  const l = wf({ path: '/l/WORKFLOW.md', tracker_columns: LINEAR, open_tasks: [{ id: 'L-1', tracker_state: 'Backlog' }], recent_runs_capped: true });
  const cards = (cols: { label: string; cards: Card[] }[]) => Object.fromEntries(cols.filter((c) => c.cards.length).map((c) => [c.label, c.cards.map((x) => x.task_id)]));
  expect(cards(boardColumns({ workflows: [a, l] }, []))).toEqual({ Backlog: ['L-1'], Todo: ['A-1'] });
  expect(labels(boardColumns({ workflows: [a, l] }, ['/a/WORKFLOW.md']))).toEqual(['Backlog', 'To Do', 'In Progress', 'Review', 'Done', 'Archived']);
  expect(cards(boardColumns({ workflows: [a, l] }, ['/a/WORKFLOW.md']))).toEqual({ 'To Do': ['A-1'] });
  expect(labels(boardColumns({ workflows: [l] }, []))).toContain('Done · latest');
  expect(labels(boardColumns({ workflows: [{ ...a, recent_runs_capped: true }] }, []))[4]).toBe('Done · latest');
  expect(labels(boardColumns({ workflows: [a] }, []))[4]).toBe('Done');
});

test('the project filter (CMX-194): empty is every project; pills toggle; a vanished project is pruned', () => {
  expect(inFilter([], '/a')).toBe(true);
  expect(inFilter(['/a'], '/a')).toBe(true);
  expect(inFilter(['/a'], '/b')).toBe(false);
  expect(inFilter(['/a'], undefined)).toBe(false);
  expect(toggleProject([], '/a')).toEqual(['/a']);
  expect(toggleProject(['/a', '/b'], '/a')).toEqual(['/b']);
  const sel = ['/a', '/b'];
  expect(pruneFilter(sel, ['/a', '/b', '/c'])).toBe(sel);
  expect(pruneFilter(sel, ['/b'])).toEqual(['/b']);
  expect(FILTER_KEY).toBe('chela_kanban_projects');
  expect(loadFilter({ getItem: (k) => (k === 'chela_kanban_projects' ? '["/a",3,null,"/b"]' : null) })).toEqual(['/a', '/b']);
  for (const v of ['{"a":1}', 'junk', null]) expect(loadFilter({ getItem: () => v })).toEqual([]);
  expect(loadFilter({ getItem: () => { throw new Error('denied'); } })).toEqual([]);
});

test('STATUS_CHIPS: every status says itself in words; only `closed` says closed', () => {
  for (const s of ALL_STATUSES.filter((x) => x !== 'backlog' && x !== 'parked')) expect(STATUS_CHIPS[s]?.label, s).toMatch(/[a-z]{4,}/);
  expect(STATUS_CHIPS.closed?.label).toBe('closed, not merged');
  expect(STATUS_CHIPS.failed?.label).toBe('failed');
  for (const [s, c] of Object.entries(STATUS_CHIPS)) if (s !== 'closed') expect(c.label, s).not.toMatch(/closed|archiv/);
});

const card = (o: Partial<Card>): Card => ({ status: 'awaiting_review', task_id: 'CMX-1', pr_url: 'https://g/pull/1', ...o });

test('mergeControl: a button only on a green (or CI-less) open PR awaiting review', () => {
  expect(mergeControl(card({ pr_checks: 'passing' }))).toBe('button');
  expect(mergeControl(card({ pr_checks: 'none' }))).toBe('button');
  for (const c of ['failing', 'pending', 'unknown', undefined]) expect(mergeControl(card({ pr_checks: c })), String(c)).toBeNull();
  expect(mergeControl(card({ pr_state: 'merged', pr_checks: 'failing' }))).toBe('merged');
  expect(mergeControl(card({ pr_state: 'closed', pr_checks: 'passing' }))).toBeNull();
  expect(mergeControl(card({ status: 'changes_requested', pr_checks: 'passing' }))).toBeNull();
  expect(mergeControl(card({ pr_url: undefined, pr_checks: 'passing' }))).toBeNull();
  expect(mergeControl(card({ task_id: undefined, pr_checks: 'passing' }))).toBeNull();
});

test('branchOrLine: a branch only when it differs from the display id, else file:line', () => {
  expect(branchOrLine(card({ branch_name: 'cmx-1' }), 'CMX-1')).toBe('');
  expect(branchOrLine(card({ branch_name: 'dogfood/abc' }), 'CMX-1')).toBe('dogfood/abc');
  expect(branchOrLine(card({ file: '/r/TODO.md', line_number: 7 }), 'CMX-1')).toBe('TODO.md:7');
  expect(branchOrLine(card({}), 'CMX-1')).toBe('');
});

test('workBadgeCounts: active runs and awaiting-review PRs across workflows', () => {
  const w = wf({ active_runs: [{}, {}], awaiting_review_runs: [{}] });
  expect(workBadgeCounts({ workflows: [w, w] })).toEqual({ runs: 4, prs: 2 });
  expect(workBadgeCounts(null)).toEqual({ runs: 0, prs: 0 });
});
