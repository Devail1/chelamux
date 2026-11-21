// CMX-434: the "Suggest names" dialog's pure half: who is listed, the rows, the inline checks,
// and the apply, which reports each half of each row on its own.
import { expect, test } from 'vitest';
import type { AgentRow } from '../../sidebar/model.ts';
import { type Row, type Suggestion, applyRows, candidateWids, initialRows, outcomeFailed, outcomeText, postTitle, rowProblems, titleNote } from '../model.ts';

const ag = (wid: string, name: string, over: Partial<AgentRow> = {}): AgentRow =>
  ({ window_id: wid, name, claude_running: true, ...over });

test('candidateWids: live Claude sessions; dispatched runs and judges only with the toggle', () => {
  const agents = [
    ag('@1', 'orch'),
    ag('@2', 'shell', { claude_running: false }),
    ag('@3', 'cmx-1', { dispatched: true }),
    ag('@4', 'judge-cmx-1', {}),
    ag('@5', 'moved', { session_moved: true }),
    ag('@6', 'run', { run: { task_id: 'CMX-2', role: 'agent' } }),
    ag('@7', 'unknown', { claude_running: undefined }),
    { name: 'no-wid', claude_running: true },
  ];
  expect(candidateWids(agents, false)).toEqual(['@1', '@5']);
  expect(candidateWids(agents, true)).toEqual(['@1', '@3', '@4', '@5', '@6']);
});

const sug = (over: Partial<Suggestion> = {}): Suggestion => ({
  wid: '@1', name: 'tetris', manual_name: false, title: 'Tetris-style blocks', title_pending: null,
  content: true, note: null, suggested_name: 'tipstack', suggested_title: 'Tipstack billing', ...over,
});

test('initialRows: a manual name is pre-unchecked; so is a row the suggestion would not change', () => {
  const rows = initialRows([
    sug(),
    sug({ wid: '@2', manual_name: true }),
    sug({ wid: '@3', suggested_name: 'tetris', suggested_title: 'Tetris-style blocks' }),
    sug({ wid: '@4', title: null, suggested_name: 'tetris', suggested_title: '', title_pending: 'Q' }),
  ]);
  expect(rows.map(r => [r.wid, r.checked])).toEqual([['@1', true], ['@2', false], ['@3', false], ['@4', false]]);
  expect(rows[0]).toEqual({ wid: '@1', name: 'tetris', manual: false, title: 'Tetris-style blocks', pending: null, note: null,
    newName: 'tipstack', newTitle: 'Tipstack billing', checked: true });
  expect(rows[3]?.title).toBe('');
  expect(rows[3]?.pending).toBe('Q');
});

const row = (over: Partial<Row> = {}): Row => ({
  wid: '@1', name: 'a', manual: false, title: 'T', pending: null, note: null, newName: 'a', newTitle: 'T', checked: true, ...over,
});

test('rowProblems: a duplicate name is rejected inline: another window\'s, or another checked row\'s', () => {
  const windows = [ag('@1', 'a'), ag('@2', 'b'), ag('@3', 'c'), ag('@9', 'shell', { claude_running: false })];
  expect(rowProblems([row({ newName: 'shell' })], windows)).toEqual({ '@1': { name: 'Another window is named shell' } });
  expect(rowProblems([row({ newName: 'x' }), row({ wid: '@2', name: 'b', newName: 'x' })], windows)).toEqual({
    '@1': { name: 'Another window is named x' }, '@2': { name: 'Another window is named x' } });
  // a swap is refused too: the renames go one at a time and the API refuses a held name
  expect(Object.keys(rowProblems([row({ newName: 'b' }), row({ wid: '@2', name: 'b', newName: 'a' })], windows))).toEqual(['@1', '@2']);
  // unchecked rows are neither checked nor counted
  expect(rowProblems([row({ newName: 'x' }), row({ wid: '@2', name: 'b', newName: 'x', checked: false })], windows)).toEqual({});
  expect(rowProblems([row({ newName: 'BAD', checked: false })], windows)).toEqual({});
  // keeping your own name is fine
  expect(rowProblems([row()], windows)).toEqual({});
});

test.each([
  ['Has_Caps'], ['a'.repeat(21)], [''], ['sp ace'], ['ok\n'], ['ünï'],
])('rowProblems: an invalid name %j is flagged', (name) => {
  expect(rowProblems([row({ newName: name })], [ag('@1', 'a')])).toEqual({
    '@1': { name: "Name must be 1-20 lowercase letters, digits or '-'" } });
});

test.each([['a'.repeat(20)], ['a-1'], ['0']])('rowProblems: a valid name %j passes', (name) => {
  expect(rowProblems([row({ newName: name })], [ag('@1', 'a')])).toEqual({});
});

test('rowProblems: a title longer than 40 characters is flagged; 40 is fine', () => {
  expect(rowProblems([row({ newTitle: 'x'.repeat(41) })], [])).toEqual({ '@1': { title: 'Title must be at most 40 characters' } });
  expect(rowProblems([row({ newTitle: 'x'.repeat(40) })], [])).toEqual({});
});

function poster(answers: Record<string, unknown | Error>) {
  const calls: [string, unknown][] = [];
  const post = (path: string, body: unknown) => {
    calls.push([path, body]);
    const a = answers[path];
    return a instanceof Error ? Promise.reject(a) : Promise.resolve(a);
  };
  return { calls, post };
}

