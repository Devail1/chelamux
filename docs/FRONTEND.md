# The dashboard frontend

The authority on chela's frontend (the repo's `CLAUDE.md` says so). Where it disagrees with a
generic skill, including the vendored React skills in `.claude/skills/`, this file wins. The
plan for porting the rest of the dashboard is `docs/FRONTEND_PORT_PLAN.md` (CMX-212).

## The stack

The dashboard is a Flask API (`chela/dashboard/app.py`) serving one page,
`chela/dashboard/templates/index.html`, whose body is one React root (CMX-248). Its code:

| | Where | Built? |
|---|---|---|
| **React** | `chela/dashboard/frontend/src/`, TypeScript + React 19 | Vite, into `static/app/` |
| **Pure models** | `chela/dashboard/static/js/*.ts` (`wallmodel`, `windowid`, `wire`), imported by `src/` | bundled with it |

It reaches the browser as ONE bundle: the build's entry `src/main.tsx` imports
`static/style.css` (emitted as the bundle's stylesheet, which `app.py` links from the
manifest) and `src/boot.ts`, then mounts the page and starts it.

The React side is a Vite single-page build, **not** Next.js: no server components, no
`"use client"`, no file routing, no `next/*`. Tests are Vitest + React Testing Library in
jsdom. The toolchain is dev-only and pinned exactly (`frontend/package.json`); users never
need Node, because the build output in `chela/dashboard/static/app/` is **committed**
(CMX-177). `tests/test_frontend_build.py` fails while it differs from a fresh build, so after
any change under `src/` **or `static/js/`** (and the `static/collab/` modules it imports):

```bash
pnpm -C chela/dashboard/frontend build    # then commit static/app/ with your change
```

The bundle's name is a content hash, so a rebuild renames it; nothing pins the name (the
manifest names it). When two PRs both rebuilt it, the second one conflicts with the base only
on `static/app/`: the dispatcher's base merge (CMX-300, `chela/basemerge.py`) then rebuilds
the bundle on the merge, runs the stale-asset check and pushes it, instead of sending the PR
back. A conflict on any other file, or a rebuild that fails, is still sent back.

What each test suite runs: the `tests/browser/` Playwright suites load the **committed
bundle** (what Flask serves), so they see a source edit only after the rebuild; the jsdom
suites (`tests/js_helpers/dashboard_dom.mjs`) boot `src/main.tsx`'s source, compiled by Vitest.

The CI gates, each also run inside pytest by `tests/test_frontend.py`: `pnpm typecheck`
(strict `tsc`), `pnpm lint` (typescript-eslint strict + react-hooks), `pnpm lint:boundaries`
(dependency-cruiser module rules) and `pnpm test`.

### Module layout

`src/features/<name>/` holds a feature, `src/shared/<name>/` code several features use. Each
directory is a deep module: its root files (`index.ts`) are its entry points, and anything in
a subfolder (`lib/`, `tests/`) is private to it. Tests import the module's entry point, never
its internals. `pnpm lint:boundaries` enforces this (`.dependency-cruiser.cjs`).

## The app shell

`templates/index.html` is the Vite entry's page (FRONTEND_PORT_PLAN.md §6.2): its `<head>`, the
pre-paint script that restores the collapsed sidebar, the boot JSON block and `<div id="root">`.
No inline handler, no `chela.` call. `app.py`'s `app_entry()` and `app_css()` read the build's
`static/app/manifest.json` once at import, and `index()` renders the entry
(`app/assets/main-<hash>.js`) as the page's one versioned `<script type="module">` and its
stylesheet as a `<link>` in the head. There is no unbundled fallback: a missing or unreadable
manifest logs an error and the page loads without it.

`src/boot.ts` is what the entry runs: `mountPage()` (the bridge, the saved theme, the panes'
`window.chela` contract, the page keys, tap tooltips, and the root), then `start()` (the
view ticks, the nav rows, the shares, the first view, the 30 s loop and the event stream).

