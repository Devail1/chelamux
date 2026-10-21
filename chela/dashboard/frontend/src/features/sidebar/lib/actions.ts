// CMX-277: what the sidebar's rows and menus call, nav.js's handlers on window.chela (mount.tsx).
/** nav.js's sidebar commands (window.chela), what the rows and the menus call. */
export interface SidebarActions {
  selectAgent(name: string): void;
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
}
