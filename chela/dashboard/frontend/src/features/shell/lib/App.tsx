// CMX-248 (CMX-212 P34): the page (templates/index.html's body until now). One React root:
// the sidebar, the canvas of views, the phone controls, the safety float and the overlays.
// Each slot's content is lib/slots.tsx's. The classes and ids are the ones style.css and the
// browser suites have always read, so the look does not change.
import type { ReactNode } from 'react';
import { BOOT, staticUrl } from '../../../shared/api/index.ts';
import { icons } from '../../../shared/icons/index.ts';
import { useStore } from '../../../shared/store/index.ts';
import { view } from '../../../shared/store/view.ts';
import { IconButton } from '../../../shared/ui/index.ts';
import { sidebarJumpInput } from '../../palette/index.ts';
import { closeSidebar, toggleSidebar } from '../../sidebar/controller.ts';
import { panelId } from '../views.ts';
import { SLOTS } from './slots.tsx';

function Slot({ id, as: Tag = 'span', className = 'island-slot' }: { id: string; as?: 'span' | 'div'; className?: string }) {
  return <Tag id={id} className={className || undefined}>{SLOTS[id]?.()}</Tag>;
}

/** A view's canvas panel: shown while the store names it. A hidden panel stays mounted (the
 * Wall's live iframes must never reload). */
function Panel({ id, children }: { id: string; children?: ReactNode }) {
  const active = useStore(view) === id;
  return <div className={`panel${active ? ' active' : ''}`} id={panelId(id)}>{children}</div>;
}

function SidebarHead() {
  return (
    <div className="sidebar-head">
      {/* One control, two behaviours (sidebar controller toggleSidebar): a phone slides the
          drawer, a desktop folds the column to an icon rail. The phone's .mobile-menu-fab
          floats over it, so open and close share one spot. The controller keeps aria-expanded. */}
      <IconButton label="Toggle sidebar" className="icon-btn sidebar-toggle" id="btn-menu"
        aria-expanded={!document.body.classList.contains('sidebar-collapsed')} onClick={() => toggleSidebar()}>
        <icons.menu aria-hidden="true" />
      </IconButton>
      <div className="brand"><img src={staticUrl('img/claw-mark.svg')} className="brand-mark" alt="" /><span>chela</span></div>
      <div className="sidebar-head-actions">
        <Slot id="decisions-island" className="decisions-island" />
      </div>
    </div>
  );
}

function Sidebar() {
  return (
    <aside className="sidebar">
      <SidebarHead />
      <div className="sidebar-scroll">
        <div className="sidebar-quick">
          <Slot id="sidebar-new-island" />
          {/* The ⌘K palette, driven in place: focus stays here, its arrows and Enter work off it
              being open (features/palette sidebarJumpInput). */}
          <label className="sidebar-jump">
            <icons.search size={15} strokeWidth={1.8} aria-hidden="true" />
            <input type="search" id="sidebar-jump-input" placeholder="Jump to session" aria-label="Jump to session"
              autoComplete="off" spellCheck={false}
              onFocus={(e) => sidebarJumpInput(e.currentTarget)} onInput={(e) => sidebarJumpInput(e.currentTarget)} />
          </label>
        </div>
        <section className="side-section">
          <div className="side-head"><span className="side-title">Navigate</span></div>
          <Slot id="side-nav" as="div" className="side-list" />
        </section>
        <section className="side-section">
          <div className="side-head">
            <span className="side-title">Sessions</span>
            <Slot id="sessions-count-island" />
            <Slot id="sidebar-view-menu" className="side-view-slot" />
          </div>
          <Slot id="sidebar-agents" as="div" className="side-list" />
        </section>
        <Slot id="side-recent-island" as="div" className="side-recent-slot" />
      </div>
      {/* Ambient readouts and the one menu; not safety-critical, unlike the float below. */}
      <div className="sidebar-foot">
        <Slot id="resources-island" />
        <Slot id="primary-menu-island" />
      </div>
      <Slot id="sidebar-resize" as="div" className="sidebar-resize-slot" />
    </aside>
  );
}

function Wall() {
  return (
    <Panel id="terminals">
      <div className="term-toolbar">
        {/* features/wall mounts the layout toolbar here (CMX-397); the shares and approvals
            pills take the row's right end on a desktop (CMX-422, CMX-135). */}
        <span className="island-slot" id="wall-toolbar-island" />
        <Slot id="shares-pill-row" className="shares-pill-slot" />
        <Slot id="term-approvals-island" />
      </div>
      {/* Gridstack owns what is inside the stage, and the Wall the Minimized tray. */}
      <div id="term-stage" />
      <div id="term-min-dock" hidden />
      <Slot id="keybar-island" as="div" className="keybar-island" />
    </Panel>
  );
}

export function App() {
  return (
    <>
      <div className="app">
        <Sidebar />
        <main className="canvas" id="canvas">
          <Panel id="agent-detail"><Slot id="agent-detail" as="div" className="" /></Panel>
          <Panel id="work">{SLOTS['panel-work']?.()}</Panel>
          {BOOT.terminalsEnabled && <Wall />}
        </main>
      </div>
      {/* Phone only: the drawer's opener and New session in one tap, outside the sidebar, whose
          `transform` would trap a fixed child off-screen (CMX-377, CMX-386). */}
      <IconButton label="Open menu" className="mobile-menu-fab" id="btn-menu-mobile" aria-expanded={false} onClick={() => toggleSidebar()}>
        <icons.menu aria-hidden="true" />
      </IconButton>
      <Slot id="new-fab-island" />
      {/* Always on top, on every breakpoint, OUTSIDE the off-canvas sidebar: a live share or a
          broken status feed must never hide behind a closed drawer (CMX-377). */}
      <div className="safety-float">
        <Slot id="status-health-island" />
        <Slot id="shares-pill-float" className="shares-pill-slot" />
        <Slot id="access-req-island" />
        <Slot id="approvals-island" />
      </div>
      <div className="drawer-scrim" id="sidebar-scrim" onClick={() => closeSidebar()} />
      <Slot id="overlays-island" as="div" className="" />
      <Slot id="newtask-island" as="div" className="" />
      <Slot id="task-modal-island" as="div" className="" />
      <Slot id="diff-modal-island" as="div" className="" />
      <Slot id="work-toasts" as="div" className="" />
      <Slot id="shares-sheet-island" as="div" className="" />
    </>
  );
}
