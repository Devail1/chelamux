// CMX-248 (CMX-212 P34): the page is one React root. What each slot draws, the panels the
// views need, the controls the shell owns itself (the ☰ pair, the scrim, the jump box), and
// the sidebar's slots, which repaint before a publish returns (its controller reads them back).
import { act, cleanup, fireEvent } from '@testing-library/react';
import { isValidElement, type ReactElement, type ReactNode } from 'react';
import type { Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { BOOT } from '../../../shared/api/index.ts';
import { createBridge, installBridge } from '../../../shared/bridge/index.ts';
import { view } from '../../../shared/store/view.ts';
import { closePalette, palette } from '../../palette/index.ts';
import * as sb from '../../sidebar/controller.ts';
import { normalizeView, type SidebarState } from '../../sidebar/model.ts';
import { mountApp, mountSlots, selectAgent, selectView, sidebarActions, SLOTS, VIEWS } from '../index.ts';
import { panelId } from '../views.ts';

const roots: Root[] = [];
const $ = (sel: string) => document.querySelector<HTMLElement>(sel);
const must = (sel: string) => {
  const el = $(sel);
  if (!el) throw new Error(`no ${sel}`);
  return el;
};

function mount() {
  document.body.innerHTML = '<div id="root"></div>';
  const root = mountApp(document);
  if (!root) throw new Error('mountApp drew nothing');
  roots.push(root);
}

// jsdom has no matchMedia: a desktop unless a test says phone.
const media = (phone: boolean) => (q: string) => ({ matches: phone && /max-width:\s*768px/.test(q), addEventListener() {}, removeEventListener() {} });

beforeEach(() => {
  vi.stubGlobal('fetch', () => new Promise(() => {})); // every read held: the shell is what paints
  vi.stubGlobal('matchMedia', media(false));
  window.chelaBridge = createBridge();
});

afterEach(() => {
  act(() => roots.splice(0).forEach((r) => r.unmount()));
  cleanup();
  document.body.innerHTML = '';
  document.body.className = '';
  vi.unstubAllGlobals();
  view.set('work');
});

test('mountApp draws <App> into #root, synchronously; a page without #root gets nothing', () => {
  document.body.innerHTML = '';
  expect(mountApp(document)).toBeNull();
  expect(document.body.innerHTML).toBe('');
  mount();
  expect($('#root > .app > aside.sidebar')).not.toBeNull();
  expect($('#root > .app > main.canvas#canvas')).not.toBeNull();
});

test('every slot draws its content in its place', () => {
  mount();
  const inSlot = (slot: string, sel: string) => [slot, $(`#${slot} ${sel}`) !== null];
  expect([
    inSlot('decisions-island', '#btn-decisions'), inSlot('sidebar-new-island', '.sidebar-new-btn'),
    inSlot('sessions-count-island', '#hdr-agents'), inSlot('sidebar-view-menu', '#btn-sidebar-view'),
    inSlot('sidebar-agents', '.side-empty'), inSlot('resources-island', '#resources-strip'),
    inSlot('primary-menu-island', '#btn-primary-menu'), inSlot('sidebar-resize', '.sidebar-resize'),
    inSlot('panel-work', '#work-seg'), inSlot('shares-pill-row', '#term-shares'),
    inSlot('keybar-island', '#term-keybar'), inSlot('keybar-island', '#term-bar'),
    inSlot('new-fab-island', '#btn-new-mobile'), inSlot('status-health-island', '#status-health-warn'),
    inSlot('shares-pill-float', '#btn-shares'), inSlot('access-req-island', '#btn-access-req'),
  ]).toEqual([
    'decisions-island', 'sidebar-new-island', 'sessions-count-island', 'sidebar-view-menu', 'sidebar-agents',
    'resources-island', 'primary-menu-island', 'sidebar-resize', 'panel-work', 'shares-pill-row', 'keybar-island',
    'keybar-island', 'new-fab-island', 'status-health-island', 'shares-pill-float', 'access-req-island',
  ].map((s) => [s, true]));
  // The safety cluster floats OUTSIDE the off-canvas sidebar (CMX-377); the row copies sit in the Wall's toolbar.
  expect($('.safety-float > #shares-pill-float > #btn-shares')).not.toBeNull();
  expect($('.sidebar .safety-float, .sidebar #btn-shares')).toBeNull();
  expect($('#panel-terminals > .term-toolbar > #shares-pill-row > #term-shares')).not.toBeNull();
  expect($('#panel-terminals > .term-toolbar > #term-approvals-island')).not.toBeNull();
  expect($('.sidebar-quick > #sidebar-new-island + .sidebar-jump > #sidebar-jump-input')).not.toBeNull();
});

/** The element a slot draws; its props say what it was handed. */
const drawn = (id: string) => (SLOTS[id] as () => ReactNode)() as ReactElement<Record<string, unknown>>;
const kids = (el: ReactElement<{ children?: ReactNode }>) => ([] as ReactNode[]).concat(el.props.children).filter(isValidElement);

test('each pill copy is handed its own id, and the inbox the sidebar\'s jump to the orchestrator', () => {
  expect([drawn('shares-pill-row').props.id, drawn('shares-pill-float').props.id]).toEqual(['term-shares', 'btn-shares']);
  expect([drawn('term-approvals-island').props.id, drawn('approvals-island').props.id]).toEqual(['term-approvals', 'btn-approvals']);
  const [button] = kids(drawn('decisions-island'));
  expect((button as ReactElement<{ jump: unknown }>).props.jump).toBe(sb.jumpToOrchestrator);
});

test('every non-virtual view has its panel, and the store\'s view is the one shown', () => {
  mount();
  for (const v of VIEWS) expect([v.id, !!document.getElementById(panelId(v.id))]).toEqual([v.id, true]);
  const active = () => [...document.querySelectorAll('.panel.active')].map((p) => p.id);
  expect(active()).toEqual(['panel-work']);
  act(() => view.set('terminals'));
  expect(active()).toEqual(['panel-terminals']);
  act(() => view.set('agent-detail'));
  expect(active()).toEqual(['panel-agent-detail']);
  // A hidden panel stays mounted: the Wall's live frames must never reload.
  expect($('#panel-terminals #term-stage')).not.toBeNull();
  expect($('#panel-agent-detail > #agent-detail')).not.toBeNull();
});

test('with terminals off the shell draws no Wall', () => {
  const was = BOOT.terminalsEnabled;
  BOOT.terminalsEnabled = false;
  try {
    mount();
    expect([$('#panel-terminals'), $('#term-stage'), $('#keybar-island'), $('#term-approvals-island')]).toEqual([null, null, null, null]);
    expect($('#panel-work')).not.toBeNull();
  } finally {
    BOOT.terminalsEnabled = was;
  }
});

test('the Wall\'s own elements are empty for Gridstack and the Wall: the stage, the hidden tray, the toolbar slot', () => {
  mount();
  expect(must('#term-stage').childNodes.length).toBe(0);
  expect(must('#term-min-dock').hidden).toBe(true);
  expect(must('#wall-toolbar-island').className).toBe('island-slot');
});

test('the ☰ folds the desktop sidebar to the rail and back; its aria-expanded starts from the restored state', () => {
  document.body.classList.add('sidebar-collapsed'); // index.html's pre-paint restore
  mount();
  const btn = must('#btn-menu');
  expect([btn.getAttribute('aria-expanded'), btn.getAttribute('aria-label')]).toEqual(['false', 'Toggle sidebar']);
  fireEvent.click(btn);
  expect([document.body.classList.contains('sidebar-collapsed'), btn.getAttribute('aria-expanded')]).toEqual([false, 'true']);
  fireEvent.click(btn);
  expect(document.body.classList.contains('sidebar-collapsed')).toBe(true);
});

test('an expanded sidebar\'s ☰ starts aria-expanded', () => {
  mount();
  expect(must('#btn-menu').getAttribute('aria-expanded')).toBe('true');
});

test('on a phone the floating ☰ opens the drawer and the scrim shuts it', () => {
  vi.stubGlobal('matchMedia', media(true));
  mount();
  const fab = must('#btn-menu-mobile');
  expect([fab.getAttribute('aria-label'), fab.getAttribute('aria-expanded')]).toEqual(['Open menu', 'false']);
  fireEvent.click(fab);
  expect([must('.sidebar').classList.contains('open'), must('#sidebar-scrim').classList.contains('open'), fab.getAttribute('aria-expanded')])
    .toEqual([true, true, 'true']);
  fireEvent.click(must('#sidebar-scrim'));
  expect([must('.sidebar').classList.contains('open'), must('#sidebar-scrim').classList.contains('open')]).toEqual([false, false]);
});

test('the scrim never folds the desktop rail', () => {
  mount();
  fireEvent.click(must('#sidebar-scrim'));
  expect(document.body.classList.contains('sidebar-collapsed')).toBe(false);
});

test('the jump box drives the palette in place: focus opens it from the sidebar, typing sets the query', () => {
  mount();
  const box = must('#sidebar-jump-input') as HTMLInputElement;
  expect([box.type, box.placeholder, box.getAttribute('aria-label'), box.getAttribute('autocomplete'), box.getAttribute('spellcheck')])
    .toEqual(['search', 'Jump to session', 'Jump to session', 'off', 'false']);
  act(() => box.focus());
  expect(palette.get()).toMatchObject({ open: true, query: '', fromSidebar: true });
  act(() => {
    box.value = 'orch';
    fireEvent.input(box);
  });
  expect(palette.get()).toMatchObject({ open: true, query: 'orch' });
  act(() => closePalette());
});

test('the sidebar rows repaint SYNCHRONOUSLY on a publish (the controller reads them right back)', () => {
  mount();
  const host = must('#sidebar-agents');
  expect(host.textContent).toBe('No agents');
  window.chelaBridge?.sidebar.set({ ...sbState(), agents: [{ name: 'one', window_id: '@1', cwd: '/srv/a' }] });
  expect(host.querySelector('.agent-row')?.getAttribute('data-agent')).toBe('one');
});

test('the nav rows, Recent and the width handle each repaint on their own store, before the publish returns', () => {
  mount();
  const bridge = installBridge(window);
  expect(must('#side-nav').innerHTML).toBe('');
  bridge.nav.set({ items: [{ id: 'terminals', label: 'Wall' }, { id: 'work', label: 'Work' }], active: 'work', badges: {} });
  expect([...document.querySelectorAll('#side-nav .side-item')].map((r) => [r.getAttribute('data-view'), r.classList.contains('active')]))
    .toEqual([['terminals', false], ['work', true]]);
  bridge.recent.set({ rows: [{ store: 's', wid: '@5', session_id: 'sid', label: 'five' }], dispatcher_rows: [], revealed: false, undo: null });
  expect($('#side-recent-island .agent-row-name')?.textContent).toBe('five');
  bridge.agents.set([{ window_id: '@9', session_id: 'sid' }]); // that session is live now
  expect(must('#side-recent-island').innerHTML).toBe('');
  bridge.sidebarLayout.set({ order: [], custom: null, view: null, width: 400, loaded: true });
  expect(must('.sidebar-resize').getAttribute('aria-valuenow')).toBe('400');
});

test('the VIEW menu repaints on every sidebar publish', () => {
  mount();
  const btn = must('#sidebar-view-menu #btn-sidebar-view');
  expect(btn.tagName).toBe('BUTTON');
  window.chelaBridge?.sidebar.set(sbState({ sort: 'created' }));
  fireEvent.keyDown(btn, { key: 'Enter' });
  const sortRow = [...document.querySelectorAll('[role="menu"] [role^="menuitem"]')].find((i) => i.textContent?.startsWith('Sort by'));
  expect(sortRow?.textContent).toContain('Created');
  fireEvent.keyDown(must('[role="menu"]'), { key: 'Escape' });
});

test('the width handle\'s commit is the controller\'s: clamped, and published to the layout the handle reads', () => {
  mount();
  const h = must('.sidebar-resize');
  fireEvent.keyDown(h, { key: 'End' });
  expect([window.chelaBridge?.sidebarLayout.get().width, h.getAttribute('aria-valuenow')]).toEqual([480, '480']);
});

test('a nav row\'s click is the shell\'s view switch', () => {
  mount();
  window.chelaBridge?.nav.set({ items: [{ id: 'terminals', label: 'Wall' }, { id: 'work', label: 'Work' }], active: 'work', badges: {} });
  act(() => fireEvent.click(must('#side-nav .side-item[data-view="terminals"]')));
  expect([view.get(), must('#panel-terminals').classList.contains('active')]).toEqual(['terminals', true]);
});

test('sidebarActions are the controller\'s commands, and the shell\'s view switch and session click', () => {
  expect([sidebarActions.selectAgent, sidebarActions.selectView]).toEqual([selectAgent, selectView]);
  const same = ['toggleGroup', 'groupNewSession', 'toggleArchivedSection', 'unarchiveRow', 'groupMenuAction', 'rowMenuAction',
    'moveToGroup', 'viewMenuAction', 'deleteCustomGroup', 'setSidebarWidth', 'resumeSession', 'dismissRecentSession',
    'clearRecentSessions', 'undoDismissRecent', 'closeRecentUndo', 'toggleDispatcherSessions'] as const;
  for (const k of same) expect([k, sidebarActions[k]]).toEqual([k, sb[k]]);
});

test('newCustomGroup and renameCustomGroup ask for the name (the menu passes none)', () => {
  const ask = vi.fn(() => 'Ops');
  vi.stubGlobal('prompt', ask);
  const g = sidebarActions.newCustomGroup();
  expect(g?.name).toBe('Ops');
  sidebarActions.renameCustomGroup(g?.id as string);
  expect(ask).toHaveBeenCalledTimes(2);
});

test('a fixture page of a few slots gets each one drawn from the same table (mountSlots)', () => {
  document.body.innerHTML = '<span id="decisions-island"></span><div id="keybar-island"></div><div id="nope"></div>';
  roots.push(...mountSlots(document));
  expect(roots).toHaveLength(2);
  expect([$('#decisions-island #btn-decisions') !== null, $('#keybar-island #term-keybar') !== null, $('#nope')?.innerHTML]).toEqual([true, true, '']);
});

function sbState(view: Record<string, unknown> = {}): SidebarState {
  return {
    agents: [], wants: () => false, orchWid: null, view: normalizeView({ activity: 'all', sort: 'name', ...view }), order: [],
    custom: { groups: [], assign: {} }, archived: [], sessions: [], labels: {}, now: Date.now(), collapsed: [],
    ctx: {}, onWall: [], active: null, altHeld: false,
  };
}
