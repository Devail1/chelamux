// CMX-248 (CMX-212 P34): the view switch and what lands a click on a session (static/js/nav.js
// until now), and the hosts it hands the features that need the view switch, the Wall or the
// orchestrator slot: the sidebar, the agent detail, the launcher, the palette and the Wall.
import { flushSync } from 'react-dom';
import { BOOT } from '../../../shared/api/index.ts';
import { onEvent } from '../../../shared/events/index.ts';
import { onOrchestratorChange, orchestratorState } from '../../../shared/orchestrator/index.ts';
import { refresh } from '../../../shared/scheduler/index.ts';
import { agents as agentsStore, type Agent } from '../../../shared/store/agents.ts';
import { view } from '../../../shared/store/view.ts';
import { minimizedPanes, orderedWids } from '../../../shared/store/wall.ts';
import { resolveWindowId } from '../../../../../static/js/windowid.ts';
import { detailAgent, redrawAgentDetail, setAgentDetailHost } from '../../agentdetail/index.ts';
import { launchProject, setLauncherHost } from '../../launcher/index.ts';
import { setPaletteHost } from '../../palette/index.ts';
import { wantsHuman, type HeadAgent } from '../../panehead/index.ts';
import { refreshSummary } from '../../primarymenu/index.ts';
import {
  closeSidebar, configureSidebar, refreshSidebar, renderSidebarAgents, setActiveRow, setNav,
} from '../../sidebar/controller.ts';
import { adoptSidebarDefault, configureWall, focusPaneByWid, isWallVisible, label as wallLabel, minimizePane, renderedWids, setTermMode } from '../../wall/index.ts';
import { findView, navViews, otherViews, paletteViews, type ViewCtx } from '../views.ts';
import { VIEWS } from './registry.ts';
import { updateTabSignal } from './tabSignal.ts';

const TERMINALS_ON = BOOT.terminalsEnabled;
const ctx = (): ViewCtx => ({ terminalsOn: TERMINALS_ON });
const agentsNow = () => agentsStore.get();

/** One nav row per enabled, non-virtual view, with its badge slots (the sidebar draws them). */
export function renderNav(): void {
  setNav({ items: navViews(VIEWS, ctx()).map(({ id, label, lucide, badges }) => ({ id, label, lucide, badges })) });
}

// The canvas shows the store's view (features/shell App); set synchronously, so the panel is
// visible before an enter hook or the Wall measures it.
function show(id: string, agentName: string | null) {
  flushSync(() => view.set(id));
  // The drill-in has no nav row of its own, so none lights up while it shows.
  setNav({ active: id === 'agent-detail' ? null : id });
  setActiveRow(agentName);
  // Every OTHER view lets go (stops its timer): a new view is one registry entry, not a branch.
  otherViews(VIEWS, id).forEach((o) => o.exit?.());
  closeSidebar(); // navigating dismisses the phone drawer (a no-op on desktop)
}

export function selectView(id: string): void {
  show(id, null);
  detailAgent.set(null); // after the switch: no moment shows the drill-in with no agent
  findView(VIEWS, id)?.enter?.();
  void refresh();
}

/** Focus one agent in the canvas: its detail card. */
export function showAgentDetail(name: string): void {
  detailAgent.set(name); // the agent detail (features/agentdetail) draws it
  show('agent-detail', name);
  refreshSummary();
}

/**
 * A click on a session (a sidebar row, the palette) acts relative to the Wall (CMX-139):
 *   1. the wall is showing and this pane is open on it: minimize it (a click on a summoned pane
 *      hides it, mirroring the on-wall ring, CMX-137);
 *   2. it is not on the wall (another view, or single mode): switch to the wall and focus it;
 *   3. the wall is showing but the pane is minimized: restore and focus it.
 * The detail card is the fallback when there is no wall (terminals off) or no window yet.
 */
