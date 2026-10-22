// CMX-242 (CMX-212 P30): the Wall's layout toolbar. Ported from wall_grid_toolbar and
// wall_grid_toolbar_stored (the fold, its stored choice) and wallnav (presets and lock inline,
// no Layout menu) .test.mjs; the grid applying each change stays legacy (terminals.js).
import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, expect, test } from 'vitest';
import { DEFAULT_PRESET, termMode, wallLayout } from '../../../shared/store/wall.ts';
import { currentLayout, FocusGlyph, GridGlyph, mountWallToolbar, WallToolbar } from '../index.ts';
import { WALL_PRESETS } from '../model.ts';

beforeEach(() => {
  localStorage.clear();
  termMode.set('wall');
  wallLayout.set({ preset: DEFAULT_PRESET, locked: false, auto: false, focus: false });
});
afterEach(cleanup);

const $ = <T extends HTMLElement = HTMLElement>(c: ParentNode, sel: string) => c.querySelector<T>(sel) as T;
const open = (c: ParentNode) => $(c, '#term-grid-toggle').getAttribute('aria-expanded') === 'true';
const svg = (el: Element) => el.querySelector('svg')?.outerHTML;

test('it shows in wall mode only: hidden in single mode, and before the Wall has rendered', () => {
  const { container } = render(<WallToolbar />);
  expect($(container, '#term-wall-grid').hidden).toBe(false);
  act(() => termMode.set('single'));
  expect($(container, '#term-wall-grid').hidden).toBe(true);
  act(() => termMode.set(null));
  expect($(container, '#term-wall-grid').hidden).toBe(true);
});

test('expanded: six presets, lock, auto-arrange and Focus, inline (no Layout menu)', () => {
  const { container } = render(<WallToolbar />);
  expect(open(container)).toBe(true);   // Focus off, no stored choice
  expect($(container, '#term-grid-row').hidden).toBe(false);
  const presets = [...container.querySelectorAll<HTMLElement>('#term-grid-presets .gl-btn')];
  expect(presets.map((b) => [b.dataset.cols, b.dataset.rows, b.title])).toEqual(WALL_PRESETS.map((p) => [String(p.cols), String(p.rows), p.label]));
  expect(presets.map((b) => b.getAttribute('aria-label'))).toEqual(WALL_PRESETS.map((p) => p.label));
  for (const id of ['term-lock-btn', 'term-auto-btn', 'term-focus-btn']) expect($(container, `#${id}`).closest('.popover'), id).toBeNull();
  expect(container.querySelector('#layout-menu')).toBeNull();
  // the active preset is the store's
  expect(presets.map((b) => b.classList.contains('active'))).toEqual([false, true, false, false, false, false]);
});

test('Focus on and no stored choice: it rests FOLDED to one control showing the Focus glyph', () => {
  wallLayout.set({ ...wallLayout.get(), focus: true });
  const { container } = render(<WallToolbar />);
  expect(open(container)).toBe(false);
  expect($(container, '#term-grid-row').hidden).toBe(true);
  expect(svg($(container, '#term-grid-toggle'))).toBe(svg($(container, '#term-focus-btn')));
  expect($(container, '#term-grid-toggle .grid-chevron svg')).not.toBeNull();
  expect($(container, '#term-grid-toggle').title).toBe('Layout: Focus, show the layout toolbar');
});

test.each([['0', true], ['1', false]])('a stored choice %j beats the Focus default (open: %j)', (stored, expanded) => {
  localStorage.setItem('pc_wall_grid_collapsed', stored);
  wallLayout.set({ ...wallLayout.get(), focus: stored === '0' });
  const { container } = render(<WallToolbar />);
  expect(open(container)).toBe(expanded);
  expect(localStorage.getItem('pc_wall_grid_collapsed')).toBe(stored);   // loading never rewrites it
});

test('the toggle is the one remembered choice', () => {
  const { container } = render(<WallToolbar />);
  fireEvent.click($(container, '#term-grid-toggle'));
  expect(open(container)).toBe(false);
  expect(localStorage.getItem('pc_wall_grid_collapsed')).toBe('1');
  fireEvent.click($(container, '#term-grid-toggle'));
  expect(open(container)).toBe(true);
  expect($(container, '#term-grid-toggle').classList.contains('grid-open')).toBe(true);
  expect(localStorage.getItem('pc_wall_grid_collapsed')).toBe('0');
});

test('choosing a preset writes the store (persisted) and folds the row, without rewriting the choice', () => {
  localStorage.setItem('pc_wall_grid_collapsed', '0');
  const { container } = render(<WallToolbar />);
  fireEvent.click($(container, '#term-grid-presets .gl-btn[data-preset="5"]'));
  expect(wallLayout.get().preset).toEqual({ cols: 3, rows: 2 });
  expect(JSON.parse(localStorage.getItem('pc_wall_preset') ?? 'null')).toEqual({ cols: 3, rows: 2 });
  expect(open(container)).toBe(false);
  expect(localStorage.getItem('pc_wall_grid_collapsed')).toBe('0');
  expect($(container, '#term-grid-presets .gl-btn[data-preset="5"]').classList.contains('active')).toBe(true);
  expect(svg($(container, '#term-grid-toggle'))).toBe(svg($(container, '#term-grid-presets .gl-btn[data-preset="5"]')));
});

