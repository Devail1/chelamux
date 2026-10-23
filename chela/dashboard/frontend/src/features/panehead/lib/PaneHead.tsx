// CMX-241 (CMX-212 P26): a Wall pane's header: the status dot, the title (double-click to
// rename the WINDOW), the facepile and room badge slots, the web chip, the UNSANDBOXED
// banner, the state pill, the ⋯ menu (Wire, Share, Orchestrator, Pin) and the window controls
// (minimize, maximize, kill with its confirm). From terminals.js `paneHead` and its handlers.
// `wall` is a Gridstack tile; otherwise it is the single pane. CMX-313: on a phone the single
// pane's header is the screen's one title bar (the Claude app's): the drawer's hamburger on
// the left, the title over its subtitle in the middle, the status chip and ⋯ on the right,
// and Close in the ⋯ menu. No maximize there: the phone pane is always full.
import { useEffect, useRef, useState, type MouseEvent as ReactMouseEvent } from 'react';
import { api, BASE_PATH } from '../../../shared/api/index.ts';
import { icons } from '../../../shared/icons/index.ts';
import { orchestratorRelease, orchestratorSubscribe } from '../../../shared/orchestrator/index.ts';
import { agents } from '../../../shared/store/agents.ts';
import { setPinned } from '../../../shared/store/wall.ts';
import { IconButton, InlineEdit, Menu, TextButton, useMediaQuery, type MenuItem } from '../../../shared/ui/index.ts';
import { toggleSidebar } from '../../sidebar/controller.ts';
import { paneToast, SHARE_MODE_UI, shareSession, unsafeBannerText, type ShareEntry, type ShareMode } from '../../share/index.ts';
import { dotState, headLabel, headState, type HeadAgent } from '../model.ts';

/** What the header asks of the Wall around it (terminals.js `configurePaneHeads`). */
export interface PaneHeadHost {
  /** Take the tile off the wall into the dock. */
  minimize(wid: string): void;
  /** The window was killed: drop its tile now, not on the next poll. */
  dropPane(wid: string): void;
  /** The Wire row was pressed: start the drag toward a peer tile. */
  wireDragStart(e: MouseEvent, from: HTMLElement, wid: string): void;
  /** The room badge: leave the room it names. */
  leaveRoom(badge: HTMLElement, wid: string): void;
  /** A rename landed in the agents store: relabel what legacy still draws (dock, dropdown). */
  labelsChanged(): void;
}

export interface PaneHeadProps {
  wid: string;
  wall: boolean;
  agent: HeadAgent | undefined;
  /** Who holds the decisions inbox (shared/store/orchestrator). */
  orchWid: string | null;
  share: ShareEntry | undefined;
  pinned: boolean;
  /** Paint time, for the UNSANDBOXED banner's time left. */
  now: number;
  host: PaneHeadHost;
}

const MODE_ICON: Record<ShareMode, typeof icons.viewOnly> = { view: icons.viewOnly, typing: icons.typing, unsandboxed: icons.unsandboxed };
const ORCH_TITLE = { on: 'This pane receives the decisions inbox, click to release', off: 'Click to receive the decisions inbox in this pane' };
const PIN_TITLE = {
  on: 'Pinned: this pane keeps its spot when a grid layout is applied. Click to unpin',
  off: "Pin: keep this pane's position when a grid layout is applied",
};
const WEB_TITLE = 'Sandboxed session with web access: public hosts only, rate-limited, every request logged';

type Kill = { phase: 'idle' } | { phase: 'confirm' } | { phase: 'killing' } | { phase: 'error'; msg: string };

/** Rename the WINDOW (POST, tmux rename-window), then reread /api/agents: every client, tmux and
 * the bound Telegram topic see the new name. There is deliberately no local label store. */
async function renameWindow(wid: string, name: string): Promise<void> {
  const res = (await api(`/api/agents/${encodeURIComponent(wid)}/rename`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })) as { ok?: boolean; error?: string } | null;
  if (!res || !res.ok) throw new Error((res && res.error) || 'rename failed');
  const rows = await api('/api/agents');
  agents.set(Array.isArray(rows) ? rows : []);
}

/** POST /api/agents/kill; '' on success, else what went wrong. */
async function killWindow(wid: string): Promise<string> {
  let resp: Response;
  let data: { ok?: boolean; error?: string } = {};
  try {
    resp = await fetch(`${BASE_PATH}/api/agents/kill`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent: wid }),
    });
    try {
      data = ((await resp.json()) as typeof data | null) || {};
    } catch {
      data = {};
    }
  } catch (e) {
    return String(e);
  }
  if (!resp.ok || !data.ok) return data.error || `HTTP ${resp.status}`;
  return '';
}

