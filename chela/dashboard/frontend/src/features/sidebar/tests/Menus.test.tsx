// CMX-277: the sidebar's group, row and VIEW menus on the kit's Menu. What a choice DOES is
// nav.js's (the actions); these pin what each menu offers and which action each choice calls.
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test } from 'vitest';
import { Sidebar, ViewMenu } from '../index.ts';
import { DISPATCHED_KEY, type SidebarState } from '../model.ts';
import { actions, runWin, state, win } from './fixtures.ts';

afterEach(cleanup);

// every item of the open menu: menuitem, menuitemradio, menuitemcheckbox
const entries = () => [...document.querySelectorAll<HTMLElement>('[role="menu"] [role^="menuitem"]')];
const items = () => entries().map(i => i.textContent);
const item = (label: string) => {
  const el = entries().find(i => i.textContent === label);
  if (!el) throw new Error(`no "${label}" in ${JSON.stringify(items())}`);
  return el;
};
const disabled = (label: string) => item(label).hasAttribute('data-disabled');
// The keyboard path: Enter on the trigger opens it, Enter on an item picks it.
const open = (trigger: Element | null) => {
  if (!(trigger instanceof HTMLButtonElement)) throw new Error('the menu trigger is not a <button>');
  fireEvent.keyDown(trigger, { key: 'Enter' });
};
const pick = (label: string) => fireEvent.keyDown(item(label), { key: 'Enter' });
const menuOpen = () => document.querySelector('[role="menu"]') !== null;

function draw(s: SidebarState, acts = actions()) {
  const { container } = render(<Sidebar state={s} actions={acts} />);
  return { el: container, acts };
}

test('the group ⋯ is a keyboard-reachable <button>: Enter opens its menu, Enter picks', () => {
  const { el, acts } = draw(state([win('a'), win('b', { cwd: '/srv/code/beta' })]));
  const more = el.querySelector('.side-group[data-g="/srv/code/alpha"] .group-more');
  expect(more?.tagName).toBe('BUTTON');
  expect(more?.getAttribute('type')).toBe('button');
  expect(more?.getAttribute('aria-haspopup')).toBe('menu');
  open(more);
  expect(screen.getByRole('menu').className).toBe('popover overflow-menu ui-floating side-menu');
  expect(items()).toEqual(['New session', 'Move up', 'Move down', 'Collapse all', 'Expand all', 'Archive all (1)']);
  expect(screen.getByRole('menu').querySelectorAll('.popover-sep')).toHaveLength(3);
  expect(disabled('Move up')).toBe(true);    // the first group
  expect(disabled('Move down')).toBe(false);
  pick('Move down');
  expect(acts.groupMenuAction.mock.calls).toEqual([['/srv/code/alpha', 'down']]);
  expect(menuOpen()).toBe(false);
  expect(acts.toggleGroup).not.toHaveBeenCalled();
  open(el.querySelector('.side-group[data-g="/srv/code/beta"] .group-more'));
  expect(disabled('Move up')).toBe(false);
  expect(disabled('Move down')).toBe(true);  // the last group
  pick('Move up');
  expect(acts.groupMenuAction.mock.calls[1]).toEqual(['/srv/code/beta', 'up']);
});

test('each group-menu choice calls its own action; New session is offered only to a folder', () => {
  const run = runWin('CMX-3', 'agent', { status: 'done' });
  const { el, acts } = draw(state([win('a'), win('busy', { session_status: 'busy' }), run]));
  const openAlpha = () => open(el.querySelector('.side-group[data-g="/srv/code/alpha"] .group-more'));
  for (const [label, act] of [['Collapse all', 'collapse-all'], ['Expand all', 'expand-all'], ['Archive all (1)', 'archive']]) {
    openAlpha();
    pick(label as string);
    expect(acts.groupMenuAction.mock.calls.at(-1)).toEqual(['/srv/code/alpha', act]);
  }
  openAlpha();
  pick('New session');
  expect(acts.groupNewSession.mock.calls).toEqual([['/srv/code/alpha']]);
  open(el.querySelector(`.side-group[data-g="${DISPATCHED_KEY}"] .group-more`));
  expect(items()).toEqual(['Move up', 'Move down', 'Collapse all', 'Expand all', 'Archive all (1)']);
});