test('applyRows: names through the rename API, titles through the title API, per row, in order', async () => {
  const { calls, post } = poster({
    '/api/agents/%401/rename': { ok: true, wid: '@1', name: 'tipstack' },
    '/api/agents/%401/title': { ok: true, state: 'applied', reason: null },
    '/api/agents/%402/title': { ok: true, state: 'pending', reason: 'busy' },
  });
  const out = await applyRows([
    row({ newName: 'tipstack', newTitle: '  Tipstack billing ' }),
    row({ wid: '@2', name: 'b', newName: 'b', newTitle: 'Busy one' }),
    row({ wid: '@3', name: 'c', newName: 'zzz', checked: false }),
    row({ wid: '@4', name: 'd', newName: 'd', newTitle: '   ' }),
  ], post);
  expect(calls).toEqual([
    ['/api/agents/%401/rename', { name: 'tipstack' }],
    ['/api/agents/%401/title', { title: 'Tipstack billing' }],
    ['/api/agents/%402/title', { title: 'Busy one' }],
  ]);
  expect(out).toEqual({
    '@1': { name: { ok: true }, title: { state: 'applied', reason: null } },
    '@2': { title: { state: 'pending', reason: 'busy' } },
    '@4': {},
  });
  expect(outcomeText(out['@1'])).toBe('Renamed · Title set');
  expect(outcomeText(out['@2'])).toBe('Title pending (busy)');
  expect(outcomeText(out['@4'])).toBe('Nothing to change');
  expect(outcomeText(undefined)).toBe('');
});

test('applyRows: a partial failure is reported per row and never stops the others', async () => {
  const { calls, post } = poster({
    '/api/agents/%401/rename': { ok: false, error: 'another window is already named x' },
    '/api/agents/%401/title': { ok: false, state: 'refused', reason: 'no Claude session runs in this pane' },
    '/api/agents/%402/rename': new Error('network down'),
    '/api/agents/%402/title': new Error('network down'),
    '/api/agents/%403/rename': { ok: true },
    '/api/agents/%403/title': null,
  });
  const out = await applyRows([
    row({ newName: 'x', newTitle: 'Shell title' }),
    row({ wid: '@2', name: 'b', newName: 'y', newTitle: 'Y' }),
    row({ wid: '@3', name: 'c', newName: 'z', newTitle: 'Z' }),
  ], post);
  expect(calls).toHaveLength(6);
  expect(out).toEqual({
    '@1': { name: { ok: false, error: 'another window is already named x' }, title: { state: 'refused', reason: 'no Claude session runs in this pane' } },
    '@2': { name: { ok: false, error: 'rename request failed' }, title: { state: 'refused', reason: 'title request failed' } },
    '@3': { name: { ok: true }, title: { state: 'refused', reason: 'title failed' } },
  });
  expect(outcomeText(out['@1'])).toBe('Name failed: another window is already named x · Title failed: no Claude session runs in this pane');
  expect([outcomeFailed(out['@1']), outcomeFailed(out['@3']), outcomeFailed({ name: { ok: true } }), outcomeFailed(undefined)])
    .toEqual([true, true, false, false]);
  expect(outcomeFailed({ title: { state: 'pending', reason: 'busy' } })).toBe(false);
  expect(outcomeFailed({ name: { ok: false, error: 'x' } })).toBe(true);
});

test('applyRows: a rename answer without ok: true is a failure, never a success', async () => {
  const { post } = poster({ '/api/agents/%401/rename': { name: 'x' } });
  expect(await applyRows([row({ newName: 'x', newTitle: 'T' })], post)).toEqual({ '@1': { name: { ok: false, error: 'rename failed' } } });
});

// --- CMX-459: postTitle, the ONE title write (the dialog and Rename title… both call it) ----

test.each([
  ['applied', { ok: true, state: 'applied', reason: null }, { state: 'applied', reason: null }],
  ['pending', { ok: true, state: 'pending', reason: 'dialog' }, { state: 'pending', reason: 'dialog' }],
  ['pending without a reason', { state: 'pending' }, { state: 'pending', reason: null }],
  ['refused', { ok: false, state: 'refused', reason: 'no Claude session runs in this pane' }, { state: 'refused', reason: 'no Claude session runs in this pane' }],
  ['an answer with no state', { error: 'boom' }, { state: 'refused', reason: 'title failed' }],
  ['a state it does not know', { state: 'queued', reason: 'x' }, { state: 'refused', reason: 'x' }],
  ['a refusal with no reason', { state: 'refused' }, { state: 'refused', reason: 'title failed' }],
  ['no answer', null, { state: 'refused', reason: 'title failed' }],
])('postTitle: %s', async (_, reply, want) => {
  const calls: [string, unknown][] = [];
  const out = await postTitle('@4', 'Rally 2', (path, body) => {
    calls.push([path, body]);
    return Promise.resolve(reply);
  });
  expect(out).toEqual(want);
  expect(calls).toEqual([['/api/agents/%404/title', { title: 'Rally 2' }]]);
});

test('postTitle: a request that throws is refused, never applied', async () => {
  expect(await postTitle('@4', 'T', () => Promise.reject(new Error('offline')))).toEqual({ state: 'refused', reason: 'title request failed' });
});

test('titleNote: nothing for applied; why it waits; why it failed', () => {
  expect(titleNote({ state: 'applied', reason: null })).toBe('');
  expect(titleNote({ state: 'pending', reason: 'busy' })).toBe('Title pending (busy): applied when the session is idle');
  expect(titleNote({ state: 'pending', reason: null })).toBe('Title pending (waits for idle): applied when the session is idle');
  expect(titleNote({ state: 'refused', reason: 'no Claude session runs in this pane' })).toBe('Title failed: no Claude session runs in this pane');
});
