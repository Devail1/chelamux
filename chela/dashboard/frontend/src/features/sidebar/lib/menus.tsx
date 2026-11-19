// CMX-277: the sidebar's menus on the kit's Menu (Radix): a group's ⋯ (or a right-click on its
// header), a row's ⋯ (or a right-click on it), and the VIEW menu on the Sessions header. A
// submenu is a second PAGE of the same menu (‹ back at its top), never a hover flyout, so a
// phone reaches it by tap. What a choice DOES is still nav.js's (`actions`): the layout, the
// archive and the collapsed groups are its state until slice C.
import { useState, type MouseEvent } from 'react';
import { icons } from '../../../shared/icons/index.ts';
import { IconButton, Menu, type MenuItem } from '../../../shared/ui/index.ts';
import {
  ENV_KINDS, GROUP_MODES, SORTS, VIEW_ACTIVITY, VIEW_STATUS, type Group, type Item, type SidebarState,
  PINNED_NO_ARCHIVE, assignKey, isArchivable, isRunsRow, isPinned, normalizeView, pinKey,
} from '../model.ts';
import type { SidebarActions } from './actions.ts';

const STATUS_LABEL: Record<string, string> = { active: 'Active', all: 'All', archived: 'Archived' };
const ENV_LABEL: Record<string, string> = { interactive: 'Interactive', dispatched: 'Dispatched agents', judge: 'Judges', background: 'Background sessions' };
const ACTIVITY_LABEL: Record<string, string> = { '1d': '1d', '7d': '7d', '30d': '30d', all: 'All' };
const GROUP_LABEL: Record<string, string> = { date: 'Date', folder: 'Folder', state: 'State', custom: 'Custom groups', none: 'None' };
const SORT_LABEL: Record<string, string> = { activity: 'Last activity', name: 'Name', created: 'Created', manual: 'Manual' };

// A click on a trigger inside a row or a group header must not also select the row or fold
// the group.
const stop = (e: MouseEvent) => e.stopPropagation();

/** A menu that a right-click on `onContext`'s element opens too: its state and that handler. */
export function useMenuOpen() {
  const [open, setOpen] = useState(false);
  const onContext = (e: MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setOpen(true);
  };
  return { open, setOpen, onContext };
}

type Open = ReturnType<typeof useMenuOpen>;

/** The group's menu: New session · Move up / Move down · Collapse all / Expand all · Archive all (N). */
export function GroupMenu({ g, index, count, s, actions, menu }: {
  g: Group; index: number; count: number; s: SidebarState; actions: SidebarActions; menu: Open;
}) {
  // "Archive all" archives the group's FINISHED rows only (model isArchivable).
  const n = g.items.filter(it => isArchivable(it, { wants: s.wants, orchWid: s.orchWid, pins: s.pins })).length;
  const act = (a: string) => () => void actions.groupMenuAction(g.key, a);
  const items: MenuItem[] = [
    ...(g.cwd ? [{ label: 'New session', onSelect: () => void actions.groupNewSession(g.key) }, 'separator' as const] : []),
    { label: 'Move up', onSelect: act('up'), disabled: index === 0 },
    { label: 'Move down', onSelect: act('down'), disabled: index === count - 1 },
    'separator',
    { label: 'Collapse all', onSelect: act('collapse-all') },
    { label: 'Expand all', onSelect: act('expand-all') },
    'separator',
    { label: `Archive all (${n})`, onSelect: act('archive'), disabled: n === 0 },
  ];
  return (
    <Menu className="side-menu" open={menu.open} onOpenChange={menu.setOpen} items={items}
      trigger={<IconButton className="group-more" data-g={g.key} label={`${g.label} menu`} onClick={stop}>
        <icons.more size={14} aria-hidden="true" /></IconButton>} />
  );
}

/** A single-session folder's place in the group order (CMX-379): its header's actions ride the row's menu. */
export interface RowGroup { g: Group; index: number; count: number }