test('Archive all counts only the FINISHED rows, and is disabled at 0', () => {
  const orch = win('orch');
  const { el } = draw(state([win('busy', { session_status: 'busy' }), orch, win('q', { needs_human: true })], { orchWid: orch.window_id ?? null }));
  open(el.querySelector('.group-more'));
  expect(items().at(-1)).toBe('Archive all (0)');
  expect(disabled('Archive all (0)')).toBe(true);
});

test('the row menu: a run opens its panes; Archive is enabled only for a finished row not yet archived', () => {
  const run = runWin('CMX-1', 'agent', { status: 'done' });
  const fin = win('fin', { done: true });
  const s = state([win('busy', { session_status: 'busy' }), fin, run]);
  const { el, acts } = draw(s);
  const rowMore = (name: string) => el.querySelector(`.agent-row[data-agent="${name}"] .row-more`);
  open(rowMore('busy'));
  expect(items()).toEqual(['Archive']);
  expect(disabled('Archive')).toBe(true);
  fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' });
  open(rowMore(run.name ?? ''));
  expect(items()).toEqual(['Open agent pane', 'Open judge pane', 'Archive']);
  expect(disabled('Open judge pane')).toBe(true);   // no judge window yet
  pick('Open agent pane');
  expect(acts.rowMenuAction.mock.calls).toEqual([['run:CMX-1', 'agent']]);
  open(rowMore(run.name ?? ''));
  pick('Archive');
  expect(acts.rowMenuAction.mock.calls[1]).toEqual(['run:CMX-1', 'archive']);
  open(rowMore('fin'));
  expect(disabled('Archive')).toBe(false);
  cleanup();
  // already archived (it sits in the Archived group): its way back is Unarchive, not Archive
  const again = draw({ ...s, archived: [`w:${fin.window_id}`] });
  open(again.el.querySelector('.side-archived .agent-row[data-agent="fin"] .row-more'));
  expect(disabled('Archive')).toBe(true);
});

test('Move to group… only under Custom groups; its page moves the row, and stays open until a choice', () => {
  const a = win('a');
  const key = `w:${a.window_id}`;
  const custom = { groups: [{ id: 'g1', name: 'Infra' }, { id: 'g2', name: 'Ops' }], assign: { [key]: 'g2' } };
  const base = state([a, win('b')], { custom });
  let { el, acts } = draw(base);
  open(el.querySelector('.agent-row[data-agent="a"] .row-more'));
  expect(items()).not.toContain('Move to group…');
  cleanup();
  ({ el, acts } = draw({ ...base, view: { ...base.view, groupBy: 'custom' } }));
  fireEvent.contextMenu(el.querySelector('.agent-row[data-agent="a"]') as Element);
  expect(items()).toEqual(['Move to group…', 'Archive']);
  pick('Move to group…');
  expect(menuOpen()).toBe(true);
  expect(items()).toEqual(['Move to group', 'Infra', 'Ops', 'Ungrouped', 'New group…']);
  const checked = screen.getAllByRole('menuitemradio').filter(i => i.getAttribute('aria-checked') === 'true');
  expect(checked.map(i => i.textContent)).toEqual(['Ops']);
  pick('Infra');
  expect(acts.moveToGroup.mock.calls).toEqual([[key, 'g1']]);
  expect(menuOpen()).toBe(false);
  // reopened, it starts on its first page again
  fireEvent.contextMenu(el.querySelector('.agent-row[data-agent="a"]') as Element);
  expect(items()[0]).toBe('Move to group…');
  pick('Move to group…');
  pick('Move to group');   // ‹ back
  expect(items()).toEqual(['Move to group…', 'Archive']);
  pick('Move to group…');
  pick('Ungrouped');
  expect(acts.moveToGroup.mock.calls[1]).toEqual([key, null]);
  // New group…: the prompted group gets the row; a cancelled prompt moves nothing
  acts.newCustomGroup.mockReturnValueOnce({ id: 'g9', name: 'New' }).mockReturnValueOnce(null);
  for (let i = 0; i < 2; i++) {
    fireEvent.contextMenu(el.querySelector('.agent-row[data-agent="a"]') as Element);
    pick('Move to group…');
    pick('New group…');
  }
  expect(acts.newCustomGroup).toHaveBeenCalledTimes(2);
  expect(acts.moveToGroup.mock.calls.slice(2)).toEqual([[key, 'g9']]);
});

