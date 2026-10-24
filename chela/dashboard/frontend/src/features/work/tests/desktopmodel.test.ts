// CMX-316: the desktop Work view's model (desktop.ts): the header's subtitle, the rails, the
// hidden columns and the Rows table's cells and sort.
import { expect, test } from 'vitest';
import type { Card } from '../board.ts';
import {
  HIDDEN_COLS_KEY, RAILS_KEY, TABLE_COLUMNS, ageSeconds, arrange, headSubtitle, loadHiddenCols, loadRails, nextSort, railSide, rowCells, saveRails, shortAge, shownColumns, sortCards, toggleHidden, viewKey,
} from '../desktop.ts';

const mem = (init: Record<string, string> = {}) => {
  const m: Record<string, string> = { ...init };
  return { m, getItem: (k: string) => m[k] ?? null, setItem: (k: string, v: string) => void (m[k] = v) };
};
const deny = { getItem: () => { throw new DOMException('denied', 'SecurityError'); }, setItem: () => { throw new DOMException('denied', 'SecurityError'); } };

test('headSubtitle: "Work" before a poll; the counts of the projects shown, then the dispatch state', () => {
  const wfs = [
    { path: '/a', active_runs: [{ task_id: '1' }], open_tasks: [{ id: 'x' }, { id: 'y' }] },
    { path: '/b', active_runs: [{ task_id: '2' }], open_tasks: [] },
  ];
  expect(headSubtitle(null, [])).toBe('Work');
  expect(headSubtitle({ workflows: wfs }, [])).toBe('2 running · 2 queued · dispatch on');
  expect(headSubtitle({ workflows: wfs }, ['/b'])).toBe('1 running · 0 queued · dispatch on');
  expect(headSubtitle({ workflows: wfs, dispatch_hold: {} }, [])).toBe('2 running · 2 queued · dispatch paused');
  expect(headSubtitle({ workflows: wfs, dispatch_hold: {}, fleet_pause: { by: 'x' } }, [])).toBe('2 running · 2 queued · agents paused');
});

test('railSide: Backlog left; Done, Archived, Canceled, Duplicate and a completed state right; the rest are columns', () => {
  for (const k of ['backlog', 'tr-backlog']) expect([k, railSide(k)]).toEqual([k, 'left']);
  for (const k of ['done', 'tr-done', 'archived', 'tr-canceled', 'tr-cancelled', 'tr-duplicate', 'tr-completed', 'tr-complete']) expect([k, railSide(k)]).toEqual([k, 'right']);
  // a whole key, never a part of one
  for (const k of ['todo', 'in_progress', 'review', 'tr-in-review', 'tr-unmapped', 'tr-qa', 'tr-backlog-grooming', 'tr-triage-backlog', 'tr-incomplete', 'tr-needs-duplicate-check', 'tr-done-review', 'xdone']) {
    expect([k, railSide(k)]).toEqual([k, null]);
  }
});

test('arrange splits by side and keeps the board order in each', () => {
  const cols = ['backlog', 'todo', 'done', 'in_progress', 'archived', 'review'].map((key) => ({ key }));
  const { left, middle, right } = arrange(cols);
  expect([left, middle, right].map((g) => g.map((c) => c.key))).toEqual([['backlog'], ['todo', 'in_progress', 'review'], ['done', 'archived']]);
});

test('viewKey: every project is "*"; a selection is its sorted paths', () => {
  expect(viewKey([])).toBe('*');
  expect(viewKey(['/b', '/a'])).toBe('/a\n/b');
  expect(viewKey(['/a', '/b'])).toBe(viewKey(['/b', '/a']));
});