/** The header actions a single-session folder row carries (CMX-379): New session, Move up / down, Collapse / Expand all. */
function folderItems({ g, index, count }: RowGroup, actions: SidebarActions): MenuItem[] {
  const act = (a: string) => () => void actions.groupMenuAction(g.key, a);
  return [
    { label: 'New session in this folder', onSelect: () => void actions.groupNewSession(g.key) },
    'separator',
    { label: 'Move up', onSelect: act('up'), disabled: index === 0 },
    { label: 'Move down', onSelect: act('down'), disabled: index === count - 1 },
    'separator',
    { label: 'Collapse all', onSelect: act('collapse-all') },
    { label: 'Expand all', onSelect: act('expand-all') },
    'separator',
  ];
}

/** A row's menu: a run's panes, Move to group… (under Custom groups only, CMX-74), Pin / Unpin
 * (CMX-390), Archive. */
export function RowMenu({ it, s, actions, menu, group, archived = false }: {
  it: Item; s: SidebarState; actions: SidebarActions; menu: Open; group?: RowGroup; archived?: boolean;
}) {
  const [page, setPage] = useState<'main' | 'move'>('main');
  const onOpenChange = (o: boolean) => {
    menu.setOpen(o);
    if (!o) setPage('main');
  };
  const items: MenuItem[] = [];
  if (page === 'move') {
    const ak = assignKey(it);
    const cur = s.custom.assign[ak];
    const has = cur != null && s.custom.groups.some(g => g.id === cur);
    items.push({ label: 'Move to group', back: true, keepOpen: true, onSelect: () => setPage('main') }, 'separator');
    for (const g of s.custom.groups) {
      items.push({ label: g.name, check: 'radio', checked: g.id === cur, onSelect: () => actions.moveToGroup(ak, g.id) });
    }
    items.push({ label: 'Ungrouped', check: 'radio', checked: !has, onSelect: () => actions.moveToGroup(ak, null) }, 'separator', {
      label: 'New group…',
      onSelect: () => {
        const g = actions.newCustomGroup();
        if (g) actions.moveToGroup(ak, g.id);
      },
    });
  } else {
    if (group) items.push(...folderItems(group, actions));
    if (it.kind === 'run') {
      if (it.agent) items.push({ label: 'Open agent pane', onSelect: () => void actions.rowMenuAction(it.key, 'agent') });
      items.push({ label: 'Open judge pane', disabled: !it.judge, onSelect: () => void actions.rowMenuAction(it.key, 'judge') }, 'separator');
    }
    // CMX-438: a dispatched run lives in Runs, never in a custom group
    const runs = isRunsRow(it, { orchWid: s.orchWid, dispatchOff: s.dispatchOff });
    if (s.view.groupBy === 'custom' && !runs) items.push({ label: 'Move to group…', keepOpen: true, onSelect: () => setPage('move') }, 'separator');
    // CMX-390: only a session can be pinned (by its session id), and an archived row cannot.
    const pins = s.pins ?? [];
    const pinned = isPinned(it, pins);
    if (pinKey(it) && !archived) {
      items.push({ label: pinned ? 'Unpin' : 'Pin', icon: icons.pin, onSelect: () => void actions.rowMenuAction(it.key, pinned ? 'unpin' : 'pin') });
    }
    // A row already in the Archived section is not archived twice (its way back is Unarchive).
    const archivable = !s.archived.includes(it.key) && isArchivable(it, { wants: s.wants, orchWid: s.orchWid, pins });
    items.push(pinned
      ? { label: 'Archive', disabled: true, title: PINNED_NO_ARCHIVE, onSelect: () => {},
        trailing: <span className="menu-hint">{PINNED_NO_ARCHIVE}</span> }
      : { label: 'Archive', disabled: !archivable, onSelect: () => void actions.rowMenuAction(it.key, 'archive') });
  }
  const label = it.kind === 'run' ? 'Run menu' : 'Row menu';
  return (
    <Menu className="side-menu" open={menu.open} onOpenChange={onOpenChange} items={items}
      trigger={<IconButton className="row-more" label={label} onClick={stop}><icons.more size={14} aria-hidden="true" /></IconButton>} />
  );
}

type Choice = Exclude<MenuItem, 'separator'>;
type Page = 'main' | 'status' | 'env' | 'activity' | 'group' | 'sort' | 'custom';

