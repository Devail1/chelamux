// CMX-242: every footer and recap is one root, portaled into the slots terminals.js draws;
// paints are synchronous, so a poll's result is on the page when the poll returns.
import { expect, test } from 'vitest';
import { agents } from '../../../shared/store/agents.ts';
import { context } from '../../../shared/store/context.ts';
import { mountTileFoots, refreshTileFoots, toggleRecap } from '../index.ts';
import './setup.ts';

function stage(html: string) {
  const el = document.createElement('div');
  el.innerHTML = html;
  document.body.append(el);
  return el;
}
const tile = (wid: string, wall = true) => `<div class="grid-stack-item-content" data-pane="${wid}">
  ${wall ? `<div class="island-slot" data-recap-slot="${wid}"></div>` : ''}
  <div class="island-slot" data-foot-for="${wid}"${wall ? ' data-foot-wall="1"' : ''}></div></div>`;
const q = (root: ParentNode, wid: string, sel: string) => root.querySelector<HTMLElement>(`[data-pane="${wid}"] ${sel}`);

test('a mount draws every slot before it returns: the wall tile its recap, footer and action bar, the single pane its footer', () => {
  agents.set([{ window_id: '@1', recap: 'did it' }, { window_id: '@2' }]);
  const root = stage(tile('@1') + tile('@2', false));
  mountTileFoots(root);
  expect(q(root, '@1', '.pane-recap')?.textContent).toBe('did it');
  expect(q(root, '@1', '.term-ctx-bar')?.dataset.ctxFor).toBe('@1');
  expect(q(root, '@1', '.term-action-bar')).not.toBeNull();
  expect(q(root, '@1', '.gs-pr')).not.toBeNull();
  expect(q(root, '@2', '.term-ctx-bar')?.dataset.ctxFor).toBe('@2');
  expect(q(root, '@2', '.gs-pr')).toBeNull();
  expect(q(root, '@2', '.term-action-bar')).toBeNull();
  root.remove();
});

test('a slot with no window id gets nothing', () => {
  const root = stage('<div data-foot-for=""></div><div data-recap-slot=""></div>');
  mountTileFoots(root);
  expect(root.querySelector('.term-ctx-bar, .pane-recap')).toBeNull();
  root.remove();
});

test('each store a footer reads repaints it synchronously on publish', () => {
  const root = stage(tile('@5'));
  mountTileFoots(root);
  context.set([{ window_id: '@5', used_pct: 42, used: '84K', total: '200K' }]);
  expect(q(root, '@5', '.gs-ctx')?.textContent).toBe('42% · 84K/200K');
  agents.set([{ window_id: '@5', session_status: 'waiting', recap: 'r' }]);
  expect(q(root, '@5', '.term-action-bar')?.hidden).toBe(false);
  toggleRecap('@5');
  expect(q(root, '@5', '.pane-recap')?.classList.contains('recap-open')).toBe(true);
  root.remove();
});

test('refreshTileFoots repaints from the stores even when no store published (an in-place change)', () => {
  const rows = [{ window_id: '@6', recap: 'before' }];
  agents.set(rows);
  const root = stage(tile('@6'));
  mountTileFoots(root);
  rows[0] = { window_id: '@6', recap: 'after' };
  expect(q(root, '@6', '.pane-recap')?.textContent).toBe('before');
  refreshTileFoots();
  expect(q(root, '@6', '.pane-recap')?.textContent).toBe('after');
  root.remove();
});

test('a slot already drawn is not drawn twice, and one that left the page is dropped', () => {
  const root = stage(tile('@7'));
  mountTileFoots(root);
  mountTileFoots(root);
  expect(root.querySelectorAll('.term-ctx-bar').length).toBe(1);
  const gone = root.querySelector('[data-pane="@7"]') as HTMLElement;
  gone.remove();
  refreshTileFoots();
  expect(gone.querySelectorAll('.term-ctx-bar').length).toBe(0);
  root.remove();
});
