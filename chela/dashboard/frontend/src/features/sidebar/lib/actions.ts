// CMX-277: what the sidebar's rows and menus call, the handlers on window.chela (mount.tsx).
// CMX-278: the commands are controller.ts's; selectAgent / selectView stay nav.js's.
import type { RecentRow } from '../model.ts';

/** The sidebar's commands (window.chela), what the rows, menus, nav, Recent and the handle call. */
export interface SidebarActions {
  selectAgent(name: string): void;
  selectView(id: string): void;
  toggleGroup(key: string): void;
  groupNewSession(key: string): unknown;
  toggleArchivedSection(): void;
  unarchiveRow(btn: null, key: string): unknown;
  /** `new` / `up` / `down` / `collapse-all` / `expand-all` / `archive` */
  groupMenuAction(key: string, act: string): unknown;
  /** `agent` / `judge` (open that pane) / `archive` */
  rowMenuAction(key: string, act: string): unknown;
  moveToGroup(itemKey: string, id: string | null): void;
  /** One View choice: `status:all`, `env:judge` (toggles), `group:state`, `toggle:showPR`, … */
  viewMenuAction(act: string): void;
  /** Prompts for the name; null when none was given. */
  newCustomGroup(): { id: string; name: string } | null | undefined;
  renameCustomGroup(id: string): void;
  deleteCustomGroup(id: string): void;
  /** The drag handle's commit, in px (clamped and saved to the server layout). */
  setSidebarWidth(w: number): void;
  /** Resolves false when the resume failed. */
  resumeSession(r: RecentRow): Promise<boolean> | unknown;
  dismissRecentSession(key: string): unknown;
  clearRecentSessions(): unknown;
  undoDismissRecent(sids: string[]): unknown;
  closeRecentUndo(): void;
  toggleDispatcherSessions(): void;
}
