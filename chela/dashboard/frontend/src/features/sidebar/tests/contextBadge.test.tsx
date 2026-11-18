// CMX-399 (Liav, 11-17): the context % badge on every kind of row, keyed by the LIVE window id.
// The screenshot had /api/agents/context {@1: 64, @4: 63, @5: 51} and only the 51% row drew its
// badge. Driven here from the live payload's shape through the controller's real cache and
// render pass into the real row: a crowned orchestrator row (Ungrouped, home folder, a long
// title), a custom-group row, and a custom-group row whose session came back under a NEW
// window id after a reboot (its assignment follows the session, CMX-378).
// Why the badge was missing on screen is layout, not data (a long title squeezed the strip
// to nothing): that half is measured in a real browser, tests/browser/react_islands.test.mjs.
import { cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { installBridge } from '../../../shared/bridge/index.ts';
import { configureSidebar, refreshLayout, renderSidebarAgents, updateCtxCache } from '../controller.ts';
import { Sidebar } from '../index.ts';
import type { AgentRow } from '../model.ts';
import { actions } from './fixtures.ts';

const bridge = installBridge(window);

const LONG = 'TAP-10 order book bot check v2b screen on 11-08 formal gate on 12-06';
const ORCH: AgentRow = {
  name: 'liavedunix', window_id: '@1', session_id: 'c084360a', claude_running: true, session_status: 'idle',
  cwd: '/home/u', cwd_is_home: true, ai_title: LONG,
};
const SLING: AgentRow = {
  name: 'slingpuck-proto', window_id: '@4', session_id: 'de72cc25', claude_running: true, session_status: 'idle',
  cwd: '/srv/games/slingpuck-proto',
};
// was @9 before the reboot; the custom group holds its SESSION, so it lands there as @5
const TEETER: AgentRow = {
  name: 'teeter-proto', window_id: '@5', session_id: '689f677d', claude_running: true, session_status: 'busy',
  cwd: '/srv/games/teeter-proto',
};
// /api/agents/context at that moment (only the fields the sidebar reads, plus a name)
const LIVE_CTX = [
  { name: 'liavedunix', window_id: '@1', used_pct: 64 },
  { name: 'slingpuck-proto', window_id: '@4', used_pct: 63 },
  { name: 'teeter-proto', window_id: '@5', used_pct: 51 },
];

beforeEach(async () => {
  document.body.innerHTML = '<div id="sidebar-agents"></div>';
  configureSidebar({
    label: a => a.ai_title || a.name || '', onWall: () => [], orchWid: () => '@1',
    wants: () => false, tabSignal() {}, adoptDefault() {}, openPane() {}, launchProject() {},
  });
  const now = Date.now();
  const layout = {
    order: [], view: { groupBy: 'custom', activity: 'all', sort: 'name' },
    custom: {
      groups: [{ id: 'gl', name: 'G&L Studio Games' }],
      assign: { 's:de72cc25': 'gl', 's:689f677d': 'gl' },
      seen: { 's:de72cc25': now, 's:689f677d': now },
    },
  };
  vi.stubGlobal('fetch', (url: string) => Promise.resolve({
    json: () => Promise.resolve(String(url).startsWith('/api/sidebar/layout') ? { layout } : { ok: true }),
  }));
  await refreshLayout();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function draw(rows: AgentRow[]) {
  renderSidebarAgents(rows);
  const s = bridge.sidebar.get();
  if (!s) throw new Error('nothing published');
  const { container } = render(<Sidebar state={s} actions={actions()} />);
  const row = (name: string) => {
    const r = container.querySelector<HTMLElement>(`.agent-row[data-agent="${name}"]`);
    if (!r) throw new Error(`no row for ${name}`);
    return r;
  };
  const badge = (name: string) => row(name).querySelector('[data-meta="context"]')?.textContent ?? null;
  const groupOf = (name: string) => row(name).closest<HTMLElement>('.side-group')?.dataset.g ?? null;
  return { row, badge, groupOf };
}

test('the live payload: the crowned orchestrator, a custom-group row and a resumed row all draw their badge', () => {
  updateCtxCache(LIVE_CTX);
  const { row, badge, groupOf } = draw([ORCH, SLING, TEETER]);
  expect(row('liavedunix').querySelector('[data-meta="orchestrator"]')).toBeTruthy();
  expect(row('liavedunix').querySelector('.agent-row-name')?.textContent).toBe(LONG);
  expect([badge('liavedunix'), badge('slingpuck-proto'), badge('teeter-proto')]).toEqual(['64%', '63%', '51%']);
  // the two game rows really are in the custom group (the resumed one by its session)
  expect([groupOf('slingpuck-proto'), groupOf('teeter-proto')]).toEqual(['~custom:gl', '~custom:gl']);
  expect(groupOf('liavedunix')).not.toBe('~custom:gl');
});

test('a reboot renumbers the windows: the next read REPLACES the cache, so no badge sits on a stale id', () => {
  // before the reboot: teeter was @9 at 51%, and @5 was another session at 90%
  updateCtxCache([{ window_id: '@9', used_pct: 51 }, { window_id: '@5', used_pct: 90 }]);
  updateCtxCache(LIVE_CTX);
  const { badge } = draw([ORCH, SLING, TEETER]);
  expect(badge('teeter-proto')).toBe('51%');
  expect(bridge.sidebar.get()?.ctx).toEqual({ '@1': 64, '@4': 63, '@5': 51 });
});

test('a failed context read (an error body) keeps the last numbers rather than blanking them', () => {
  updateCtxCache(LIVE_CTX);
  updateCtxCache({ error: 'boom' });
  updateCtxCache(null);
  const { badge } = draw([ORCH, SLING, TEETER]);
  expect([badge('liavedunix'), badge('slingpuck-proto'), badge('teeter-proto')]).toEqual(['64%', '63%', '51%']);
});

test('an entry with no window id or no number draws no badge, and the rest still do', () => {
  updateCtxCache([{ name: 'liavedunix', used_pct: 64 }, { window_id: '@4', used_pct: null }, LIVE_CTX[2]]);
  const { badge } = draw([ORCH, SLING, TEETER]);
  expect([badge('liavedunix'), badge('slingpuck-proto'), badge('teeter-proto')]).toEqual([null, null, '51%']);
});
