// CMX-219 (CMX-212 P05/P06/P20/P21): the page's chrome as React: the safety float's pills,
// the sidebar's New session, agent count, resources strip and gear menu, the phone "+", the
// agent detail, and the overlays. No side effect at import: islands.tsx mounts it on boot, and
// the jsdom suites mount it on a page of their own (tests/js_helpers/dashboard_dom.mjs).
import { AccessRequestsPill, AccessRequestsSheet } from './features/accessreq/index.ts';
import { AgentDetail, SendMessage } from './features/agentdetail/index.ts';
import { ApprovalsPill, ApprovalsSheet } from './features/approvals/index.ts';
import { LaunchMenu, NewSessionButton, NewSessionFab } from './features/launcher/index.ts';
import { PrimaryMenu, SessionsCount } from './features/primarymenu/index.ts';
import { Resources } from './features/resources/index.ts';
import { StatusHealth } from './features/statushealth/index.ts';
import { UpdateBanner } from './features/update/index.ts';
import { mountIsland } from './shared/islands/index.tsx';

/** CMX-219: what floats over the page, one root: each portals or fixes itself. */
export function Overlays() {
  return (
    <>
      <LaunchMenu />
      <SendMessage />
      <ApprovalsSheet />
      <AccessRequestsSheet />
      <UpdateBanner />
    </>
  );
}

/** CMX-219: the islands that draw one control in a legacy row (index.html's .island-slot). */
export function mountChrome(doc: Document) {
  mountIsland('sidebar-new-island', NewSessionButton, {}, doc);
  mountIsland('sessions-count-island', SessionsCount, {}, doc);
  mountIsland('resources-island', Resources, {}, doc);
  mountIsland('primary-menu-island', PrimaryMenu, {}, doc);
  mountIsland('new-fab-island', NewSessionFab, {}, doc);
  mountIsland('status-health-island', StatusHealth, {}, doc);
  mountIsland('access-req-island', AccessRequestsPill, {}, doc);
  mountIsland('approvals-island', ApprovalsPill, { id: 'btn-approvals' }, doc);
  mountIsland('term-approvals-island', ApprovalsPill, { id: 'term-approvals' }, doc);
  mountIsland('agent-detail', AgentDetail, {}, doc);
  mountIsland('overlays-island', Overlays, {}, doc);
}
