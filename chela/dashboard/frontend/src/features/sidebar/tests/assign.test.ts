// CMX-378: custom-group assignments follow the Claude SESSION, not the tmux window. A reboot
// or tmux restart resumes every session under a NEW window id; a window-keyed assignment
// then names nothing and the groups came back empty.
import { expect, test } from 'vitest';
import {
  type AgentRow, type Custom, type SessionRecord, assignKey, buildItems, groupSidebar, reconcileAssign, sidebarModel,
} from '../model.ts';
import { NOW, runWin, state, wants, win } from './fixtures.ts';

const DAY = 86400000;
/** The assignment key of the ONE row `a` makes. */
const keyOf = (a: AgentRow): string => {
  const [it] = buildItems([a]);
  if (!it) throw new Error('no row');
  return assignKey(it);
};
const G = [{ id: 'g1', name: 'Infra' }];
const custom = (assign: Record<string, string>, seen?: Record<string, number>): Custom =>
  ({ groups: G, assign, ...(seen ? { seen } : {}) });
const under = (agents: AgentRow[], c: Custom, label = 'Infra') =>
  groupSidebar(agents, { mode: 'custom', wants, custom: c, now: NOW }).groups
    .find(g => g.label === label)?.items.map(it => it.agent?.name) ?? [];

test('assignKey: an agent with a session id is keyed by it; one without, by its window; a run, by its task', () => {
  expect(keyOf(win('a', { window_id: '@7', session_id: 'S' }))).toBe('s:S');
  expect(keyOf(win('a', { window_id: '@7' }))).toBe('w:@7');
  expect(keyOf(win('a', { window_id: '@7', session_id: '' }))).toBe('w:@7');
  expect(keyOf(runWin('CMX-8', 'agent', {}, { session_id: 'R' }))).toBe('run:CMX-8');
});

test('ACCEPTANCE: assigned on window @7 / session S, the row is still in its group once S comes back as @3', () => {
  const before = win('mine', { window_id: '@7', session_id: 'S' });
  const key = keyOf(before);
  // negative control: this is the key the menu saves; a window key would be `w:@7`
  expect(key).toBe('s:S');
  const c = custom({ [key]: 'g1' });
  expect(under([before], c)).toEqual(['mine']);
  const after = { ...before, window_id: '@3' };
  expect(under([after], c)).toEqual(['mine']);
  // and the reconcile pass on that render keeps the assignment as it is
  expect(reconcileAssign(c, [after], [], NOW)).toEqual(custom({ 's:S': 'g1' }, { 's:S': NOW }));
});

test('negative control: a WINDOW-keyed assignment loses the row once its session moves to @3', () => {
  const after = win('mine', { window_id: '@3', session_id: 'S' });
  expect(under([after], custom({ 'w:@7': 'g1' }))).toEqual([]);
});

test('ACCEPTANCE: the migration turns a live window\'s legacy `w:` key into its session key', () => {
  const a = win('mine', { window_id: '@7', session_id: 'S' });
  const out = reconcileAssign(custom({ 'w:@7': 'g1' }), [a], [], NOW);
  expect(out).toEqual(custom({ 's:S': 'g1' }, { 's:S': NOW }));
  expect(under([a], out ?? custom({}))).toEqual(['mine']);
});

test('migration never overwrites an existing session-keyed assignment', () => {
  const groups = [...G, { id: 'g2', name: 'Ops' }];
  const a = win('mine', { window_id: '@7', session_id: 'S' });
  const out = reconcileAssign({ groups, assign: { 'w:@7': 'g2', 's:S': 'g1' }, seen: { 's:S': NOW } }, [a], [], NOW);
  expect(out).toEqual({ groups, assign: { 's:S': 'g1' }, seen: { 's:S': NOW } });
});

test('a live window with no session id keeps its `w:` key; a gone one is dropped (a recycled @N must not inherit it)', () => {
  const shell = win('sh', { window_id: '@4' });
  expect(reconcileAssign(custom({ 'w:@4': 'g1' }), [shell], [], NOW)).toBeNull();
  expect(reconcileAssign(custom({ 'w:@4': 'g1', 'w:@9': 'g1' }), [shell], [], NOW)).toEqual(custom({ 'w:@4': 'g1' }, {}));
});

test('a run window\'s session id never migrates a `w:` key (a run is keyed by its task)', () => {
  const r = runWin('CMX-8', 'agent', {}, { window_id: '@5', session_id: 'R' });
  expect(reconcileAssign(custom({ 'w:@5': 'g1', 'run:CMX-8': 'g1' }), [r], [], NOW)).toBeNull();
});

test('ACCEPTANCE: a session absent for one tick keeps its assignment (restore may be mid-flight)', () => {
  const c = custom({ 's:S': 'g1' }, { 's:S': NOW });
  // the tick it is gone: nothing to change at all
  expect(reconcileAssign(c, [win('other', { session_id: 'T' })], [], NOW + 2000)).toBeNull();
  // and the tick after, it is back under a new window, still in its group
  expect(under([win('mine', { window_id: '@3', session_id: 'S' })], c)).toEqual(['mine']);
});

test('an absent session with no stamp yet starts the clock; it is not pruned', () => {
  expect(reconcileAssign(custom({ 's:S': 'g1' }), [], [], NOW)).toEqual(custom({ 's:S': 'g1' }, { 's:S': NOW }));
});