export function PaneHead({ wid, wall, agent, orchWid, share, pinned, now, host }: PaneHeadProps) {
  const head = useRef<HTMLDivElement>(null);
  const [editing, setEditing] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [orchBusy, setOrchBusy] = useState(false);
  const [maxed, setMaxed] = useState(false);
  const [kill, setKill] = useState<Kill>({ phase: 'idle' });
  const phone = useMediaQuery('(max-width: 768px)');
  const titleBar = phone && !wall;

  const label = headLabel(agent, wid);
  const state = headState(agent);
  const mode = share?.mode ?? null;
  const orch = orchWid === wid;

  const commitRename = async (value: string) => {
    setEditing(false);
    const name = value.trim();
    if (!name || name === (agent?.name ?? wid)) return;
    try {
      await renameWindow(wid, name);
      host.labelsChanged();
    } catch (e) {
      console.error('renamePane', e);
      paneToast(head.current, `Rename failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const toggleOrch = async () => {
    setOrchBusy(true);
    try {
      const r = orch ? await orchestratorRelease(wid) : await orchestratorSubscribe(wid);
      if (!r || !r.ok) console.error('orchestrator toggle failed', r && r.error);
    } finally {
      setOrchBusy(false);
    }
  };

  // Lift the pane to a full-viewport overlay; Gridstack's layout is untouched, so restore is
  // the exact inverse. No Escape binding: Escape is the agent's interrupt key.
  const toggleMax = () => {
    const pane = head.current?.closest('.term-pane, .grid-stack-item-content');
    if (!pane) return;
    const on = pane.classList.toggle('pane-maximized');
    document.body.classList.toggle('pane-is-maximized', on);
    setMaxed(on);
  };

  // A pane maximized on a desktop that narrows to a phone: the title bar has no restore, so
  // undo it (the phone pane is always full anyway). The state resets during the render, the
  // page classes in the effect.
  if (titleBar && maxed) setMaxed(false);
  useEffect(() => {
    if (!titleBar) return;
    const pane = head.current?.closest('.term-pane, .grid-stack-item-content');
    if (pane?.classList.contains('pane-maximized')) {
      pane.classList.remove('pane-maximized');
      document.body.classList.remove('pane-is-maximized');
    }
  }, [titleBar]);

  const confirmKill = async () => {
    setKill({ phase: 'killing' });
    const err = await killWindow(wid);
    if (err) setKill({ phase: 'error', msg: err });
    else host.dropPane(wid);
  };

  const ModeIcon = mode ? MODE_ICON[mode] : null;
  const items: MenuItem[] = [
    ...(wall
      ? [{
          label: 'Wire to…',
          icon: icons.wire,
          className: 'gs-port',
          title: 'Wire this agent to another: drag onto a peer',
          onSelect: () => {},
          // The press starts the drag; the menu closes at once so it never floats over the wall.
          onMouseDown: (e: ReactMouseEvent<HTMLElement>) => {
            setMenuOpen(false);
            host.wireDragStart(e.nativeEvent, e.currentTarget, wid);
          },
        }]
      : []),
    {
      label: 'Share current session',
      icon: icons.share,
      className: mode ? 'gs-share-btn on' : 'gs-share-btn',
      title: 'Share this session',
      data: { 'data-wid': wid, 'data-mode': mode ?? '' },
      onSelect: () => void shareSession(wid),
      trailing: (
        <>
          {ModeIcon && mode && (
            <span className="gs-share-mode" title={SHARE_MODE_UI[mode].label}>
              <ModeIcon size={12} aria-label={SHARE_MODE_UI[mode].label} />
            </span>
          )}
          {/* presence-owner.js writes the live joiner count here */}
          <span className="gs-share-count" hidden />
        </>
      ),
    },
    {
      label: 'Orchestrator',
      icon: icons.orchestrator,
      className: 'gs-orch-btn',
      check: 'checkbox',
      checked: orch,
      disabled: orchBusy,
      title: orch ? ORCH_TITLE.on : ORCH_TITLE.off,
      data: { 'data-wid': wid },
      onSelect: () => void toggleOrch(),
    },
    ...(wall
      ? [{
          label: 'Pin',
          icon: icons.pin,
          className: 'gs-pin-btn',
          check: 'checkbox' as const,
          checked: pinned,
          title: pinned ? PIN_TITLE.on : PIN_TITLE.off,
          onSelect: () => setPinned(wid, !pinned),
        }]
      : []),
    ...(titleBar
      ? [{
          label: 'Close session',
          icon: icons.kill,
          className: 'gs-kill-item',
          title: 'Kill this session',
          disabled: kill.phase !== 'idle',
          onSelect: () => setKill({ phase: 'confirm' }),
        }]
      : []),
  ];

  const Max = maxed ? icons.restore : icons.maximize;
  const titleEl = editing ? (
    <InlineEdit className="pane-title-edit" label="Window name" initial={agent?.name ?? wid} onCommit={(v) => void commitRename(v)} onCancel={() => setEditing(false)} />
  ) : (
    <span className="pane-title" title="double-click to rename" onDoubleClick={(e) => {
        e.stopPropagation();
        setEditing(true);
      }}>
      {label.title}
    </span>
  );
  const subtitle = label.subtitle && (
    <span className={label.cwd ? 'pane-subtitle pane-cwd' : 'pane-subtitle'} title={label.subtitle}>
      {label.subtitle}
    </span>
  );
  // CMX-32: the orchestrator ring is on the WRAPPER: the dot's shapes are clip-paths
  const dot = (
    <span className={orch ? 'gs-dot-ring gs-dot-orch' : 'gs-dot-ring'} data-wid={wid}>
      <span className={`gs-dot term-status-dot ${dotState(agent)}`} data-status-for={wid} data-wid={wid} title={state.title} />
    </span>
  );
  // the owner-presence facepile (collab/presence-owner.js draws into it)
  const presence = <span className="gs-presence" data-presence-for={wid} />;
  const badges = (
    <>
      <span className="gs-net-badge" data-net-for={wid} title={WEB_TITLE} hidden={agent?.share_net !== 'web'}>
        <icons.web size={11} aria-hidden /> web
      </span>
      <span className="gs-unsafe-banner" data-banner-for={wid} role="status" hidden={mode !== 'unsandboxed'}>
        {mode === 'unsandboxed' && (
          <>
            <icons.unsandboxed size={11} aria-hidden /> {unsafeBannerText(share?.expiresAt, now)}
          </>
        )}
      </span>
    </>
  );
  const menu = (
    <span className="gs-menu-wrap">
      <Menu className="pane-overflow-menu" open={menuOpen} onOpenChange={setMenuOpen} items={items}
        trigger={
          <IconButton className="gs-menu-btn" label="Session actions">
            <icons.paneMenu size={titleBar ? 18 : 14} aria-hidden />
          </IconButton>
        } />
    </span>
  );

  return (
    <>
      {titleBar ? (
        <div className="gs-head gs-head-bar" ref={head}>
          {/* the drawer's opener, where the floating ☰ used to be (the controller keeps aria-expanded) */}
          <IconButton className="gs-head-menu" label="Open menu" aria-expanded={document.body.classList.contains('sidebar-open')}
            onClick={() => toggleSidebar()}>
            <icons.menu size={18} aria-hidden />
          </IconButton>
          <span className="gs-label">
            {titleEl}
            {subtitle}
          </span>
          <span className="gs-head-end">
            {presence}
            {badges}
            <span className="gs-status-chip">{dot}</span>
            {menu}
          </span>
        </div>
      ) : (
        <div className="gs-head" ref={head}>
          {dot}
          {/* the NAME is Gridstack's drag handle on the wall (CMX-117) */}
          <span className={wall ? 'gs-grip' : 'gs-label'} title={wall ? 'drag to move' : undefined}>
            {titleEl}
            {subtitle}
          </span>
          {wall && (
            // wire.ts applyRoomAccents shows it and writes the room's name
            <TextButton className="gs-room" hidden onClick={(e) => host.leaveRoom(e.currentTarget, wid)}>
              <icons.room size={11} aria-hidden />
              <span className="gs-room-name" />
            </TextButton>
          )}
          {presence}
          {badges}
          {wall && (
            <span className={`gs-state gs-state-${state.cls}`} data-state-for={wid} title={state.title}>
              <span className="gs-state-word">{state.word}</span>
            </span>
          )}
          {menu}
          <span className="gs-keys">
            <span className="gs-win-ctl">
              {wall && (
                <IconButton className="gs-min-btn" label="Minimize to dock" onClick={() => host.minimize(wid)}>
                  <icons.minimize size={14} aria-hidden />
                </IconButton>
              )}
              <IconButton className="gs-max-btn" label={maxed ? 'Restore pane' : 'Maximize pane'} aria-pressed={maxed} onClick={toggleMax}>
                <Max size={14} aria-hidden />
              </IconButton>
              <IconButton className="gs-kill-btn" label="Kill this session" style={kill.phase === 'idle' ? undefined : { visibility: 'hidden' }}
                onClick={() => setKill({ phase: 'confirm' })}>
                <icons.kill size={14} aria-hidden />
              </IconButton>
            </span>
          </span>
        </div>
      )}
      {kill.phase !== 'idle' && (
        // over the middle of the pane, never window.confirm; the pane is its containing block
        <div className="kanban-confirm pane-kill-confirm" data-agent={wid} role="alertdialog" aria-label={`Kill ${label.title}`}>
          {kill.phase === 'confirm' && (
            <>
              <span className="kanban-confirm-msg">Kill {label.title}?</span>
              <TextButton className="btn-confirm" onClick={() => void confirmKill()}>Kill</TextButton>
              <TextButton onClick={() => setKill({ phase: 'idle' })}>Cancel</TextButton>
            </>
          )}
          {kill.phase === 'killing' && <span className="kanban-confirm-msg">Killing…</span>}
          {kill.phase === 'error' && (
            <>
              <span className="kanban-confirm-msg pane-kill-error">{kill.msg}</span>
              <TextButton onClick={() => setKill({ phase: 'idle' })}>Close</TextButton>
            </>
          )}
        </div>
      )}
    </>
  );
}