export function selectAgent(name: string): void {
  const a = agentsNow().find((x) => x.name === name);
  const wid = TERMINALS_ON && a?.window_id;
  if (!wid) return showAgentDetail(name);
  if (isWallVisible() && renderedWids.includes(wid) && !minimizedPanes.get().has(wid)) return minimizePane(wid);
  if (!isWallVisible()) setTermMode('wall');
  focusPaneByWid(wid);
}

/** Open (never toggle) a window's pane: a menu's "Open pane" and Alt+N always land on it. */
function openPane(a: Agent | null | undefined) {
  if (!a) return;
  if (TERMINALS_ON && a.window_id) {
    if (!isWallVisible()) setTermMode('wall');
    focusPaneByWid(a.window_id);
    return;
  }
  showAgentDetail(a.name ?? '');
}

/** The status dot a session shows in every view, matching the wall's (features/panehead):
 * blocked on you, including a pane-only permission gate: yellow; working: green; else grey. */
export function agentDotColor(a: Agent): string {
  if (wantsHuman(a as HeadAgent)) return 'yellow';
  return a.session_status === 'busy' ? 'green' : 'grey';
}

// CMX-300: three roles, exclusive. The orchestrator holds the one decisions-inbox slot (the
// fact the pane toggle and the inbox's owner chip render); a dispatched window was spawned by the
// dispatcher (/api/agents `dispatched`); anything else is a plain session. Orchestrator wins.
const ROLE_LABEL = { orchestrator: 'Orchestrator', dispatched: 'Dispatched', plain: 'Plain session' };
function roleLabel(a: Agent): string {
  if (a.window_id && orchestratorState().wid === a.window_id) return ROLE_LABEL.orchestrator;
  return a.dispatched ? ROLE_LABEL.dispatched : ROLE_LABEL.plain;
}

/** A session's label everywhere, the wall's (CMX-62): a rename, else Claude's title, else its name. */
const agentLabel = (a: Agent) => (a.window_id ? wallLabel(a.window_id) : (a.name ?? ''));

// --- The hosts --------------------------------------------------------------------------
configureSidebar({
  label: agentLabel,
  onWall: () => renderedWids.filter((w) => !minimizedPanes.get().has(w)),
  orchWid: () => orchestratorState().wid,
  wants: (a) => wantsHuman(a as HeadAgent),
  tabSignal: (rows) => updateTabSignal(rows),
  adoptDefault: () => adoptSidebarDefault(),
  openPane: (a) => openPane(a as Agent),
  launchProject: (cwd, opts) => launchProject(cwd, opts),
});

// The orchestrator slot changes with no agent poll (a pane's toggle, the inbox): redraw off the
// cached list, no refetch to move one badge.
onOrchestratorChange(() => {
  renderSidebarAgents(agentsNow() as never);
  redrawAgentDetail(); // its Role row reads the slot too
});

// The window set changed (a window appeared or vanished): the next read refetches it.
onEvent('windows', () => agentsStore.set([]));

setAgentDetailHost({ selectView, roleLabel, dotColor: agentDotColor });
// A launch lands on the Wall, and on the sidebar's next read.
setLauncherHost({ focusPaneByWid, selectView, refreshSidebar });
setPaletteHost({
  views: () => paletteViews(VIEWS, ctx()).map((v) => ({ id: v.id, label: v.label ?? v.id })),
  label: agentLabel,
  dotColor: agentDotColor,
  selectView,
  selectAgent,
  showAgentDetail,
  resolveWid: resolveWindowId,
  wall: {
    rendered: () => renderedWids,
    minimized: () => minimizedPanes.get(),
    ordered: orderedWids,
    visible: isWallVisible,
    showWall: () => setTermMode('wall'),
    focus: focusPaneByWid,
  },
});
// The Wall switches to itself on a focus, and badges the tab from its 4 s poll.
configureWall({ selectView, tabSignal: (rows) => updateTabSignal(rows) });
