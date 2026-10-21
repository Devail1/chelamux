// CMX-241: every header is one root, portaled into the slots terminals.js draws; paints are
// synchronous, so Gridstack finds a tile's `.gs-grip` and legacy reads the header back at once.
import { expect, test, vi } from 'vitest';
import { agents } from '../../../shared/store/agents.ts';
import { orchestrator } from '../../../shared/store/orchestrator.ts';
import { pinnedPanes, setPinned } from '../../../shared/store/wall.ts';
import { noteShare } from '../../share/index.ts';
import { configurePaneHeads, mountPaneHeads, refreshPaneHeads } from '../index.ts';
import { must, openMenu } from './setup.ts';

function stage(html: string) {
  const el = document.createElement('div');
  el.innerHTML = html;
  document.body.append(el);
  return el;
}
const slot = (wid: string, wall = true) => `<div class="pane" data-pane="${wid}"><div class="island-slot" data-head-for="${wid}"${wall ? ' data-head-wall="1"' : ''}></div></div>`;
const title = (root: ParentNode, wid: string) => root.querySelector(`[data-pane="${wid}"] .pane-title`)?.textContent;

test('a mount draws every slot\'s header before it returns, wall and single alike', () => {
  agents.set([{ window_id: '@1', name: 'one' }, { window_id: '@2', name: 'two' }]);
  const root = stage(slot('@1') + slot('@2', false));
  mountPaneHeads(root);
  expect(title(root, '@1')).toBe('one');
  expect(root.querySelector('[data-pane="@1"] .gs-grip')).not.toBeNull();
  expect(root.querySelector('[data-pane="@2"] .gs-label')).not.toBeNull();
  expect(root.querySelector('[data-pane="@2"] .gs-grip')).toBeNull();
  root.remove();
});

test('a slot with no window id gets no header', () => {
  const root = stage('<div class="pane"><div data-head-for=""></div></div>');
  mountPaneHeads(root);
  expect(root.querySelector('.gs-head')).toBeNull();
  root.remove();
});

test('each store a header reads repaints it synchronously on publish', () => {
  agents.set([{ window_id: '@5', name: 'five', claude_running: true }]);
  const root = stage(slot('@5'));
  mountPaneHeads(root);
  const q = (s: string) => root.querySelector(s);

  agents.set([{ window_id: '@5', name: 'renamed', claude_running: true }]);
  expect(title(root, '@5')).toBe('renamed');

  orchestrator.set({ wid: '@5', name: 'x', state: 'ok', why: '', queued: 0 });
  expect(q('.gs-dot-ring')?.classList.contains('gs-dot-orch')).toBe(true);

  noteShare('@5', 'unsandboxed');
  expect((q('.gs-unsafe-banner') as HTMLElement).hidden).toBe(false);

  setPinned('@5', true);
  expect(openMenu(root).querySelector('.gs-pin-btn')?.getAttribute('aria-checked')).toBe('true');
  root.remove();
});

test('refreshPaneHeads repaints from the stores even when no store published (an in-place change)', () => {
  const rows = [{ window_id: '@6', name: 'six' }];
  agents.set(rows);
  const root = stage(slot('@6'));
  mountPaneHeads(root);
  rows[0] = { window_id: '@6', name: 'mutated' };
  expect(title(root, '@6')).toBe('six');
  refreshPaneHeads();
  expect(title(root, '@6')).toBe('mutated');
  root.remove();
});

test('a slot that left the page is dropped: its header unmounts on the next paint', () => {
  agents.set([{ window_id: '@7', name: 'seven' }]);
  const root = stage(slot('@7'));
  mountPaneHeads(root);
  const gone = must(root.querySelector('[data-pane="@7"]'));
  root.remove();
  refreshPaneHeads();
  expect(gone.querySelector('.gs-head')).toBeNull();
});

test('mounting the same slot twice draws one header', () => {
  agents.set([{ window_id: '@8', name: 'eight' }]);
  const root = stage(slot('@8'));
  mountPaneHeads(root);
  mountPaneHeads(root);
  expect(root.querySelectorAll('.gs-head')).toHaveLength(1);
  root.remove();
});

test('a header asks the Wall through the host terminals.js configured', () => {
  const minimize = vi.fn();
  configurePaneHeads({ minimize, dropPane() {}, wireDragStart() {}, leaveRoom() {}, labelsChanged() {} });
  agents.set([{ window_id: '@9', name: 'nine' }]);
  const root = stage(slot('@9'));
  mountPaneHeads(root);
  (root.querySelector('.gs-min-btn') as HTMLElement).click();
  expect(minimize).toHaveBeenCalledWith('@9');
  root.remove();
});

test('setPinned persists the set and publishes only a change', () => {
  pinnedPanes.set(new Set());
  const seen = vi.fn();
  const off = pinnedPanes.subscribe(seen);
  setPinned('@1', true);
  setPinned('@1', true);
  expect(seen).toHaveBeenCalledTimes(1);
  expect(JSON.parse(localStorage.getItem('pc_wall_pinned') ?? '[]')).toEqual(['@1']);
  setPinned('@1', false);
  expect(JSON.parse(localStorage.getItem('pc_wall_pinned') ?? '[]')).toEqual([]);
  expect(seen).toHaveBeenCalledTimes(2);
  off();
});

test('the pinned set loads what the page saved, ignoring anything that is not a window id', async () => {
  vi.resetModules();
  localStorage.setItem('pc_wall_pinned', JSON.stringify(['@1', 3, null, '@2']));
  const wall = await import('../../../shared/store/wall.ts');
  expect([...wall.pinnedPanes.get()]).toEqual(['@1', '@2']);
  vi.resetModules();
  localStorage.setItem('pc_wall_pinned', '{not json');
  expect([...(await import('../../../shared/store/wall.ts')).pinnedPanes.get()]).toEqual([]);
  vi.resetModules();
  localStorage.setItem('pc_wall_pinned', '{"a": 1}');
  expect([...(await import('../../../shared/store/wall.ts')).pinnedPanes.get()]).toEqual([]);
  vi.resetModules();
  localStorage.removeItem('pc_wall_pinned');
  expect([...(await import('../../../shared/store/wall.ts')).pinnedPanes.get()]).toEqual([]);
});
