// CMX-453: a worktree row's folder line names the task from EITHER run card — the row's own
// (`it.run`) or, failing that, its primary window's (`a.run`) — and falls back to the worktree
// directory only when NEITHER carries a task id. The directory is not always the task id (a
// judge worktree is `judge-cmx-206`), so a fallback taken too early is visible.
import { describe, expect, test } from 'vitest';
import { type AgentRow, type Item, type RunCard, folderLines } from '../model.ts';
import { win } from './fixtures.ts';

const WT = '/home/u/.chela/worktrees/chelamux/judge-cmx-206';

function runItem(itemRun: RunCard | undefined, windowRun: RunCard | undefined): Item {
  const a: AgentRow = win('w', { cwd: WT, ...(windowRun ? { run: windowRun } : {}) });
  return { kind: 'run', key: 'run:x', run: itemRun, agent: a, judge: null, windows: [a] };
}

const card = (task_id: string): RunCard => ({ task_id, role: 'judge', status: 'running' });

describe('folderLines: the task id comes from the row OR its window, the dir only as a last resort', () => {
  test.each([
    ['only the row carries the run card', card('CMX-206'), undefined, 'chelamux · CMX-206'],
    ['only the window carries the run card', undefined, card('CMX-206'), 'chelamux · CMX-206'],
    ['both carry it — the row wins', card('CMX-206'), card('CMX-999'), 'chelamux · CMX-206'],
    ['the row card has an EMPTY task id — still falls through to the window', card(''), card('CMX-206'), 'chelamux · CMX-206'],
    ['neither carries one — the worktree dir', undefined, undefined, 'chelamux · judge-cmx-206'],
  ])('%s', (_name, itemRun, windowRun, want) => {
    expect(folderLines([runItem(itemRun, windowRun)])).toEqual({ 'run:x': want });
  });
});
