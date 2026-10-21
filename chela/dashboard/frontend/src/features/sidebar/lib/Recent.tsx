// CMX-208: Recent sessions, one-click resume of a session a hard tmux death orphaned
// (chela/restore.py's classification, /api/restore). Hidden when there is nothing to resume.
// CMX-278 (Liav, 10-19): a session live in a window of this epoch is never listed, matched by
// session id (recentView), and a judge's or dispatched session sits under the dispatcher
// toggle with no Resume: chela owns its lifecycle. A row's × (or a swipe left) dismisses it;
// the transcript is kept, and an Undo toast offers it back (CMX-437).
import { useRef, useState, type TouchEvent } from 'react';
import { createPortal } from 'react-dom';
import { icons } from '../../../shared/icons/index.ts';
import { type AgentRow, type RecentRow, type RecentState, dismissKey, dispatcherToggleLabel, recentView } from '../model.ts';
import type { SidebarActions } from './actions.ts';

const SWIPE_DISMISS_PX = 70;

function Dismiss({ r, label, actions }: { r: RecentRow; label: string; actions: SidebarActions }) {
  return (
    <button className="recent-dismiss" title="Dismiss: hide this session from Recent (the transcript is kept)"
      aria-label={`Dismiss ${label}`} data-dismiss={dismissKey(r)}
      onClick={e => { e.stopPropagation(); void actions.dismissRecentSession(dismissKey(r)); }}>
      <icons.close size={14} aria-hidden="true" />
    </button>
  );
}

// Swipe left on touch dismisses: a mostly-horizontal leftward drag past the threshold; anything
// shorter or more vertical is a scroll.
function useSwipe(onSwipe: () => void) {
  const start = useRef<{ x: number; y: number } | null>(null);
  const [dx, setDx] = useState(0);
  return {
    style: dx ? { transform: `translateX(${dx}px)` } : undefined,
    onTouchStart: (e: TouchEvent) => { const t = e.touches[0]; if (t) start.current = { x: t.clientX, y: t.clientY }; },
    onTouchMove: (e: TouchEvent) => {
      const t = e.touches[0], s = start.current;
      if (!t || !s) return;
      const d = Math.min(0, t.clientX - s.x);
      if (Math.abs(d) > Math.abs(t.clientY - s.y)) setDx(d);
    },
    onTouchEnd: (e: TouchEvent) => {
      const t = e.changedTouches[0], s = start.current;
      start.current = null;
      setDx(0);
      if (!t || !s) return;
      const x = t.clientX - s.x, y = t.clientY - s.y;
      if (x < -SWIPE_DISMISS_PX && Math.abs(x) > 2 * Math.abs(y)) onSwipe();
    },
  };
}

function Resume({ r, actions }: { r: RecentRow; actions: SidebarActions }) {
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false); // one resume, one window: a second click must not open another
  const click = () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    void Promise.resolve(actions.resumeSession(r)).then(ok => {
      if (ok !== false) return;
      inFlight.current = false;
      setBusy(false);
    });
  };
  return (
    <button className="btn-accent recent-resume" title="Resume this session" disabled={busy}
      data-store={r.store} data-wid={r.wid} data-session={r.session_id ?? ''}
      onClick={e => { e.stopPropagation(); click(); }}>{busy ? 'Resuming…' : 'Resume'}</button>
  );
}

function ResumableRow({ r, actions }: { r: RecentRow; actions: SidebarActions }) {
  const label = r.label || r.cwd || r.wid;
  const swipe = useSwipe(() => void actions.dismissRecentSession(dismissKey(r)));
  return (
    <div className="agent-row recent-row" data-key={`${r.store} ${r.wid}`} {...swipe}>
      <span className="ar-type recent" title="dead session, needs a human to resume"><icons.recent size={13} aria-hidden="true" /></span>
      <div className="ar-main">
        <div className="ar-top"><span className="agent-row-name" title={label}>{label}</span></div>
        <div className="ar-sub"><span className="ar-recap" title={r.cwd ?? ''}>{r.cwd ?? ''}</span></div>
      </div>
      <Resume r={r} actions={actions} />
      <Dismiss r={r} label={label} actions={actions} />
    </div>
  );
}

// A chela-owned row never gets a Resume: resuming it would race the dispatcher's own worktree
// lifecycle. CMX-156: a run the dispatcher will relaunch itself says so, outside the toggle.
function OwnedRow({ r, actions }: { r: RecentRow; actions: SidebarActions }) {
  const label = r.label || r.cwd || r.wid;
  return (
    <div className="agent-row recent-row recent-row-dispatcher"
      title="dispatcher-owned: resuming it would race the dispatcher's own worktree lifecycle">
      <span className="ar-type recent" title="dispatcher-owned session"><icons.recent size={13} aria-hidden="true" /></span>
      <div className="ar-main">
        <div className="ar-top"><span className="agent-row-name" title={label}>{label}</span></div>
        <div className="ar-sub">
          <span className="ar-recap" title={r.cwd ?? ''}>{r.relaunch ? `dispatcher will relaunch ${r.relaunch}` : r.cwd ?? ''}</span>
        </div>
      </div>
      <Dismiss r={r} label={label} actions={actions} />
    </div>
  );
}

function UndoToast({ undo, actions }: { undo: NonNullable<RecentState['undo']>; actions: SidebarActions }) {
  let stack = document.getElementById('run-toast-stack');
  if (!stack) {
    stack = document.createElement('div');
    stack.id = 'run-toast-stack';
    stack.className = 'run-toast-stack';
    document.body.appendChild(stack);
  }
  return createPortal(
    <div className="run-toast recent-undo-toast" role="status" onClick={() => actions.closeRecentUndo()}>
      <span className="recent-undo-text">{undo.text}</span>{' '}
      <button className="recent-undo" onClick={e => { e.stopPropagation(); void actions.undoDismissRecent(undo.sids); }}>Undo</button>
    </div>,
    stack,
  );
}

export function Recent({ recent, agents, actions }: { recent: RecentState; agents: AgentRow[]; actions: SidebarActions }) {
  const live = new Set(agents.map(a => a?.session_id).filter((s): s is string => !!s));
  const { resumable, relaunch, hidden } = recentView(recent, live);
  const toast = recent.undo && <UndoToast undo={recent.undo} actions={actions} />;
  if (!resumable.length && !relaunch.length && !hidden.length) return toast || null;
  return (
    <section className="side-section" id="side-recent-section">
      <div className="side-head">
        <span className="side-title">Recent sessions</span>
        <span className="side-count" id="hdr-recent">{resumable.length}</span>
        {resumable.length > 1 && (
          <button className="recent-clear-all" id="recent-clear-all" title="Dismiss every recent session (transcripts are kept)"
            onClick={() => void actions.clearRecentSessions()}>Clear all</button>
        )}
      </div>
      <div className="side-list" id="side-recent">
        {resumable.map(r => <ResumableRow key={dismissKey(r) || `${r.store} ${r.wid}`} r={r} actions={actions} />)}
        {relaunch.map(r => <OwnedRow key={dismissKey(r)} r={r} actions={actions} />)}
        {hidden.length > 0 && (
          <button className="recent-toggle-dispatcher" aria-expanded={recent.revealed}
            onClick={() => actions.toggleDispatcherSessions()}>{dispatcherToggleLabel(hidden.length, recent.revealed)}</button>
        )}
        {recent.revealed && hidden.map(r => <OwnedRow key={dismissKey(r)} r={r} actions={actions} />)}
      </div>
      {toast}
    </section>
  );
}
