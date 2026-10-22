// CMX-248: the view switch and what a click on a session does (static/js/nav.js until then),
// against the shell's real panels. The Wall's commands, its timer and the refresh loop are
// mocked: what is pinned here is which of them the shell calls, and when.
import { act, cleanup } from '@testing-library/react';
import type { Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { createBridge, installBridge } from '../../../shared/bridge/index.ts';
import { dispatch } from '../../../shared/events/index.ts';
import { refresh } from '../../../shared/scheduler/index.ts';
import { agents } from '../../../shared/store/agents.ts';
import { view } from '../../../shared/store/view.ts';
import { minimizedPanes } from '../../../shared/store/wall.ts';
import { detailAgent } from '../../agentdetail/index.ts';
import { startTermTimer, stopTermTimer } from '../../paneframe/index.ts';
import { refreshSummary } from '../../primarymenu/index.ts';
import { renderSidebarAgents } from '../../sidebar/controller.ts';
import { focusPaneByWid, isWallVisible, minimizePane, renderedWids, setTermMode } from '../../wall/index.ts';
import { agentDotColor, mountApp, renderNav, selectAgent, selectView, showAgentDetail } from '../index.ts';

vi.mock('../../paneframe/index.ts', async (orig) => ({ ...(await orig<object>()), startTermTimer: vi.fn(), stopTermTimer: vi.fn() }));
vi.mock('../../primarymenu/index.ts', async (orig) => ({ ...(await orig<object>()), refreshSummary: vi.fn() }));
vi.mock('../../../shared/scheduler/index.ts', async (orig) => ({ ...(await orig<object>()), refresh: vi.fn(() => Promise.resolve()) }));
vi.mock('../../wall/index.ts', async (orig) => ({
  ...(await orig<object>()),
  renderedWids: [] as string[],
  isWallVisible: vi.fn(() => false),
  minimizePane: vi.fn(),
  setTermMode: vi.fn(),
  focusPaneByWid: vi.fn(),
}));

let root: Root | null = null;
const panel = () => [...document.querySelectorAll('.panel.active')].map((p) => p.id);
const nav = () => installBridge(window).nav.get();
const media = (phone: boolean) => (q: string) => ({ matches: phone && /max-width:\s*768px/.test(q), addEventListener() {}, removeEventListener() {} });

beforeEach(() => {
  vi.stubGlobal('fetch', () => new Promise(() => {}));
  vi.stubGlobal('matchMedia', media(false));
  window.chelaBridge = createBridge();
  document.body.innerHTML = '<div id="root"></div>';
  root = mountApp(document);
  vi.clearAllMocks();
});

afterEach(() => {
  act(() => root?.unmount());
  cleanup();
  vi.unstubAllGlobals();
  renderedWids.length = 0;
  minimizedPanes.set(new Set());
  agents.set([]);
  detailAgent.set(null);
  view.set('work');
});

test('selectView shows the view\'s panel before it returns, lights its nav row, and refreshes', () => {
  detailAgent.set('someone');
  selectView('terminals');
  expect([view.get(), panel(), nav().active, detailAgent.get()]).toEqual(['terminals', ['panel-terminals'], 'terminals', null]);
  expect(vi.mocked(refresh)).toHaveBeenCalledTimes(1);
  selectView('work');
  expect([view.get(), panel(), nav().active]).toEqual(['work', ['panel-work'], 'work']);
  expect(vi.mocked(refresh)).toHaveBeenCalledTimes(2);
});

test('entering a view runs ITS enter hook, and every OTHER view\'s exit', () => {
  selectView('terminals');
  expect([vi.mocked(startTermTimer).mock.calls.length, vi.mocked(stopTermTimer).mock.calls.length]).toEqual([1, 0]);
  selectView('work');
  expect([vi.mocked(startTermTimer).mock.calls.length, vi.mocked(stopTermTimer).mock.calls.length]).toEqual([1, 1]);
});

test('the view\'s panel is visible when its enter hook runs (the Wall measures its stage)', () => {
  vi.mocked(startTermTimer).mockImplementation(() => {
    expect(document.getElementById('panel-terminals')?.classList.contains('active')).toBe(true);
  });
  selectView('terminals');
  expect(vi.mocked(startTermTimer)).toHaveBeenCalledTimes(1);
});

test('navigating shuts the phone drawer', () => {
  vi.stubGlobal('matchMedia', media(true));
  document.querySelector('.sidebar')?.classList.add('open');
  selectView('work');
  expect(document.querySelector('.sidebar')?.classList.contains('open')).toBe(false);
});

test('showAgentDetail drills in: its panel, its agent, no nav row lit, its sidebar row active, every view let go', () => {
  selectView('terminals');
  renderSidebarAgents([{ name: 'alpha', window_id: '@7', cwd: '/srv/a' }]); // the row it lights
  vi.clearAllMocks();
  showAgentDetail('alpha');
  expect([view.get(), panel(), detailAgent.get(), nav().active]).toEqual(['agent-detail', ['panel-agent-detail'], 'alpha', null]);
  expect(installBridge(window).sidebar.get()?.active ?? null).toBe('alpha');
  expect([vi.mocked(stopTermTimer).mock.calls.length, vi.mocked(startTermTimer).mock.calls.length]).toEqual([1, 0]);
  expect(vi.mocked(refreshSummary)).toHaveBeenCalledTimes(1);
  expect(vi.mocked(refresh)).not.toHaveBeenCalled();
  selectView('work');
  expect(installBridge(window).sidebar.get()?.active ?? null).toBeNull();
});

const A = { name: 'alpha', window_id: '@7', online: true };

test('a session click with the Wall showing and its pane open on it MINIMIZES the pane', () => {
  agents.set([A]);
  renderedWids.push('@7');
  vi.mocked(isWallVisible).mockReturnValue(true);
  selectAgent('alpha');
  expect(vi.mocked(minimizePane).mock.calls).toEqual([['@7']]);
  expect([vi.mocked(focusPaneByWid).mock.calls.length, vi.mocked(setTermMode).mock.calls.length]).toEqual([0, 0]);
});

test('a session click with the Wall showing but the pane minimized restores and focuses it', () => {
  agents.set([A]);
  renderedWids.push('@7');
  minimizedPanes.set(new Set(['@7']));
  vi.mocked(isWallVisible).mockReturnValue(true);
  selectAgent('alpha');
  expect([vi.mocked(minimizePane).mock.calls.length, vi.mocked(setTermMode).mock.calls.length]).toEqual([0, 0]);
  expect(vi.mocked(focusPaneByWid).mock.calls).toEqual([['@7']]);
});

test('a session click with the pane not on the Wall: the Wall comes back (wall mode) and focuses it', () => {
  agents.set([A]);
  vi.mocked(isWallVisible).mockReturnValue(true);
  selectAgent('alpha'); // visible, but not rendered there
  expect([vi.mocked(minimizePane).mock.calls.length, vi.mocked(setTermMode).mock.calls.length]).toEqual([0, 0]);
  vi.mocked(isWallVisible).mockReturnValue(false);
  renderedWids.push('@7');
  selectAgent('alpha'); // rendered, but the Wall is not showing (another view, or single mode)
  expect(vi.mocked(setTermMode).mock.calls).toEqual([['wall']]);
  expect(vi.mocked(focusPaneByWid).mock.calls).toEqual([['@7'], ['@7']]);
  expect(vi.mocked(minimizePane)).not.toHaveBeenCalled();
});

test('a session with no window yet, or no such session, opens the detail card', () => {
  agents.set([{ name: 'nowid', online: true }]);
  selectAgent('nowid');
  expect([view.get(), detailAgent.get()]).toEqual(['agent-detail', 'nowid']);
  selectAgent('ghost');
  expect(detailAgent.get()).toBe('ghost');
  expect([vi.mocked(focusPaneByWid).mock.calls.length, vi.mocked(minimizePane).mock.calls.length]).toEqual([0, 0]);
});

test('agentDotColor: blocked on you is yellow (a pane-only permission gate too), working green, else grey', () => {
  expect([
    agentDotColor({ session_status: 'waiting' }), agentDotColor({ needs_human: true, session_status: 'busy' }),
    agentDotColor({ session_status: 'busy' }), agentDotColor({ session_status: 'idle' }), agentDotColor({}),
  ]).toEqual(['yellow', 'yellow', 'green', 'grey', 'grey']);
});

test('renderNav publishes one nav row per enabled, non-virtual view, with its label, icon and badges', () => {
  renderNav();
  expect(nav().items.map((v) => [v.id, v.label, v.lucide, (v.badges ?? []).map((b) => b.id)])).toEqual([
    ['terminals', 'Wall', 'layout-grid', []],
    ['work', 'Work', 'columns-3', ['side-runs-count', 'side-pr-count']],
  ]);
});

test('a `windows` event drops the agent list, so the next read refetches it', () => {
  agents.set([A]);
  dispatch('windows', '{}');
  expect(agents.get()).toEqual([]);
});
