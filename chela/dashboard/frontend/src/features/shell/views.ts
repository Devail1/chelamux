// CMX-248 (CMX-212 P34): the view registry's kernel (static/js/viewreg.js until now). Pure and
// DOM-free: the sidebar's nav rows, the view switch's enter/exit hooks, the scheduler's view
// tick and the ⌘K palette all read the registry (lib/registry.ts) through these, so adding or
// deleting a view is one entry there and nothing else. Before the registry, a view lived in
// four hardcoded lists and was cheap to add, expensive to delete: the dashboard grew to seven.

export interface ViewCtx {
  terminalsOn: boolean;
}

export interface ViewBadge {
  id: string;
  cls?: string;
  title?: string;
  text?: string;
}

/** One view. Everything but `id` is optional. */
export interface View {
  /** Its canvas panel is `panel-<id>`. */
  id: string;
  /** The nav row's and the palette's text. */
  label?: string;
  /** The nav row's icon (features/sidebar maps the id to one). */
  lucide?: string;
  /** The nav row's badge slots (the Work poll fills them). */
  badges?: ViewBadge[];
  enabled?: boolean | ((ctx: ViewCtx) => boolean);
  /** Reachable, but not a nav row or a palette entry (the agent drill-in). */
  virtual?: boolean;
  /** false keeps it out of the palette. */
  palette?: boolean;
  /** Becoming the active view (start its timer). */
  enter?(): void;
  /** Leaving it (stop its timer); run on EVERY other view's entry. */
  exit?(): void;
  /** What the 30 s refresh runs while this view is active. */
  tick?(context: Promise<unknown> | null): unknown;
}

export const panelId = (id: string): string => `panel-${id}`;

export function findView(views: readonly View[], id: string): View | null {
  return views.find((v) => v.id === id) ?? null;
}

const isEnabled = (v: View, ctx: ViewCtx) => (typeof v.enabled === 'function' ? !!v.enabled(ctx) : v.enabled !== false);

/** The sidebar's nav rows, in registry order: every enabled, non-virtual view. */
export function navViews(views: readonly View[], ctx: ViewCtx): View[] {
  return views.filter((v) => !v.virtual && isEnabled(v, ctx));
}

/** What the palette can jump to: the same list, minus a `palette: false` view. */
export function paletteViews(views: readonly View[], ctx: ViewCtx): View[] {
  return navViews(views, ctx).filter((v) => v.palette !== false);
}

/** Every view to stop when `id` becomes active. */
export function otherViews(views: readonly View[], id: string): View[] {
  return views.filter((v) => v.id !== id);
}