function drawView(s: SidebarState | null) {
  const acts = actions();
  render(<ViewMenu state={s} actions={acts} />);
  const btn = screen.getByRole('button', { name: 'View options' });
  return { acts, btn };
}
const rowOf = (label: string) => entries().find(i => i.querySelector('.ov-label')?.textContent === label);
const valueOf = (label: string) => rowOf(label)?.querySelector('.ov-val')?.textContent;

test('the VIEW menu: a <button>; each submenu shows its value; a page opens by Enter and a choice returns to the top', () => {
  const s = state([win('a')], { custom: { groups: [{ id: 'g1', name: 'Infra' }], assign: {} } });
  const { acts, btn } = drawView({ ...s, view: { ...s.view, status: 'active', groupBy: 'state' } });
  expect(btn.tagName).toBe('BUTTON');
  expect(btn.id).toBe('btn-sidebar-view');
  expect(btn.className).toBe('side-view-btn');
  open(btn);
  expect(screen.getByRole('menu').className).toContain('side-menu view-menu');
  expect(entries().map(i => i.querySelector('.ov-label')?.textContent)).toEqual([
    'Status', 'Environment', 'Last activity', 'Group by', 'Sort by', 'Custom groups', 'Show empty groups', 'Show PR status']);
  expect([valueOf('Status'), valueOf('Environment'), valueOf('Last activity'), valueOf('Group by'), valueOf('Sort by'), valueOf('Custom groups')])
    .toEqual(['Active', '3 selected', 'All', 'State', 'Name', '1']);
  expect(rowOf('Status')?.getAttribute('aria-haspopup')).toBe('menu');
  expect(rowOf('Status')?.querySelector('.ov-chev')).toBeTruthy();
  pick(rowOf('Group by')?.textContent ?? '');
  expect(menuOpen()).toBe(true);
  expect(items()).toEqual(['Group by', 'Date', 'Folder', 'State', 'Custom groups', 'None']);
  expect(screen.getAllByRole('menuitemradio').filter(i => i.getAttribute('aria-checked') === 'true').map(i => i.textContent)).toEqual(['State']);
  pick('Folder');
  expect(acts.viewMenuAction.mock.calls).toEqual([['group:folder']]);
  expect(menuOpen()).toBe(true);   // a choice keeps the menu open
  expect(rowOf('Status')).toBeTruthy();   // back on the top page
  for (const [page, choice, act] of [['Status', 'Archived', 'status:archived'], ['Last activity', '30d', 'activity:30d'], ['Sort by', 'Created', 'sort:created']]) {
    pick(rowOf(page as string)?.textContent ?? '');
    pick(choice as string);
    expect(acts.viewMenuAction.mock.calls.at(-1)).toEqual([act]);
    expect(rowOf('Status')).toBeTruthy();
  }
});

test('the VIEW menu: Environment and the toggles are multi-selects that stay where they are', () => {
  const s = state([win('a')]);
  const { acts, btn } = drawView(s);
  open(btn);
  pick(rowOf('Environment')?.textContent ?? '');
  const boxes = () => screen.getAllByRole('menuitemcheckbox').map(i => [i.textContent, i.getAttribute('aria-checked')]);
  expect(boxes()).toEqual([['Interactive', 'true'], ['Dispatched agents', 'true'], ['Judges', 'false'], ['Background sessions', 'true']]);
  pick('Judges');
  expect(acts.viewMenuAction.mock.calls).toEqual([['env:judge']]);
  expect(items()[0]).toBe('Environment');   // still on its page
  pick('Environment');   // ‹ back
  pick('Show PR status');
  expect(acts.viewMenuAction.mock.calls[1]).toEqual(['toggle:showPR']);
  expect(rowOf('Show PR status')?.getAttribute('aria-checked')).toBe('true');
  pick('Show empty groups');
  expect(acts.viewMenuAction.mock.calls[2]).toEqual(['toggle:showEmpty']);
  expect(menuOpen()).toBe(true);
});

