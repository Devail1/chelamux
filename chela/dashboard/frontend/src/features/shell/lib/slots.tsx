// CMX-248: what each of the shell's slots draws, by the slot element's id. <App> renders every
// slot in place (lib/App.tsx); a page of its own without the shell (a test fixture) mounts the
// same table into whichever slots it has (mountSlots). One table, so the two cannot disagree.
import type { ReactNode } from 'react';
import { AccessRequestsPill, AccessRequestsSheet } from '../../accessreq/index.ts';
import { AgentDetail, SendMessage } from '../../agentdetail/index.ts';
import { ApprovalsPill, ApprovalsSheet } from '../../approvals/index.ts';
import { DecisionsButton, DecisionsDialog } from '../../decisions/index.ts';
import { DiffModal } from '../../diffmodal/index.ts';
import { Keybar, paneKeybarActions } from '../../keybar/index.ts';
import { LaunchMenu, NewSessionButton, NewSessionFab } from '../../launcher/index.ts';
import { NewTask } from '../../newtask/index.ts';
import { Palette, Shortcuts } from '../../palette/index.ts';
import { PrimaryMenu, SessionsCount } from '../../primarymenu/index.ts';
import { Resources } from '../../resources/index.ts';
import { Settings } from '../../settings/index.ts';
import { SharesPill, SharesSheet } from '../../share/index.ts';
import { Nav, Recent, ResizeHandle, Sidebar, ViewMenu, type SidebarActions } from '../../sidebar/index.ts';
import * as sb from '../../sidebar/controller.ts';
import type { AgentRow } from '../../sidebar/model.ts';
import { StatusHealth } from '../../statushealth/index.ts';
import { TaskModal } from '../../taskmodal/index.ts';
import { UpdateBanner } from '../../update/index.ts';
import { WorkToasts, WorkView } from '../../work/index.ts';
import { installBridge } from '../../../shared/bridge/index.ts';
import { useStoreNow } from '../../../shared/store/index.ts';
import { selectAgent, selectView } from './nav.ts';

/** What the sidebar's rows, menus, nav, Recent and the width handle call. */
export const sidebarActions: SidebarActions = {
  selectAgent, selectView,
  toggleGroup: sb.toggleGroup, groupNewSession: sb.groupNewSession, toggleArchivedSection: sb.toggleArchivedSection,
  unarchiveRow: sb.unarchiveRow, groupMenuAction: sb.groupMenuAction, rowMenuAction: sb.rowMenuAction,
  moveToGroup: sb.moveToGroup, viewMenuAction: sb.viewMenuAction, newCustomGroup: () => sb.newCustomGroup(),
  renameCustomGroup: (id) => sb.renameCustomGroup(id), deleteCustomGroup: sb.deleteCustomGroup,
  setSidebarWidth: sb.setSidebarWidth, resumeSession: sb.resumeSession, dismissRecentSession: sb.dismissRecentSession,
  clearRecentSessions: sb.clearRecentSessions, undoDismissRecent: sb.undoDismissRecent, closeRecentUndo: sb.closeRecentUndo,
  toggleDispatcherSessions: sb.toggleDispatcherSessions,
};

// The sidebar's stores repaint before a publish returns: its controller reads the rows back.
const bridge = () => installBridge(window);
function SidebarRows() {
  return <Sidebar state={useStoreNow(bridge().sidebar)} actions={sidebarActions} />;
}
function SidebarViewMenu() {
  return <ViewMenu state={useStoreNow(bridge().sidebar)} actions={sidebarActions} />;
}
function SidebarNav() {
  return <Nav nav={useStoreNow(bridge().nav)} actions={sidebarActions} />;
}
function SidebarRecent() {
  const recent = useStoreNow(bridge().recent);
  const agents = useStoreNow(bridge().agents) as unknown as AgentRow[];
  return <Recent recent={recent} agents={agents} actions={sidebarActions} />;
}
function SidebarResize() {
  return <ResizeHandle width={useStoreNow(bridge().sidebarLayout).width} onCommit={(w) => sidebarActions.setSidebarWidth(w)} />;
}

/** What floats over the page, each portaled or fixed by itself. */
function Overlays() {
  return (
    <>
      <Settings />
      <Palette />
      <Shortcuts />
      <LaunchMenu />
      <SendMessage />
      <ApprovalsSheet />
      <AccessRequestsSheet />
      <UpdateBanner />
    </>
  );
}

export const SLOTS: Readonly<Record<string, () => ReactNode>> = {
  'decisions-island': () => (
    <>
      <DecisionsButton jump={sb.jumpToOrchestrator} />
      <DecisionsDialog />
    </>
  ),
  'sidebar-new-island': () => <NewSessionButton />,
  'side-nav': () => <SidebarNav />,
  'sessions-count-island': () => <SessionsCount />,
  'sidebar-view-menu': () => <SidebarViewMenu />,
  'sidebar-agents': () => <SidebarRows />,
  'side-recent-island': () => <SidebarRecent />,
  'resources-island': () => <Resources />,
  'primary-menu-island': () => <PrimaryMenu />,
  'sidebar-resize': () => <SidebarResize />,
  'agent-detail': () => <AgentDetail />,
  'panel-work': () => <WorkView />,
  'shares-pill-row': () => <SharesPill id="term-shares" />,
  'term-approvals-island': () => <ApprovalsPill id="term-approvals" />,
  'keybar-island': () => <Keybar actions={paneKeybarActions} />,
  'new-fab-island': () => <NewSessionFab />,
  'status-health-island': () => <StatusHealth />,
  'shares-pill-float': () => <SharesPill id="btn-shares" />,
  'access-req-island': () => <AccessRequestsPill />,
  'approvals-island': () => <ApprovalsPill id="btn-approvals" />,
  'overlays-island': () => <Overlays />,
  'newtask-island': () => <NewTask />,
  'task-modal-island': () => <TaskModal />,
  'diff-modal-island': () => <DiffModal />,
  'work-toasts': () => <WorkToasts />,
  'shares-sheet-island': () => <SharesSheet />,
};