test('choosing the active preset again is still a choice (it re-tidies the wall)', () => {
  const { container } = render(<WallToolbar />);
  const before = wallLayout.get().preset;
  fireEvent.click($(container, '#term-grid-presets .gl-btn[data-preset="1"]'));
  expect(wallLayout.get().preset).toEqual(before);
  expect(wallLayout.get().preset).not.toBe(before);
});

test('lock toggles in place (it does not fold the row), and says what it does', () => {
  const { container } = render(<WallToolbar />);
  const lock = $(container, '#term-lock-btn');
  expect(lock.title).toBe('Lock layout: drag swaps panes, resize off');
  const unlocked = svg(lock);
  fireEvent.click(lock);
  expect(wallLayout.get().locked).toBe(true);
  expect(localStorage.getItem('pc_wall_locked')).toBe('1');
  expect(lock.classList.contains('active')).toBe(true);
  expect(lock.getAttribute('aria-pressed')).toBe('true');
  expect(lock.title).toMatch(/^Layout locked:/);
  expect(svg(lock)).not.toBe(unlocked);   // the shackle closes
  expect(open(container)).toBe(true);
  fireEvent.click(lock);
  expect(wallLayout.get().locked).toBe(false);
});

test('auto-arrange and Focus own the layout: one on turns the other off, and each folds the row', () => {
  const { container } = render(<WallToolbar />);
  fireEvent.click($(container, '#term-auto-btn'));
  expect(wallLayout.get()).toMatchObject({ auto: true, focus: false });
  expect(open(container)).toBe(false);
  expect(svg($(container, '#term-grid-toggle'))).toBe(svg($(container, '#term-auto-btn')));
  expect($(container, '#term-auto-btn').title).toMatch(/^Auto-arrange ON/);
  fireEvent.click($(container, '#term-grid-toggle'));
  fireEvent.click($(container, '#term-focus-btn'));
  expect(wallLayout.get()).toMatchObject({ auto: false, focus: true });
  expect(localStorage.getItem('pc_wall_auto')).toBe('0');
  expect(localStorage.getItem('pc_wall_focus')).toBe('1');
  expect($(container, '#term-focus-btn').title).toMatch(/^Focus ON/);
  fireEvent.click($(container, '#term-grid-toggle'));
  fireEvent.click($(container, '#term-focus-btn'));
  expect(wallLayout.get()).toMatchObject({ auto: false, focus: false });
  expect(svg($(container, '#term-grid-toggle'))).toBe(svg($(container, '#term-grid-presets .gl-btn.active')));
});

test('Esc folds it and hands focus back to the toggle, not a control that just vanished', () => {
  const { container } = render(<WallToolbar />);
  const preset = $(container, '#term-grid-presets .gl-btn');
  preset.focus();
  fireEvent.keyDown(document, { key: 'Escape' });
  expect(open(container)).toBe(false);
  expect(document.activeElement).toBe($(container, '#term-grid-toggle'));
});

test('Esc with focus elsewhere folds it and leaves focus alone; other keys do nothing', () => {
  const { container } = render(<><WallToolbar /><input id="elsewhere" /></>);
  const other = $(container, '#elsewhere');
  other.focus();
  fireEvent.keyDown(document, { key: 'a' });
  expect(open(container)).toBe(true);
  fireEvent.keyDown(document, { key: 'Escape' });
  expect(open(container)).toBe(false);
  expect(document.activeElement).toBe(other);
});

test('a press outside folds it; a press inside does not', () => {
  const { container } = render(<><WallToolbar /><div id="stage" /></>);
  fireEvent.pointerDown($(container, '#term-grid-presets'));
  expect(open(container)).toBe(true);
  fireEvent.pointerDown($(container, '#stage'));
  expect(open(container)).toBe(false);
});

test('the folded control names the current layout: Focus, then auto-arrange, then the preset', () => {
  const l = { preset: { cols: 3, rows: 1 }, locked: false, auto: false, focus: false };
  expect(currentLayout(l).label).toBe('3 columns');
  expect(currentLayout({ ...l, auto: true }).label).toBe('Auto-arrange');
  expect(currentLayout({ ...l, auto: true, focus: true }).label).toBe('Focus');
  expect(currentLayout({ ...l, preset: { cols: 5, rows: 5 } }).label).toBe('2 columns');   // unknown: the default
});

test('a grid glyph is cols × rows rounded rects; Focus is its own shape', () => {
  const { container } = render(<><GridGlyph cols={3} rows={2} /><FocusGlyph /></>);
  const [grid, focus] = [...container.querySelectorAll('svg')];
  if (!grid || !focus) throw new Error('missing glyphs');
  expect(grid.querySelectorAll('rect').length).toBe(6);
  expect(grid.getAttribute('aria-hidden')).toBe('true');
  expect(focus.querySelectorAll('rect').length).toBe(3);
});

test('mountWallToolbar draws into its slot at once, once', () => {
  const slot = document.createElement('span');
  document.body.append(slot);
  mountWallToolbar(slot);
  expect(slot.querySelector('#term-grid-toggle')).not.toBeNull();
  mountWallToolbar(slot);
  expect(slot.querySelectorAll('#term-grid-toggle').length).toBe(1);
  mountWallToolbar(null);
  slot.remove();
});
