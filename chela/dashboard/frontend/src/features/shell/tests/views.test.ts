// THE VIEW REGISTRY (tests/views.test.mjs until CMX-248): the contract that makes a view cheap
// to DELETE, not just to add. Before it, a view lived in four places (the sidebar markup, the
// view switch's timer if/else, the refresh loop's if/else and the palette's own list), so a
// view was never removed and the dashboard carried seven. CMX-279: Wall and Work ship, plus
// the agent-detail drill-in. The panels each view needs are App.test.tsx's.
import { expect, test, vi } from 'vitest';
import { startTermTimer, stopTermTimer } from '../../paneframe/index.ts';
import { renderTerminals } from '../../wall/index.ts';
import { VIEWS } from '../index.ts';
import { findView, navViews, otherViews, paletteViews, panelId, type View } from '../views.ts';

// A registry shaped like the real one, its hooks stubbed out.
const fake = (): View[] => [
  { id: 'terminals', label: 'Wall', enabled: (ctx) => ctx.terminalsOn },
  { id: 'work', label: 'Work', badges: [{ id: 'side-runs-count' }] },
  { id: 'agent-detail', label: 'Agent', virtual: true },
];
const ON = { terminalsOn: true };

vi.mock('../../paneframe/index.ts', async (orig) => ({ ...(await orig<object>()), startTermTimer: vi.fn(), stopTermTimer: vi.fn() }));
vi.mock('../../wall/index.ts', async (orig) => ({ ...(await orig<object>()), renderTerminals: vi.fn(() => Promise.resolve()) }));

test('the shipped registry is Wall, Work and the agent drill-in, in that order, and the fake copies it', () => {
  expect(VIEWS.map((v) => v.id)).toEqual(['terminals', 'work', 'agent-detail']);
  expect(fake().map((v) => v.id)).toEqual(VIEWS.map((v) => v.id));
  expect(VIEWS.map((v) => [v.id, !!v.virtual])).toEqual([['terminals', false], ['work', false], ['agent-detail', true]]);
});

test('the shipped Wall is enabled exactly when terminals are on; Work always', () => {
  expect(navViews(VIEWS, { terminalsOn: true }).map((v) => v.id)).toEqual(['terminals', 'work']);
  expect(navViews(VIEWS, { terminalsOn: false }).map((v) => v.id)).toEqual(['work']);
});

test('the shipped labels, icons and the Work badges the poll fills', () => {
  expect(VIEWS.map((v) => [v.label, v.lucide])).toEqual([['Wall', 'layout-grid'], ['Work', 'columns-3'], ['Agent', undefined]]);
  expect(findView(VIEWS, 'work')?.badges).toEqual([
    { id: 'side-runs-count', title: 'Active runs', text: '0' },
    { id: 'side-pr-count', cls: 'badge-pr', title: 'Open PRs', text: '0 PR' },
  ]);
});

test('the sidebar and the palette both derive from the registry: same views, same order', () => {
  expect(navViews(fake(), ON).map((v) => v.id)).toEqual(['terminals', 'work']);
  expect(paletteViews(fake(), ON).map((v) => v.id)).toEqual(['terminals', 'work']);
});

test('ADDING a view is one registry entry: nav, palette and lookup all pick it up', () => {
  const views = [...fake(), { id: 'metrics', label: 'Metrics' }];
  expect(navViews(views, ON).map((v) => v.id)).toContain('metrics');
  expect(paletteViews(views, ON).map((v) => v.id)).toContain('metrics');
  expect(findView(views, 'metrics')?.label).toBe('Metrics');
  expect(panelId('metrics')).toBe('panel-metrics');
});

test('REMOVING a view is one registry deletion: it leaves the nav, the palette and the lookup', () => {
  const views = fake().filter((v) => v.id !== 'work');
  expect(findView(views, 'work')).toBeNull();
  expect(navViews(views, ON).map((v) => v.id)).toEqual(['terminals']);
  expect(paletteViews(views, ON).map((v) => v.id)).toEqual(['terminals']);
});

test('a virtual view is reachable but neither a nav row nor a palette entry', () => {
  expect(findView(fake(), 'agent-detail')).not.toBeNull();
  expect(navViews(fake(), ON).map((v) => v.id)).not.toContain('agent-detail');
  expect(paletteViews(fake(), ON).map((v) => v.id)).not.toContain('agent-detail');
});

test('enabled: a predicate decides per context, a boolean false always hides, absent means shown', () => {
  const views: View[] = [{ id: 'a', enabled: false }, { id: 'b', enabled: true }, { id: 'c' }, { id: 'd', enabled: (c) => !c.terminalsOn }];
  expect(navViews(views, ON).map((v) => v.id)).toEqual(['b', 'c']);
  expect(navViews(views, { terminalsOn: false }).map((v) => v.id)).toEqual(['b', 'c', 'd']);
});

test('palette: false keeps a view out of the palette only', () => {
  const views: View[] = [...fake(), { id: 'quiet', palette: false }];
  expect(navViews(views, ON).map((v) => v.id)).toContain('quiet');
  expect(paletteViews(views, ON).map((v) => v.id)).not.toContain('quiet');
});

test('entering a view lets every OTHER view go, and only them', () => {
  expect(otherViews(fake(), 'work').map((v) => v.id)).toEqual(['terminals', 'agent-detail']);
  expect(findView(fake(), 'nope')).toBeNull();
});

test('the Wall\'s hooks own its 4 s timer and its tick renders the wall from the tick\'s context read', () => {
  const w = findView(VIEWS, 'terminals') as View;
  w.enter?.();
  expect([vi.mocked(startTermTimer).mock.calls.length, vi.mocked(stopTermTimer).mock.calls.length]).toEqual([1, 0]);
  w.exit?.();
  expect(vi.mocked(stopTermTimer).mock.calls.length).toBe(1);
  const ctx = Promise.resolve([]);
  w.tick?.(ctx);
  expect(vi.mocked(renderTerminals).mock.calls).toEqual([[ctx]]);
  // Work and the drill-in have no hooks: the view switch's refresh runs the Work poll.
  for (const id of ['work', 'agent-detail']) {
    const v = findView(VIEWS, id) as View;
    expect([v.enter, v.exit, v.tick]).toEqual([undefined, undefined, undefined]);
  }
});
