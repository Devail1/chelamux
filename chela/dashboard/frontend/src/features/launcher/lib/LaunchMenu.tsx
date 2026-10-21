// The "+ new" launch menu (CMX-212 P20, ported from nav.js's #new-menu and launcher.js by
// CMX-219): Favorites + Recent (click to launch Claude, the shell icon for a plain shell, the
// star to pin or unpin, x to forget a recent), then Init a repo, Scheduled task, the two
// sandboxed sessions and Shell window. One popover, opened from the sidebar's New session, the
// phone "+" and the gear menu's New...; the favorites picker is its second page.
import { useEffect, useState } from 'react';
import { BOOT, api } from '../../../shared/api/index.ts';
import { icons } from '../../../shared/icons/index.ts';
import { createStore, useStore } from '../../../shared/store/index.ts';
import { AnchoredPopover, IconButton, ItemButton } from '../../../shared/ui/index.ts';
import { openInitRepo } from '../../runs/index.ts';
import { showAddSchedule } from '../../schedules/index.ts';
import { entries, forgetRecent, launchProject, launcher, newSandboxedSession, newShellWindow, pinFav, refreshLauncher, unpinFav, type LaunchEntry } from './state.ts';

/** The control the menu hangs off; null = closed. */
const anchor = createStore<HTMLElement | null>(null);

/** Open the launch menu at `at` (re-reading the lists, so a pin from elsewhere shows). */
export function openLaunchMenu(at: HTMLElement) {
  void refreshLauncher();
  anchor.set(at);
}

export function closeLaunchMenu() {
  anchor.set(null);
}

function LaunchRow({ e, pinned }: { e: LaunchEntry; pinned: boolean }) {
  const go = (shell = false) => {
    closeLaunchMenu();
    void launchProject(e.path, { shell });
  };
  // A missing dir is dimmed with a warning but stays clickable: the spawn says what is wrong.
  return (
    <div className={e.exists ? 'launch-row' : 'launch-row lr-gone'} data-path={e.path} title={`${e.path}${e.exists ? '' : ' (missing)'}`}>
      <ItemButton className="lr-main" onClick={() => go()}>
        <span className="lr-icon">{e.exists ? <icons.launch size={10} aria-hidden /> : <icons.warn size={10} aria-hidden />}</span>
        <span className="lr-label">{e.label}</span>
      </ItemButton>
      <span className="lr-actions">
        <IconButton className="lr-shell" label="Open a plain shell here" onClick={() => go(true)}><icons.shell size={12} aria-hidden /></IconButton>
        {pinned
          ? <IconButton className="lr-star pinned" label="Unpin from Favorites" onClick={() => void unpinFav(e.path)}><icons.favorite size={12} fill="currentColor" aria-hidden /></IconButton>
          : <IconButton className="lr-star" label="Pin to Favorites" onClick={() => void pinFav(e.path)}><icons.favorite size={12} aria-hidden /></IconButton>}
        {!pinned && <IconButton className="lr-forget" label="Remove from Recent" onClick={() => void forgetRecent(e.path)}><icons.close size={12} aria-hidden /></IconButton>}
      </span>
    </div>
  );
}

type Suggestion = { path: string; label: string; pinned?: boolean };

/** "Add a project to Favorites": the git repos under CHELA_PROJECTS_DIR not pinned yet. */
function FavAdd({ onDone }: { onDone: () => void }) {
  const [list, setList] = useState<Suggestion[] | null>(null);
  useEffect(() => {
    api('/api/launcher/suggest')
      .then((d) => setList((Array.isArray(d) ? (d as Suggestion[]) : []).filter((s) => !s.pinned)))
      .catch(() => setList([]));
  }, []);
  if (list === null) return <div className="popover-item popover-note">Scanning…</div>;
  if (!list.length) return <div className="popover-item popover-note">No projects to add <span className="popover-hint">(set CHELA_PROJECTS_DIR)</span></div>;
  return (
    <>
      {list.map((s) => (
        <ItemButton key={s.path} title={s.path} onClick={() => {
            void pinFav(s.path);
            onDone();
          }}>{s.label}</ItemButton>
      ))}
    </>
  );
}

function Launch({ onAdd }: { onAdd: () => void }) {
  const data = useStore(launcher);
  const favorites = entries(data.favorites);
  const recent = entries(data.recent);
  const act = (fn: () => unknown) => () => {
    closeLaunchMenu();
    void fn();
  };
  return (
    <>
      {BOOT.terminalsEnabled && (
        <>
          <div id="new-menu-launch">
            <div className="launch-group-label">Favorites<IconButton className="side-add" label="Add a project to Favorites" onClick={onAdd}><icons.add size={12} aria-hidden /></IconButton></div>
            {favorites.length ? favorites.map((e) => <LaunchRow key={e.path} e={e} pinned />) : <div className="side-empty">No projects yet. Add one with +</div>}
            {recent.length > 0 && <div className="launch-group-label">Recent</div>}
            {recent.map((e) => <LaunchRow key={e.path} e={e} pinned={false} />)}
          </div>
          <div className="popover-sep" />
        </>
      )}
      <ItemButton onClick={act(() => openInitRepo(launcher.get()))}>Init a repo</ItemButton>
      <ItemButton onClick={act(showAddSchedule)}>Scheduled task</ItemButton>
      {/* CMX-403: the ONLY kind of window a share guest may type into (docs/SHARE_SANDBOX.md) */}
      <ItemButton id="new-sandbox-item" title="A container that sees only the project, the only kind of window a share guest may type into"
        onClick={act(() => newSandboxedSession())}>Sandboxed session…</ItemButton>
      {/* CMX-418: the same container, opted in to web access (public hosts only, logged) */}
      <ItemButton id="new-sandbox-web-item" title="A sandboxed session that may also read PUBLIC web pages, never your host or LAN. Every request is logged"
        onClick={act(() => newSandboxedSession(true))}>Sandboxed session · allow web access…</ItemButton>
      <ItemButton id="new-shell-item" onClick={act(newShellWindow)}>Shell window</ItemButton>
    </>
  );
}

/** The menu itself (#launcher-island). */
export function LaunchMenu() {
  const at = useStore(anchor);
  const [page, setPage] = useState<'launch' | 'favadd'>('launch');
  const close = () => {
    closeLaunchMenu();
    setPage('launch');
  };
  return (
    <AnchoredPopover anchor={at} onClose={close} className={page === 'launch' ? 'launch-menu' : undefined} label={page === 'launch' ? 'New' : 'Add a project to Favorites'}>
      {page === 'launch' ? <Launch onAdd={() => setPage('favadd')} /> : <FavAdd onDone={close} />}
    </AnchoredPopover>
  );
}

/** The sidebar's New session (CMX-377): the menu's prominent entry point. */
export function NewSessionButton() {
  return (
    <IconButton className="sidebar-new-btn" label="New session" onClick={(e) => openLaunchMenu(e.currentTarget)}>
      <icons.add size={16} aria-hidden />
      <span>New session</span>
    </IconButton>
  );
}

/**
 * The phone's "+" (CMX-386): New session in one tap, with the drawer left closed. Outside the
 * sidebar, whose transform would trap it off-screen; desktop hides it.
 */
export function NewSessionFab() {
  return (
    <IconButton className="mobile-new-fab" id="btn-new-mobile" label="New session" onClick={(e) => openLaunchMenu(e.currentTarget)}>
      <icons.add aria-hidden />
    </IconButton>
  );
}
