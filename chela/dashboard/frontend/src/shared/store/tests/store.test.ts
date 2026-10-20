import { expect, test } from 'vitest';
import { agents } from '../agents.ts';
import { view } from '../view.ts';

test('the view starts on Work, the page with terminals off, until one is selected', () => {
  expect(view.get()).toBe('work');
});

test('agents IS the page bridge\'s store, so the islands and legacy read one value', () => {
  expect(window.chelaBridge?.agents).toBe(agents);
  const rows = [{ window_id: '@1' }];
  agents.set(rows);
  expect(window.chelaBridge?.agents.get()).toBe(rows);
});