test('loadRails / saveRails: per view, under the literal key; junk, a non-list and a throwing storage are none', () => {
  expect(RAILS_KEY).toBe('chela_work_rails');
  const st = mem();
  expect(saveRails(st, '*', ['done'])).toBe(true);
  expect(saveRails(st, '/a', ['backlog', 'done'])).toBe(true);
  expect(JSON.parse(st.m.chela_work_rails ?? '')).toEqual({ '*': ['done'], '/a': ['backlog', 'done'] });
  expect([loadRails(st, '*'), loadRails(st, '/a'), loadRails(st, '/b')]).toEqual([['done'], ['backlog', 'done'], []]);
  expect(loadRails(mem({ chela_work_rails: '{"*":["done",3,null]}' }), '*')).toEqual(['done']);
  for (const v of ['{bad', '[["done"]]', '{"*":"done"}', 'null']) expect([v, loadRails(mem({ chela_work_rails: v }), '*')]).toEqual([v, []]);
  expect(loadRails(deny, '*')).toEqual([]);
  expect(saveRails(deny, '*', ['done'])).toBe(false);
  // a junk record is replaced, not kept beside the new one
  const junk = mem({ chela_work_rails: '{bad' });
  expect(saveRails(junk, '*', ['done'])).toBe(true);
  expect(JSON.parse(junk.m.chela_work_rails ?? '')).toEqual({ '*': ['done'] });
});

test('hidden columns: the literal key, junk is none, a toggle flips one key, shownColumns drops them', () => {
  expect(HIDDEN_COLS_KEY).toBe('chela_work_hidden_columns');
  expect(loadHiddenCols(mem({ chela_work_hidden_columns: '["todo",1,"done"]' }))).toEqual(['todo', 'done']);
  for (const v of ['{"a":1}', 'junk']) expect(loadHiddenCols(mem({ chela_work_hidden_columns: v }))).toEqual([]);
  expect(loadHiddenCols(deny)).toEqual([]);
  expect(toggleHidden(['a'], 'b')).toEqual(['a', 'b']);
  expect(toggleHidden(['a', 'b'], 'a')).toEqual(['b']);
  const cols = ['a', 'b', 'c'].map((key) => ({ key, label: key, cards: [] }));
  expect(shownColumns(cols, ['b']).map((c) => c.key)).toEqual(['a', 'c']);
});

const NOW = Date.parse('2026-10-23T12:00:00Z');
const card = (o: Partial<Card> & Record<string, unknown>): Card => ({ status: 'open', ...o }) as Card;

test('shortAge and ageSeconds: since it ended, else started; nothing without a time', () => {
  expect([shortAge(null), shortAge(0), shortAge(59.6), shortAge(60), shortAge(3599), shortAge(3600), shortAge(86399), shortAge(86400), shortAge(200000)]).toEqual(['', '0s', '59s', '1m', '59m', '1h', '23h', '1d', '2d']);
  expect(ageSeconds(card({ started_at: '2026-10-23T11:00:00Z' }), NOW)).toBe(3600);
  expect(ageSeconds(card({ started_at: '2026-10-20T00:00:00Z', ended_at: '2026-10-23T11:59:00Z' }), NOW)).toBe(60);
  expect(ageSeconds(card({ started_at: 'garbage' }), NOW)).toBeNull();
  expect(ageSeconds(card({}), NOW)).toBeNull();
  expect(ageSeconds(card({ started_at: '2026-10-24T00:00:00Z' }), NOW)).toBe(0);
});

test('rowCells: the words of a row; CI only with a PR, risk only a known one, orange only for failing CI and high risk', () => {
  expect(TABLE_COLUMNS.map((c) => c.label)).toEqual(['ID', 'Title', 'State', 'PR', 'CI', 'Risk', 'Age']);
  const r = rowCells(card({ status: 'running', task_id: 'CMX-3', project_key: 'CMX', task_number: 3, title: 't', pr_url: 'https://github.com/o/r/pull/9', pr_checks: 'failing', risk: 'HIGH', started_at: '2026-10-23T11:00:00Z' }), NOW);
  expect(r).toEqual({ id: 'CMX-3', title: 't', state: 'running', pr: '#9', ci: 'failing', ciWarn: true, risk: 'high', riskWarn: true, age: '1h' });
  expect(rowCells(card({ task_id: 'o', pr_checks: 'failing', risk: 'extreme' }), NOW)).toMatchObject({ pr: '', ci: '', ciWarn: false, risk: '', riskWarn: false, age: '' });
  for (const [checks, word] of [['passing', 'passing'], ['pending', 'pending'], ['none', 'no CI'], [undefined, 'unknown']] as const) {
    expect(rowCells(card({ pr_url: 'https://x/pull/1', pr_checks: checks, risk: 'low' }), NOW)).toMatchObject({ ci: word, ciWarn: false, risk: 'low', riskWarn: false });
  }
});

