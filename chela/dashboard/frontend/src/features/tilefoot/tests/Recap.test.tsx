// CMX-242 (CMX-212 P27): a wall tile's recap line and its pin-open, from terminals.js
// `_recapLineHTML`, `toggleRecap` and the recap half of `_applyWallTileFrame`.
import { fireEvent, render } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import { useStore } from '../../../shared/store/index.ts';
import type { HeadAgent } from '../../panehead/index.ts';
import { Recap, recapOpen, toggleRecap } from '../index.ts';
import { must } from './setup.ts';

const AGENT: HeadAgent = { window_id: '@1', name: 'w', recap: 'Fixed the flaky test and pushed', recap_ts: '2026-10-22T10:00:00Z' };

function Live({ agent }: { agent: HeadAgent | undefined }) {
  const open = useStore(recapOpen);
  return <Recap wid="@1" agent={agent} open={open.has('@1')} />;
}

const line = (c: HTMLElement) => must(c.querySelector<HTMLElement>('.pane-recap[data-recap-for="@1"]'));

test('no recap: the line is hidden and empty, never an empty bar', () => {
  const { container } = render(<Live agent={{ window_id: '@1' }} />);
  expect(line(container).hidden).toBe(true);
  expect(line(container).textContent).toBe('');
  expect(line(container).title).toBe('');
});

test('a recap shows on one line; the tooltip carries all of it and its time', () => {
  const { container } = render(<Live agent={AGENT} />);
  const el = line(container);
  expect(el.hidden).toBe(false);
  expect(el.textContent).toBe('Fixed the flaky test and pushed');
  expect(el.title).toBe('Fixed the flaky test and pushed\n\n(as of 2026-10-22T10:00:00Z)');
  expect(el.classList.contains('recap-open')).toBe(false);
});

test('a recap without a time has no "as of"', () => {
  const { container } = render(<Live agent={{ ...AGENT, recap_ts: null }} />);
  expect(line(container).title).toBe('Fixed the flaky test and pushed');
});

test('a click pins it open (kept for a reload); another unpins it', () => {
  const { container } = render(<Live agent={AGENT} />);
  fireEvent.click(line(container));
  expect(line(container).classList.contains('recap-open')).toBe(true);
  expect(line(container).getAttribute('aria-expanded')).toBe('true');
  expect(JSON.parse(localStorage.getItem('pc_wall_recap_open') ?? '[]')).toEqual(['@1']);
  fireEvent.click(line(container));
  expect(line(container).classList.contains('recap-open')).toBe(false);
  expect(JSON.parse(localStorage.getItem('pc_wall_recap_open') ?? '[]')).toEqual([]);
});

test('Enter and Space toggle it too; other keys do not', () => {
  const { container } = render(<Live agent={AGENT} />);
  fireEvent.keyDown(line(container), { key: 'a' });
  expect(line(container).classList.contains('recap-open')).toBe(false);
  fireEvent.keyDown(line(container), { key: 'Enter' });
  expect(line(container).classList.contains('recap-open')).toBe(true);
  fireEvent.keyDown(line(container), { key: ' ' });
  expect(line(container).classList.contains('recap-open')).toBe(false);
});

test('a pin with no recap to show draws nothing open', () => {
  toggleRecap('@1');
  const { container } = render(<Live agent={{ window_id: '@1' }} />);
  expect(line(container).classList.contains('recap-open')).toBe(false);
});

test('each window keeps its own pin', () => {
  toggleRecap('@2');
  const { container } = render(<Live agent={AGENT} />);
  expect(line(container).classList.contains('recap-open')).toBe(false);
  expect(recapOpen.get().has('@2')).toBe(true);
});

test('a blocked storage still pins for this page', () => {
  const set = Storage.prototype.setItem;
  Storage.prototype.setItem = () => { throw new Error('quota'); };
  try {
    toggleRecap('@1');
    expect(recapOpen.get().has('@1')).toBe(true);
  } finally {
    Storage.prototype.setItem = set;
  }
});

test('a reload reads the pins back; an unreadable store starts with none', async () => {
  localStorage.setItem('pc_wall_recap_open', '["@4", 5]');
  vi.resetModules();
  let m = await import('../index.ts');
  expect([...m.recapOpen.get()]).toEqual(['@4']);
  localStorage.setItem('pc_wall_recap_open', '{');
  vi.resetModules();
  m = await import('../index.ts');
  expect(m.recapOpen.get().size).toBe(0);
}, 20_000);   // re-imports the module graph: slow on a loaded machine