The page itself is `src/features/shell` (CMX-248, plan P34): `<App>` renders the sidebar, the
canvas of views, the phone controls, the safety float and the overlays, with the classes and
ids `style.css` and the browser suites have always read. `mountApp()` renders it synchronously,
so the features that find their elements by id (the Wall's stage, the sidebar's rows) start
right after. What each slot draws is one table, `SLOTS` (`lib/slots.tsx`), keyed by the slot
element's id: `<App>` renders every slot in place, and a page of its own without `#root` (a jsdom
fixture of a few slots) mounts the same table into whichever slots it has (`mountSlots()`). A
slot that draws one control inside a row is a `<span class="island-slot">` (`display:
contents`), so the control lays out as the row's own child.

The views are a registry (`lib/registry.ts`, read through the pure `views.ts`): one entry per
view (`id`, `label`, the nav icon, badges, `enabled`, `virtual`, and `enter` / `exit` / `tick`).
The nav rows, the view switch, the scheduler's view tick and the ⌘K palette all derive from it,
so adding or deleting a view is one entry. The active view is the `view` store; each view's
panel (`panel-<id>`) is shown while the store names it and stays mounted while hidden (the
Wall's live frames must never reload). `selectView()` sets it synchronously, so the panel is
visible before the view's `enter` hook runs.

What a feature needs from the rest of the page (the view switch, the Wall, the orchestrator
slot), the shell hands it at import (`lib/nav.ts`): `configureSidebar`, `setAgentDetailHost`,
`setLauncherHost`, `setPaletteHost` and `configureWall`. The tab title and the favicon badge
(`lib/tabSignal.ts`) count the panes blocked on you.

The Wall's own roots are not the shell's: the pane headers, footers and frames portal into
tiles Gridstack owns, and the Wall mounts its toolbar and Minimized tray into the elements
the shell gives it (`#wall-toolbar-island`, `#term-min-dock`).

### Settings, the palette and the iframe contract

Settings (`src/features/settings`, CMX-221) is one `<Dialog>` with every tab rendered while it is
open, so its search (CMX-396) marks the rendered rows in place: a section card is
`.settings-section`, a knob is a `FieldRow` (its `keywords` prop is extra words the search
matches), and the marks are `data-s-hide` / `data-s-hit`, attributes React never renders, so a
re-render keeps them. A row that lands after the query (a tab's fetch) is marked too.

The ⌘K palette and the shortcuts sheet are `src/features/palette`; its `installPalette()` owns the
page's global keys (Ctrl/⌘+K, Ctrl/⌘+,). A focused pane's keys never reach the page:
`app.py`'s `_TERM_PALETTE_KEY_SHIM` calls `window.parent.chela.openPalette()` and
`.toggleSettings()`, which `src/iframeBridge.ts` registers, exactly those two
(`iframeBridge.test.ts` fails if one goes missing or another is added).

## The state bridge

The page's state is `src/shared/store/`, one file per slice (`view.ts`, the active view;
`agents.ts`, the `/api/agents` rows; `work.ts`, the last `/api/dispatcher` payload, which
`src/features/work`'s one poll (a scheduler lane, CMX-233) publishes and the Work view and
New-task dialog read; they ask for a fresh poll with `refreshWork()`, CMX-231; `orchestrator.ts`,
who holds the decisions inbox, read and written through `src/shared/orchestrator`, CMX-228;
`context.ts`, the last `/api/agents/context` rows, which the gear menu's usage readouts and the
agent detail read, CMX-219; `wall.ts`, the Wall's). The sidebar's stores are the bridge's
(`src/shared/bridge`), on `window.chelaBridge`:

| Store | Published by | Value |
|---|---|---|
| `agents` | the sidebar controller's `/api/agents` read, the Wall's | the `/api/agents` rows (also `shared/store/agents.ts`) |
| `sidebarLayout` | the sidebar controller's layout (`features/sidebar/lib/layout.ts`) | `{order, custom, view, width, loaded}` (CMX-82, CMX-278) |
| `sidebar` | the sidebar controller's `renderSidebarAgents()` (and Alt held, the active row) | `SidebarState`: what the sidebar draws (CMX-276) |
| `nav` | the shell's `renderNav()` / `selectView()`, the Work poll (the badges) | the nav rows, the active view, the Work badge counts (CMX-278) |
| `recent` | the sidebar controller (`/api/restore`, dismiss, Undo) | Recent sessions (CMX-278) |

A store is a value plus listeners (`createStore`: `get`, `set`, `subscribe`; `set` with the
same value notifies nobody, so always publish a NEW array or object, never one mutated in place). React reads it with `useStore(store)`, a one-line
`useSyncExternalStore`. That is React's own primitive for an external store: it adds no
dependency, and it renders a consistent value even during concurrent rendering. A library
(zustand) would add selectors and middleware nobody needs; Redux is ruled out.
`useStoreNow(store)` is the same read, repainted before `set` returns, for a store whose writer
reads the DOM straight back (the sidebar's).

To add a bridge store: add it to `Bridge` and `createBridge()` in `src/shared/bridge/index.ts`.

### The sidebar

The sidebar is `src/features/sidebar` (CMX-172: slices CMX-276, CMX-277, CMX-278). Its parts
draw into the shell's slots: the session list `#sidebar-agents`, the VIEW menu
`#sidebar-view-menu`, the nav rows `#side-nav`, Recent sessions `#side-recent-island` and the
width handle `#sidebar-resize`. Its group, row and VIEW menus are the kit's `Menu`.

Its state and commands are `controller.ts`, the module's second entry point (the pure
`model.ts` is the third): the `/api/agents` read the sidebar draws (its scheduler lane), the
per-device collapsed groups, the server layout and archive, Recent sessions, the collapsed rail
and phone drawer, the Alt fast switch, and the width. What it needs from the rest of the page
(a window's label, the wall, opening a pane, the launcher, the orchestrator slot) it reaches
through the host the shell gives `configureSidebar()`; the shell, the launcher, the Work poll
and `features/wall` import it. Its components' clicks reach it through the shell's
`sidebarActions` (the controller's commands, and the shell's `selectView` and `selectAgent`).

Every part repaints synchronously (`useStoreNow`) on a publish of its stores, because the
controller reads the rows straight back (Alt+N, jump to the orchestrator).
`tests/js_helpers/dashboard_dom.mjs` boots the page (`APP_BODY` is the real one, `#root`) for
every suite, and returns the controller as `modules.sidebar` (`extraModules: [SIDEBAR]`); a
suite with its own jsdom boot calls `mountIslands(window)` (the entry's `mountPage()`, or a
fixture's slots) and then `boot.ts`'s `start()`. Icons come from `src/shared/icons`, a map from
a meaning to a Lucide icon.

The desktop width (CMX-278) is `--sidebar-w` on the root element, set from the layout's `width`
(clamped to 264-480 px, default 288; `chela/sidebar_layout.py` clamps the same). The handle sets
it live while dragging and saves it on release. The phone drawer keeps its own 264 px.

### The Wall

The Wall is `src/features/wall` (CMX-247, plan P33; it was `static/js/terminals.js`): the
Gridstack grid (`lib/grid.ts`: init, adopt and remove keyed by window id, destroy; the saved
layout `pc_wall_layout`, the presets, lock's swap-on-drop, auto-arrange and Focus) and the rest
(`lib/wall.ts`: wall vs single mode, minimize and restore, the last viewed pane, the 4 s
reconcile and the `windows` event). The vendored Gridstack bundle stays the layout engine, a page
global read when the wall builds. A tile element is plain DOM, because Gridstack owns it (it
adopts, moves and removes it); everything inside a tile is React, portaled into the tile's
slots. Single mode has no picker of its own: the open pane is `activePane()`, switched by
`showPane(wid)` (a sidebar tap, the header swipe, a focus). The shell hands it the view switch
and the tab signal through `configureWall()`.

The pane headers are React (`src/features/panehead`, CMX-241, plan P26), the first Wall surface ported inside out. Every
header on the page is ONE root, portaled into a slot each tile's markup carries
(`<div class="island-slot" data-head-for="@N" data-head-wall="1">`, `display: contents`).
`features/wall` calls `mountPaneHeads(scope)` right after inserting tiles, and BEFORE
Gridstack adopts them: Gridstack finds each tile's `.gs-grip` drag handle (the title) when it
inits. A slot that leaves the page is dropped on the next paint.

Paints are synchronous (`flushSync`), on a mount and on every publish of the stores a header
reads: `agents`, the share state, `orchestrator` and `pinnedPanes` (`shared/store/wall.ts`,
which the grid reads too). What a header asks of the Wall (minimize, drop a killed
tile, start a wire, leave a room, relabel the dock) goes through the host `features/wall` hands
`configurePaneHeads()`. Three header elements are still filled by vanilla code, so they
render with no React children: the facepile `.gs-presence` and the Share row's
`.gs-share-count` (`collab/presence-owner.js`), and the room badge `.gs-room` (`wire.ts`).

On a phone the single pane's header is the screen's one title bar (CMX-313, `.gs-head-bar`): the
drawer's hamburger on the left, the title over its subtitle in the middle, the status chip and
⋮ on the right. Close is a row of the ⋮ menu and there is no maximize, because the pane is
always full: `style.css` pins it to the viewport, edge to edge, from the top to the keybar.
The floating ☰ (`.mobile-menu-fab`) shows only while no pane is open, and there is no phone
"+": New session is the drawer's own button.

The rest of a tile is React too (CMX-242, plan P27/P28/P30/P32), on the same pattern:

| Module | Draws | Mounted by features/wall into |
|---|---|---|
| `features/tilefoot` | the footer (`.term-ctx-bar`: window id, model, PR, cost, branch, context %, Files) and the wall's action bar; the recap line | each tile's `[data-foot-for]` and `[data-recap-slot]` slots (one root, portals, `flushSync`) |
| `features/walldock` | the Minimized tray (one row, scrolling sideways) and the lazy dock's decisions for dispatched workers | `#term-min-dock` |
| `features/walltoolbar` | the layout toolbar (presets, lock, auto-arrange, Focus) | `#wall-toolbar-island` |
| `features/wire` | the wire gesture and its SVG line; the room accents (`static/js/wire.ts` holds the geometry) | over `#term-stage` while a wire is in flight |
| `features/paneframe` | the terminal: the ttyd `<iframe>` (its src and per-connection cid), the readiness placeholder and its poll, the focus ring, the keys a pane carries (Alt+N, Ctrl/⌘+K), watch reporting, the background-tab teardown, the 4 s tick; and the keybar's keys and Paste (CMX-246, plan P31) | each tile's `[data-frame-for]` slot |

React renders the pane's `<iframe>` and owns its src, never anything inside it. A frame keeps
its cid for its mount, so a repaint never reconnects. The teardown blanks a frame by drawing it
`about:blank` with its src in `data-suspended-src` (`term-reconnect.js` reads both); what else
crosses the iframe boundary is plan §3's table, one test per row in
`features/paneframe/tests/boundary.test.tsx`. The 4 s tick's timer is the frame's; the
reconcile it runs (drop and absorb panes) is `features/wall`'s `termTick`, handed in through
`configurePaneFrames()` with the grid it rebuilds.

The Wall's shared state is `shared/store/wall.ts`: `termMode`, `pinnedPanes`, `minimizedPanes`
and `wallLayout` (`{preset, locked, auto, focus}`; auto-arrange and Focus exclude each other in
the setters). The toolbar and the dock write it; `features/wall` owns the Gridstack grid
and applies each change (`_onLayoutChange`, minimize and restore). The footer reads the
`context` store, which the Wall's 4 s tick publishes.

### Markdown

`src/shared/markdown` renders the task briefs and judge reports (`markdownHtml`, `inlineHtml`).
It escapes every character of its source and links only http(s), mailto and relative URLs: a
`javascript:`/`data:` link renders as its text (CMX-228). React renders its output only through
`<Markdown>`, the app's one `dangerouslySetInnerHTML`.

### Overlays on a phone

Every `Dialog` and `Sheet` of the kit (`src/shared/ui`) is drawn on ONE layer, `.ui-overlay`
(`--z-overlay`, 200 in `style.css`): above the phone drawer (`--z-drawer`, 45) and the safety
float, under the floating layer (a Select's list, 210). A variant's own class never sets the
backdrop's z-index. Each draws a close button (`.ui-close`, 44px on a touch screen), a tap on
the backdrop closes it, and opening one runs `onOverlayOpen()` listeners: the sidebar
controller registers `closeSidebar()` there, so an overlay opened from the drawer opens with it
shut (CMX-306). `tests/browser/overlays_phone.test.mjs` drives every ported overlay at phone
width; a new overlay gets a row there.

## Polls, pushes and the boot config

- **`src/shared/api`**: `api()`, `BASE_PATH`, `staticUrl()` and `BOOT`, the config Flask renders
  into index.html as one JSON block (`#chela-boot`: `assetVersion`, `terminalsEnabled`,
  `wallTileDispatched`). A missing or malformed field runs as its default.
- **`src/shared/scheduler`**: the 30 s refresh loop. A feature adds a poll with
  `registerLane(name, tick => ..., phase)` from its own module. Lanes are independent (one
  that throws is logged, the rest run), share ONE `/api/agents/context` read per tick
  (`tick.context`), and the active view's tick starts once a lane calls
  `tick.paneListKnown()` (CMX-166). `phase` is `'tick'` (awaited), `'detached'` (not awaited,
  e.g. the update check) or `'settled'` (after every `'tick'` lane).
- **`src/shared/events`**: the page's one `EventSource` on `/api/events`. A feature subscribes
  with `onEvent(name, data => ...)` for the events it owns (`hello`, `windows`, `runs`, `log`,
  `term-ready`, `orchestrator`, `overrides`). Frames are notifications: a handler refetches.

## Adding a component, with its test

1. Create `src/features/<name>/lib/<Name>.tsx` and export it from
   `src/features/<name>/index.ts`.
2. Test it in `src/features/<name>/tests/<Name>.test.tsx` with React Testing Library,
   importing `../index.ts`. Stub the network with `vi.stubGlobal('fetch', …)`. Cover every
   branch, including what a failed read renders: unknown must never render as a value.
3. For a new region of the page: give it a slot in `features/shell`'s `<App>` and its entry in
   `SLOTS`.
4. `pnpm -C chela/dashboard/frontend typecheck lint lint:boundaries test`, then `build`, and
   commit `static/app/` with the source.
5. A UI that must render on the real page gets a browser test in `tests/browser/` too:
   `react_islands.test.mjs` runs an isolated dashboard (temp `CHELA_DIR`, its own port).
   Never point a test at the live dashboard.

No em dashes in UI strings.

## The dev loop

```bash
pnpm install --frozen-lockfile                       # once, from the repo root
CHELA_DEV_API=http://127.0.0.1:<port> pnpm -C chela/dashboard/frontend dev
```

`pnpm dev` serves `frontend/index.html`, the page without Flask (so without the Gridstack
bundle and ttyd: it runs with terminals off), with hot reload, and proxies `/api` to the
dashboard at `CHELA_DEV_API` (default `http://127.0.0.1:5001`, the dashboard's default port).
To see the Wall, `build` and reload the dashboard.

## How the judge grades TypeScript

The judge and `chela judge sweep` mutate `.ts`/`.tsx` like `.js`, line by line, plus some
TypeScript-only mutations (`chela/sweep.py`, CMX-173). A mutant that no longer parses with
the repo's own `typescript` is discarded as invalid, not counted as caught
(`chela/judge.py`, CMX-175). A mutated file selects the test files whose import graph reaches
it, transitive importers included (`chela/judge_select.py`). The Vitest suite runs inside
`uv run pytest` (`tests/test_frontend.py`), which is the judge's test command, so a
`*.test.tsx` that catches a mutant counts like any Python test. Spec files are never mutated.
For self-check experiments on `.tsx`, the `before` anchor is TypeScript source text.
