import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { createStore, installBridge, useStore, type Agent, type Store } from '../index.ts';

afterEach(cleanup);

function AgentCount({ store }: { store: Store<Agent[]> }) {
  return <p>{useStore(store).length} agents</p>;
}

test('a store update re-renders a component reading it through the hook', () => {
  const store = createStore<Agent[]>([]);
  render(<AgentCount store={store} />);
  expect(screen.getByText('0 agents')).toBeTruthy();
  act(() => store.set([{ window_id: '@1' }, { window_id: '@2' }]));
  expect(screen.getByText('2 agents')).toBeTruthy();
});

test('set notifies each listener once per change, never for the same value, never after unsubscribe', () => {
  const value: Agent[] = [];
  const store = createStore(value);
  const a = vi.fn();
  const b = vi.fn();
  const offA = store.subscribe(a);
  store.subscribe(b);
  store.set(value);
  expect([a.mock.calls.length, b.mock.calls.length]).toEqual([0, 0]);
  store.set([{ name: 'x' }]);
  expect([a.mock.calls.length, b.mock.calls.length]).toEqual([1, 1]);
  offA();
  store.set([]);
  expect([a.mock.calls.length, b.mock.calls.length]).toEqual([1, 2]);
  expect(store.get()).toEqual([]);
});

test('installBridge puts one bridge on the window, starting empty, and keeps it after', () => {
  const win = {} as Window;
  const bridge = installBridge(win);
  expect(win.chelaBridge).toBe(bridge);
  expect(bridge.agents.get()).toEqual([]);
  expect(bridge.sidebarLayout.get()).toEqual({ order: [], custom: null, view: null, width: null, loaded: false });
  bridge.agents.set([{ window_id: '@1' }]);
  expect(installBridge(win)).toBe(bridge);
  expect(installBridge(win).agents.get()).toEqual([{ window_id: '@1' }]);
});