/** The VIEW menu (CMX-66): Status / Environment / Last activity / Group by / Sort by / Custom groups, and two toggles. */
export function ViewMenu({ state, actions }: { state: SidebarState | null; actions: SidebarActions }) {
  const [page, setPage] = useState<Page>('main');
  const v = state ? state.view : normalizeView(null);
  const groups = state ? state.custom.groups : [];
  const back = (title: string): MenuItem[] => [{ label: title, back: true, keepOpen: true, onSelect: () => setPage('main') }, 'separator'];
  const sub = (label: string, value: string, to: Page): MenuItem => ({ label, value, submenu: true, keepOpen: true, onSelect: () => setPage(to) });
  // A single choice returns to the top page (its new value showing); a multi-select or a
  // toggle stays where it is. The menu stays open either way, so several choices take one trip.
  const pick = (label: string, act: string, on: boolean, stay = false): Choice => ({
    label, check: 'radio', checked: on, keepOpen: true,
    onSelect: () => {
      actions.viewMenuAction(act);
      if (!stay) setPage('main');
    },
  });
  const toggle = (label: string, act: string, on: boolean): Choice => ({ ...pick(label, act, on, true), check: 'checkbox' });
  const pages: Record<Page, () => MenuItem[]> = {
    status: () => [...back('Status'), ...VIEW_STATUS.map(k => pick(STATUS_LABEL[k] ?? k, `status:${k}`, v.status === k))],
    env: () => [...back('Environment'), ...ENV_KINDS.map(k => toggle(ENV_LABEL[k] ?? k, `env:${k}`, v.env.includes(k)))],
    activity: () => [...back('Last activity'), ...Object.keys(VIEW_ACTIVITY).map(k => pick(ACTIVITY_LABEL[k] ?? k, `activity:${k}`, v.activity === k))],
    // None sits apart, below a divider, as on the desktop
    group: () => [...back('Group by'), ...GROUP_MODES.filter(k => k !== 'none').map(k => pick(GROUP_LABEL[k] ?? k, `group:${k}`, v.groupBy === k)),
      'separator', pick('None', 'group:none', v.groupBy === 'none')],
    sort: () => [...back('Sort by'), ...SORTS.map(k => pick(SORT_LABEL[k] ?? k, `sort:${k}`, v.sort === k))],
    custom: () => [...back('Custom groups'),
      ...groups.flatMap((g): MenuItem[] => [
        { label: `Rename ${g.name}`, keepOpen: true, onSelect: () => actions.renameCustomGroup(g.id) },
        { label: `Delete ${g.name}`, keepOpen: true, onSelect: () => actions.deleteCustomGroup(g.id) },
      ]),
      ...(groups.length ? [] : [{ label: 'No custom groups yet', disabled: true, onSelect: () => {} }]),
      'separator', { label: 'New group…', keepOpen: true, onSelect: () => void actions.newCustomGroup() }],
    main: () => [
      sub('Status', STATUS_LABEL[v.status] ?? v.status, 'status'),
      sub('Environment', `${v.env.length} selected`, 'env'),
      sub('Last activity', ACTIVITY_LABEL[v.activity] ?? v.activity, 'activity'),
      'separator',
      sub('Group by', GROUP_LABEL[v.groupBy] ?? v.groupBy, 'group'),
      sub('Sort by', SORT_LABEL[v.sort] ?? v.sort, 'sort'),
      sub('Custom groups', String(groups.length), 'custom'),
      'separator',
      toggle('Show empty groups', 'toggle:showEmpty', v.showEmpty),
      toggle('Show PR status', 'toggle:showPR', v.showPR),
      'separator',
      // CMX-434: a dialog of proposals; nothing is renamed until it applies them.
      { label: 'Suggest names…', onSelect: () => actions.suggestNames() },
    ],
  };
  return (
    <Menu className="side-menu view-menu" items={pages[page]()} onOpenChange={o => { if (!o) setPage('main'); }}
      trigger={<IconButton className="side-view-btn" id="btn-sidebar-view" label="View options">
        <icons.view size={14} aria-hidden="true" /></IconButton>} />
  );
}
