// @vitest-environment node
// CMX-218: the relaxed fs rule (tests read ../static/style.css) is for Vitest only; the dev
// server keeps Vite's strict file serving.
import { expect, test } from 'vitest';
import config from '../vite.config.ts';

const server = (mode: string) => {
  if (typeof config !== 'function') throw new Error('vite.config.ts exports a config factory');
  return config({ mode, command: 'serve' }).server;
};

test('only test mode turns strict fs off', () => {
  expect(server('test')?.fs).toEqual({ strict: false });
  expect(server('development')?.fs).toBeUndefined();
  expect(server('production')?.fs).toBeUndefined();
});