test('the VIEW menu: the Custom groups page renames, deletes and creates, and says when there are none', () => {
  const { acts, btn } = drawView(state([win('a')], { custom: { groups: [{ id: 'g1', name: 'Ops' }], assign: {} } }));
  open(btn);
  pick(rowOf('Custom groups')?.textContent ?? '');
  expect(items()).toEqual(['Custom groups', 'Rename Ops', 'Delete Ops', 'New group…']);
  pick('Rename Ops');
  pick('Delete Ops');
  pick('New group…');
  expect(acts.renameCustomGroup.mock.calls).toEqual([['g1']]);
  expect(acts.deleteCustomGroup.mock.calls).toEqual([['g1']]);
  expect(acts.newCustomGroup).toHaveBeenCalledTimes(1);
  expect(items()[0]).toBe('Custom groups');
  cleanup();
  // before the first publish (no state yet): the defaults, and no groups
  const blank = drawView(null);
  open(blank.btn);
  expect(valueOf('Status')).toBe('All');
  pick(rowOf('Custom groups')?.textContent ?? '');
  expect(items()).toEqual(['Custom groups', 'No custom groups yet', 'New group…']);
  expect(disabled('No custom groups yet')).toBe(true);
});

test('a choice page marks ONLY the current value: Status, Last activity, Sort by', () => {
  const s = state([win('a')]);
  const { btn } = drawView({ ...s, view: { ...s.view, status: 'archived', activity: '30d', sort: 'created' } });
  open(btn);
  const on = () => entries().filter(i => i.getAttribute('aria-checked') === 'true').map(i => i.textContent);
  for (const [page, want] of [['Status', 'Archived'], ['Last activity', '30d'], ['Sort by', 'Created']]) {
    pick(rowOf(page as string)?.textContent ?? '');
    expect(on()).toEqual([want]);
    // the check mark is drawn on the current choice alone
    expect(entries().filter(i => i.querySelector('.ov-check')).map(i => i.textContent)).toEqual([want]);
    pick(page as string);   // ‹ back
  }
});

test('the kit draws a back chevron only on a back row, a submenu marker only on a submenu row', () => {
  const { btn } = drawView(state([win('a')]));
  open(btn);
  expect(entries().filter(i => i.getAttribute('aria-haspopup') === 'menu').map(i => i.querySelector('.ov-label')?.textContent))
    .toEqual(['Status', 'Environment', 'Last activity', 'Group by', 'Sort by', 'Custom groups']);
  expect(document.querySelectorAll('[role="menu"] .ov-chev.back')).toHaveLength(0);
  pick(rowOf('Sort by')?.textContent ?? '');
  expect(entries().filter(i => i.querySelector('.ov-chev.back')).map(i => i.textContent)).toEqual(['Sort by']);
  expect(item('Sort by').className).toBe('popover-item ov-item ov-back');
  expect(item('Name').className).toBe('popover-item ov-item on');   // the fixture sorts by Name
  expect(item('Created').className).toBe('popover-item ov-item');
});

test('a page change moves focus to the new page\'s first item; a closed menu reopens on its top page', async () => {
  const { btn } = drawView(state([win('a')]));
  open(btn);
  pick(rowOf('Group by')?.textContent ?? '');
  await act(() => new Promise(r => setTimeout(r, 0)));
  expect(document.activeElement?.textContent).toBe('Group by');
  pick('Group by');   // ‹ back
  await act(() => new Promise(r => setTimeout(r, 0)));
  expect(document.activeElement?.querySelector('.ov-label')?.textContent).toBe('Status');
  pick(rowOf('Sort by')?.textContent ?? '');
  fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' });
  expect(menuOpen()).toBe(false);
  open(btn);
  expect(rowOf('Status')).toBeTruthy();
});

test('Move to group: a row assigned to a DELETED group reads Ungrouped, unassigned reads Ungrouped', () => {
  for (const assigned of ['gone', undefined]) {
    const a = win('a');
    const custom = { groups: [{ id: 'g1', name: 'Infra' }], assign: assigned ? { [`w:${a.window_id}`]: assigned } : {} };
    const s = state([a], { custom });
    const { el } = draw({ ...s, view: { ...s.view, groupBy: 'custom' } });
    fireEvent.contextMenu(el.querySelector('.agent-row[data-agent="a"]') as Element);
    pick('Move to group…');
    expect(screen.getAllByRole('menuitemradio').filter(i => i.getAttribute('aria-checked') === 'true').map(i => i.textContent))
      .toEqual(['Ungrouped']);
    cleanup();
  }
});
