// ---------------------------------------------------------------------------
// The view-registry KERNEL — pure, DOM-free, and deliberately tiny.
//
// The registry itself is views.js (one entry per view). This file is the handful
// of functions everything READS it through: the sidebar, selectView's timer
// dispatch, the global refresh loop and the command palette. They no longer each
// carry their own hardcoded copy of "the list of views" — which is why the
// dashboard grew to seven views and never lost one: a view was cheap to add and
// expensive to delete, so nothing was ever deleted.
//
// No imports on purpose: this is unit-testable in plain node (tests/views.test.mjs
// proves a view can be ADDED and REMOVED by editing the registry alone).
//
// The shape of a view entry (all optional but `id`):
//   id       'work'                 — the canvas panel is `panel-<id>`, the only
//                                     real DOM contract, and it is kept.
//   label    'Work'                 — sidebar + palette text
//   lucide   'columns-3'            — sidebar icon, an inline lucide SVG (fixed box)
//   icon     '▤'                    — sidebar glyph (fallback when no `lucide`)
//   badges   [{id, cls, title}]     — sidebar badge slots (a renderer fills them)
//   enabled  ctx => bool            — e.g. the wall only exists when terminals are on
//   virtual  true                   — reachable, but NOT a nav item (agent-detail)
//   palette  false                  — keep it out of the command palette
//   enter()  — becoming the active view (one-shot render + start its timer)
//   exit()   — leaving it (stop its timer). Called on EVERY other view's entry,
//              so a view that forgets to stop a timer is a bug in one place.
//   tick()   — what the global 30s refresh runs while this view is active
// ---------------------------------------------------------------------------

// The `panel-<id>` id convention is the contract between a view and its markup.
export function panelId(id) { return 'panel-' + id; }

export function findView(views, id) {
    return (views || []).find(v => v && v.id === id) || null;
}

function isEnabled(v, ctx) {
    return typeof v.enabled === 'function' ? !!v.enabled(ctx || {}) : v.enabled !== false;
}

// Everything the sidebar shows, in registry order. `virtual` views (agent-detail)
// are reachable but have no nav item of their own.
export function navViews(views, ctx) {
    return (views || []).filter(v => v && !v.virtual && isEnabled(v, ctx));
}

// CMX-230: navViews split by `tier`, for the sidebar's two nav groups (nav.js's
// renderNav) — the domain-object rail (primary) vs. the demoted "attributes of
// things, not places to go" group (secondary). Both read from the SAME navViews
// list (same order, same enabled/virtual filtering), so nothing about routing,
// the command palette (paletteViews, below) or a view's own behaviour changes —
// only which of the two DOM lists a row's markup lands in. A view with no
// `tier` (or any value other than 'secondary') defaults to primary, so this is
// additive: forgetting to tier a new entry never silently hides it.
export function primaryNavViews(views, ctx) {
    return navViews(views, ctx).filter(v => v.tier !== 'secondary');
}
export function secondaryNavViews(views, ctx) {
    return navViews(views, ctx).filter(v => v.tier === 'secondary');
}

// Everything the command palette can jump to. Same source, same order — the
// palette's third hardcoded list is gone.
export function paletteViews(views, ctx) {
    return navViews(views, ctx).filter(v => v.palette !== false);
}

// Every view whose timers must be stopped when `id` becomes active. Leaving a
// view is not the view's own business — otherwise adding view N means editing
// N-1 else-branches, which is exactly the if/else chain this replaces.
export function otherViews(views, id) {
    return (views || []).filter(v => v && v.id !== id);
}