test('pruned only once neither live nor archived for 30 days', () => {
  const at30 = custom({ 's:S': 'g1' }, { 's:S': NOW - 30 * DAY });
  expect(reconcileAssign(at30, [], [], NOW)).toBeNull();
  const past30 = custom({ 's:S': 'g1' }, { 's:S': NOW - 30 * DAY - 1 });
  expect(reconcileAssign(past30, [], [], NOW)).toEqual(custom({}, {}));
  // an archived record of the session keeps it (unarchiving resumes it into its group)
  const rec = { key: 's:S', session_id: 'S' };
  expect(reconcileAssign(past30, [], [rec], NOW)).toEqual(custom({ 's:S': 'g1' }, { 's:S': NOW }));
  // as does the session being live
  expect(reconcileAssign(past30, [win('m', { session_id: 'S' })], [], NOW)).toEqual(custom({ 's:S': 'g1' }, { 's:S': NOW }));
});

test('a live session\'s stamp is refreshed at most once a day (no write every tick)', () => {
  const live = [win('m', { session_id: 'S' })];
  expect(reconcileAssign(custom({ 's:S': 'g1' }, { 's:S': NOW - DAY + 1 }), live, [], NOW)).toBeNull();
  expect(reconcileAssign(custom({ 's:S': 'g1' }, { 's:S': NOW - DAY }), live, [], NOW))
    .toEqual(custom({ 's:S': 'g1' }, { 's:S': NOW }));
});

test('run keys are kept; stray stamps for unassigned keys are dropped', () => {
  expect(reconcileAssign(custom({ 'run:CMX-8': 'g1' }), [], [], NOW)).toBeNull();
  expect(reconcileAssign(custom({ 'run:CMX-8': 'g1' }, { 's:old': 1 }), [], [], NOW)).toEqual(custom({ 'run:CMX-8': 'g1' }, {}));
});

test('the island draws a resumed session in its group from the state it is handed', () => {
  const s = state([win('mine', { window_id: '@3', session_id: 'S' })], {
    view: { ...state([]).view, groupBy: 'custom' }, custom: custom({ 's:S': 'g1' }),
  });
  expect(sidebarModel(s).groups.find(g => g.label === 'Infra')?.items.map(it => it.key)).toEqual(['w:@3']);
});

test('nothing is pruned while the archive has not answered (sessions = null): unknown is not "gone"', () => {
  const past30 = custom({ 's:S': 'g1' }, { 's:S': NOW - 31 * DAY });
  expect(reconcileAssign(past30, [], null, NOW)).toBeNull();
  expect(reconcileAssign(past30, [], [], NOW)).toEqual(custom({}, {}));
});

test('a stamp in the future is reset to now, so the key can still age out', () => {
  const c = custom({ 's:S': 'g1' }, { 's:S': NOW + 365 * DAY });
  expect(reconcileAssign(c, [], [], NOW)).toEqual(custom({ 's:S': 'g1' }, { 's:S': NOW }));
  expect(reconcileAssign(c, [win('m', { session_id: 'S' })], [], NOW)).toEqual(custom({ 's:S': 'g1' }, { 's:S': NOW }));
});

test('a null hole in the agent list is skipped, not read (the rows after it still migrate)', () => {
  const c = custom({ 'w:@7': 'g1' });
  const agents = [null, undefined, win('m', { window_id: '@7', session_id: 'S' })];
  expect(reconcileAssign(c, agents, [], NOW)).toEqual(custom({ 's:S': 'g1' }, { 's:S': NOW }));
});

test('a null hole in the archive list is skipped, not read (the records after it still count)', () => {
  // CMX-454: the archive guard `r && r.session_id` must hold for a hole, not only for a
  // record with no session id; read through, the hole throws and the reconcile dies.
  const past30 = custom({ 's:S': 'g1' }, { 's:S': NOW - 31 * DAY });
  const sessions = [null, { key: 's:S', session_id: 'S' }] as unknown as SessionRecord[];
  expect(reconcileAssign(past30, [], sessions, NOW)).toEqual(custom({ 's:S': 'g1' }, { 's:S': NOW }));
  // negative control: without the record after the hole, the key is pruned
  expect(reconcileAssign(past30, [], [null] as unknown as SessionRecord[], NOW)).toEqual(custom({}, {}));
});

test('a window with no previous session, or the same one, moves no assignment', () => {
  const c = custom({ 's:A': 'g1' }, { 's:A': NOW });
  const now = [win('m', { window_id: '@5', session_id: 'A' })];
  expect(reconcileAssign(c, now, [], NOW, new Map([['@5', 'A']]))).toBeNull();
  expect(reconcileAssign(c, [win('m', { window_id: '@5', session_id: 'B' })], [], NOW, new Map([['@9', 'A']]))).toBeNull();
});

test('buildItems skips a null hole in the agent list', () => {
  expect(buildItems([null, win('m'), undefined]).map(it => it.agent?.name)).toEqual(['m']);
});

test('a window whose session changed in place (/clear) carries its group to the new session', () => {
  const c = custom({ 's:A': 'g1' }, { 's:A': NOW });
  const now = [win('m', { window_id: '@5', session_id: 'B' })];
  expect(reconcileAssign(c, now, [], NOW, new Map([['@5', 'A']]))).toEqual(custom({ 's:B': 'g1' }, { 's:B': NOW }));
  // without the previous render's map it stays where it was
  expect(reconcileAssign(c, now, [], NOW)).toBeNull();
  // the old session still live in another window keeps its group; the new one does not take it
  const both = [...now, win('o', { window_id: '@9', session_id: 'A' })];
  expect(reconcileAssign(c, both, [], NOW, new Map([['@5', 'A']]))).toBeNull();
  // the new session already has a group of its own: neither moves
  const own = custom({ 's:A': 'g1', 's:B': 'g1' }, { 's:A': NOW, 's:B': NOW });
  expect(reconcileAssign(own, now, [], NOW, new Map([['@5', 'A']]))).toBeNull();
});
