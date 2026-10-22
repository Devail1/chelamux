// CMX-242 (CMX-212 P28): the lazy dock's machine and the chip order, pure. The machine's
// properties are walldock.test.mjs's (CMX-76): a working worker docks, a blocked one pops out,
// once popped the wall never touches it again, and a recycled id never hides a human's window.
import { expect, test } from 'vitest';
import { lazyDockStep, taskbarOrder, type DockAgent } from '../model.ts';

const worker = (over: Partial<DockAgent> = {}): DockAgent => ({ window_id: '@9', dispatched: true, session_status: 'busy', ...over });
const view = (onWall: string[] = ['@9'], minimized: string[] = []) => ({
  onWall: (w: string) => onWall.includes(w),
  minimized: (w: string) => minimized.includes(w),
});

test('a working dispatched worker on the wall is docked, once', () => {
  const s = lazyDockStep([worker()], {}, view());
  expect(s).toEqual({ state: { '@9': 'docked' }, minimize: ['@9'], restore: [], changed: true });
  // the next poll changes nothing: the decision is taken
  expect(lazyDockStep([worker()], s.state, view(['@9'], ['@9']))).toEqual({ state: { '@9': 'docked' }, minimize: [], restore: [], changed: false });
});

test('a worker with no tile yet is decided on the next tick, never dropped', () => {
  expect(lazyDockStep([worker()], {}, view([]))).toEqual({ state: {}, minimize: [], restore: [], changed: false });
});

test.each([
  ['needs_human', { needs_human: true }],
  ['waiting', { session_status: 'waiting' }],
])('a worker born blocked (%s) keeps its tile: popped at once', (_, over) => {
  expect(lazyDockStep([worker(over)], {}, view())).toEqual({ state: { '@9': 'popped' }, minimize: [], restore: [], changed: true });
});

test('a docked worker that blocks POPS OUT, restored only if it is still minimized', () => {
  expect(lazyDockStep([worker({ needs_human: true })], { '@9': 'docked' }, view(['@9'], ['@9'])))
    .toEqual({ state: { '@9': 'popped' }, minimize: [], restore: ['@9'], changed: true });
  expect(lazyDockStep([worker({ needs_human: true })], { '@9': 'docked' }, view(['@9'], [])))
    .toEqual({ state: { '@9': 'popped' }, minimize: [], restore: [], changed: true });
});

test('once popped, the wall never touches it again, blocked or not', () => {
  for (const over of [{}, { needs_human: true }]) {
    expect(lazyDockStep([worker(over)], { '@9': 'popped' }, view(['@9'], ['@9'])))
      .toEqual({ state: { '@9': 'popped' }, minimize: [], restore: [], changed: false });
  }
});

test('a window that stops being dispatched is disowned, and un-hidden only if the dock hid it', () => {
  const human = worker({ dispatched: false });
  expect(lazyDockStep([human], { '@9': 'docked' }, view(['@9'], ['@9']))).toEqual({ state: {}, minimize: [], restore: ['@9'], changed: true });
  expect(lazyDockStep([human], { '@9': 'docked' }, view(['@9'], []))).toEqual({ state: {}, minimize: [], restore: [], changed: true });
  expect(lazyDockStep([human], { '@9': 'popped' }, view(['@9'], ['@9']))).toEqual({ state: {}, minimize: [], restore: [], changed: true });
  expect(lazyDockStep([human], {}, view())).toEqual({ state: {}, minimize: [], restore: [], changed: false });
});

test('rows without a window id are skipped, and the state passed in is never mutated', () => {
  const state = Object.freeze({ '@9': 'docked' as const });
  const s = lazyDockStep([null, undefined, { dispatched: true }, worker({ dispatched: false })], state, view());
  expect(s.state).toEqual({});
  expect(state).toEqual({ '@9': 'docked' });
});

test('the chip order: waiting first, then most recently busy, the manual order breaks ties', () => {
  const base = ['@1', '@2', '@3', '@4'];
  expect(taskbarOrder(base, {}, {})).toEqual(base);
  expect(taskbarOrder(base, {}, { '@3': 100, '@2': 50 })).toEqual(['@3', '@2', '@1', '@4']);
  expect(taskbarOrder(base, { '@4': 'waiting' }, { '@3': 100 })).toEqual(['@4', '@3', '@1', '@2']);
  expect(taskbarOrder(base, { '@1': 'waiting', '@4': 'waiting' }, { '@4': 5 })).toEqual(['@4', '@1', '@2', '@3']);
});