test('CMX-318: a held run\'s state cell is its park (the epoch-second since read against the ms clock), its title the full reason', () => {
  const park = { kind: 'slot' as const, reason: 'waiting for a suite slot (holder: CMX-7)', since: NOW / 1000 - 300 };
  expect(rowCells(card({ status: 'running', park }), NOW)).toMatchObject({ state: 'waiting · waiting for a suite slot (holder: CMX-7) · 5m ago', stateTitle: 'waiting for a suite slot (holder: CMX-7) · 5m ago' });
  expect(rowCells(card({ status: 'running', park: null }), NOW)).toMatchObject({ state: 'running', stateTitle: undefined });
});

const ids = (cs: Card[]) => cs.map((c) => c.task_id);

test('sortCards: none keeps the order (a copy); numeric ids and PRs; the worst CI and the highest risk first; blanks last both ways; stable', () => {
  const cs = [
    card({ task_id: 'CMX-10', title: 'b', pr_url: 'https://x/pull/100', pr_checks: 'passing', risk: 'low', started_at: '2026-10-23T11:00:00Z' }),
    card({ task_id: 'CMX-9', title: 'a', pr_url: 'https://x/pull/20', pr_checks: 'failing', risk: 'high', started_at: '2026-10-23T09:00:00Z' }),
    card({ task_id: 'CMX-11', title: 'c' }),
    card({ task_id: 'CMX-12', title: 'a', pr_url: 'https://x/pull/30', pr_checks: 'pending', risk: 'normal', started_at: '2026-10-23T10:00:00Z' }),
  ];
  const copy = sortCards(cs, null, NOW);
  expect([copy !== cs, ids(copy)]).toEqual([true, ['CMX-10', 'CMX-9', 'CMX-11', 'CMX-12']]);
  const by = (key: Parameters<typeof nextSort>[1], dir: 'asc' | 'desc') => ids(sortCards(cs, { key, dir }, NOW));
  expect(by('id', 'asc')).toEqual(['CMX-9', 'CMX-10', 'CMX-11', 'CMX-12']);
  expect(by('id', 'desc')).toEqual(['CMX-12', 'CMX-11', 'CMX-10', 'CMX-9']);
  expect(by('pr', 'asc')).toEqual(['CMX-9', 'CMX-12', 'CMX-10', 'CMX-11']);
  expect(by('pr', 'desc')).toEqual(['CMX-10', 'CMX-12', 'CMX-9', 'CMX-11']);
  expect(by('ci', 'asc')).toEqual(['CMX-9', 'CMX-12', 'CMX-10', 'CMX-11']);
  expect(by('risk', 'asc')).toEqual(['CMX-9', 'CMX-12', 'CMX-10', 'CMX-11']);
  expect(by('risk', 'desc')).toEqual(['CMX-10', 'CMX-12', 'CMX-9', 'CMX-11']);
  expect(by('age', 'asc')).toEqual(['CMX-10', 'CMX-12', 'CMX-9', 'CMX-11']);
  expect(by('age', 'desc')).toEqual(['CMX-9', 'CMX-12', 'CMX-10', 'CMX-11']);
  // two equal titles keep their order, in either direction
  expect(by('title', 'asc')).toEqual(['CMX-9', 'CMX-12', 'CMX-10', 'CMX-11']);
  expect(by('title', 'desc')).toEqual(['CMX-11', 'CMX-10', 'CMX-9', 'CMX-12']);
  // a PR with no number sorts as its label, after the numbered ones
  expect(ids(sortCards([card({ task_id: 'u', pr_url: 'https://x/review' }), card({ task_id: 'n', pr_url: 'https://x/pull/5' })], { key: 'pr', dir: 'asc' }, NOW))).toEqual(['n', 'u']);
});

test('nextSort: a new column ascends, the same one flips, then clears', () => {
  expect(nextSort(null, 'id')).toEqual({ key: 'id', dir: 'asc' });
  expect(nextSort({ key: 'id', dir: 'asc' }, 'id')).toEqual({ key: 'id', dir: 'desc' });
  expect(nextSort({ key: 'id', dir: 'desc' }, 'id')).toBeNull();
  expect(nextSort({ key: 'id', dir: 'desc' }, 'age')).toEqual({ key: 'age', dir: 'asc' });
});
