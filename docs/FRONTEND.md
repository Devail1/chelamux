# The dashboard frontend

The authority on chela's frontend (the repo's `CLAUDE.md` says so). Where it disagrees with a
generic skill, including the vendored React skills in `.claude/skills/`, this file wins. The
plan for porting the rest of the dashboard is `docs/FRONTEND_PORT_PLAN.md` (CMX-212).

## The stack

The dashboard is a Flask API (`chela/dashboard/app.py`) serving one page,
`chela/dashboard/templates/index.html`. Two kinds of code run in it:

| | Where | Built? |
|---|---|---|
| **Legacy** | `chela/dashboard/static/js/*.js`, plain ES modules, booted by `src/boot.ts` | Vite, bundled with React (CMX-215) |
| **React** | `chela/dashboard/frontend/src/`, TypeScript + React 19 | Vite, into `static/app/` |

Both reach the browser as ONE bundle: the build's entry `src/main.tsx` imports the islands,
then `src/boot.ts` (which imports the legacy graph), so a legacy module can `import` a `.ts`
one directly. Legacy imports are extensionless (`'./util'`, not `'./util.js'`), so a module
converted to TypeScript renames no importer.

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
bundle** (what Flask serves), so they see a legacy edit only after the rebuild; the jsdom
suites (`tests/js_helpers/dashboard_dom.mjs`) boot `src/main.tsx`'s source, compiled by Vitest.

The CI gates, each also run inside pytest by `tests/test_frontend.py`: `pnpm typecheck`
(strict `tsc`), `pnpm lint` (typescript-eslint strict + react-hooks), `pnpm lint:boundaries`
(dependency-cruiser module rules) and `pnpm test`.

### Module layout

`src/features/<name>/` holds a feature, `src/shared/<name>/` code several features use. Each
directory is a deep module: its root files (`index.ts`) are its entry points, and anything in
a subfolder (`lib/`, `tests/`) is private to it. Tests import the module's entry point, never
its internals. `pnpm lint:boundaries` enforces this (`.dependency-cruiser.cjs`).

## Islands

The page is still the legacy page. React takes it over one region at a time: an **island** is
a React root rendered into an element `index.html` already has. Legacy keeps owning
everything else and keeps working untouched.

How the page loads them: `app.py`'s `app_entry()` reads the build's
`static/app/manifest.json` once at import and finds the entry `src/main.tsx` compiled to
(`app/assets/main-<hash>.js`). `index()` renders it as the page's one versioned
`<script type="module">`. `main.tsx` imports `islands.tsx` **before** `boot.ts`, and
imports run in order, so the state bridge below exists before any legacy module publishes to
it. There is no unbundled fallback: a missing or unreadable manifest logs an error and the
page loads with no JS.

`src/islands.tsx` installs the bridge and mounts every island (`pnpm dev` loads it alone):

```tsx
mountIsland('version-island', VersionBadge, {});
```

`mountIsland(id, Component, props)` (`src/shared/islands`) renders `<Component {...props} />`
into `#id` and returns the React root, or `null` when the page has no such element.

The page's chrome (CMX-219) mounts from `src/chrome.tsx`, which has no side effect at import so
the jsdom suites can mount it on a page of their own: the safety float's pills, the sidebar's
New session, agent count, resources strip and gear menu, the phone "+", the agent detail, and
`Overlays` (one root for Settings, the ⌘K palette and the shortcuts sheet, the launch menu, the
send-message dialog, the approvals and access-request sheets and the update banner). An island that draws one control
inside a legacy row renders into a `<span class="island-slot">` (`display: contents`), so the
control lays out as the row's own child.

When a ported surface needs something only legacy knows (the Wall, the view switch, the
orchestrator slot), legacy hands it in at import, the way `work.js` hands `setWorkPoll` its poll:
`nav.js` calls `setLauncherHost`, `setAgentDetailHost` and `setPaletteHost`. A legacy surface
the island only opens (sharing the current pane) is looked up on `window.chela` at call time.

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

Islands are a migration path, not the end state: each island is its own root, so context and
portals do not cross between them. `FRONTEND_PORT_PLAN.md` §2.1 ends in one app shell.

## The state bridge

Legacy code owns the fetches until the component reading their result is ported. The bridge
(`src/shared/bridge`) is how a ported component reads what legacy fetched:

| Store | Published by legacy from | Value |
|---|---|---|
| `agents` | `util.js` `setAgentsCache()` | the `/api/agents` rows |
| `sidebarLayout` | the sidebar controller's layout (`features/sidebar/lib/layout.ts`) | `{order, custom, view, width, loaded}` (CMX-82, CMX-278) |
| `sidebar` | the sidebar controller's `renderSidebarAgents()` (and Alt held, the active row) | `SidebarState`: what the sidebar island draws (CMX-276) |
| `nav` | `nav.js` `renderNav()` / `selectView()`, `work.js` (the badges) | the nav rows, the active view, the Work badge counts (CMX-278) |
| `recent` | the sidebar controller (`/api/restore`, dismiss, Undo) | Recent sessions (CMX-278) |

A store is a value plus listeners (`createStore`: `get`, `set`, `subscribe`; `set` with the
same value notifies nobody, so always publish a NEW array or object, never one mutated in place). React reads it with `useStore(store)`, a one-line
`useSyncExternalStore`. That is React's own primitive for an external store: it adds no
dependency, and it renders a consistent value even during concurrent rendering. A library
(zustand) would add selectors and middleware nobody needs for two values; Redux is ruled out.

Since CMX-217 the page's state is `src/shared/store/`, one file per slice (`view.ts`, the
active view; `agents.ts`, the bridge's `agents` store; `work.ts`, the last `/api/dispatcher`
payload, which `src/features/work`'s one poll (a scheduler lane, CMX-233) publishes and the Work
view and New-task dialog read; they ask for a fresh poll with `refreshWork()`, CMX-231; `orchestrator.ts`, who holds the decisions
inbox, read and written through `src/shared/orchestrator`, CMX-228; `context.ts`, the last
`/api/agents/context` rows, which the gear menu's usage readouts and the agent detail read,
CMX-219). Legacy imports those objects through
`util.js`, whose `currentTab` and `_agentsCache` are a facade over them: the store is the
truth, the live bindings mirror it. `sidebarLayout` still goes through `window.chelaBridge`:

```js
setAgentsCache(rows);                     // util.js: writes the agents store
publish('sidebarLayout', layout);         // util.js: a no-op when the islands did not load
```

In React:

```tsx
const agents = useStore(installBridge(window).agents);   // the page's one bridge
```

To add a store: add it to `Bridge` and `createBridge()` in `src/shared/bridge/index.ts`, and
call `publish('<name>', value)` wherever legacy changes it.

### The sidebar island

The sidebar is `src/features/sidebar` (CMX-172: slices CMX-276, CMX-277, CMX-278). Its roots
draw into slots `index.html` keeps: the session list `#sidebar-agents`, the VIEW menu
`#sidebar-view-menu`, the nav rows `#side-nav`, Recent sessions `#side-recent-island` and the
width handle `#sidebar-resize`. Its group, row and VIEW menus are the kit's `Menu`.

Its state and commands are `controller.ts`, the module's second entry point (the pure
`model.ts` is the third): the `/api/agents` read the sidebar draws (its scheduler lane), the
per-device collapsed groups, the server layout and archive, Recent sessions, the collapsed rail
and phone drawer, the Alt fast switch, and the width. What it needs from the rest of the page
(a window's label, the wall, opening a pane, the launcher, the orchestrator slot) it reaches
through the host `nav.js` gives `configureSidebar()`, so it imports no legacy module; legacy
(`nav.js`, `terminals.js`, `launcher.js`, `work.js`) imports it. The islands' clicks reach it
through `window.chela` (`legacyActions`, looked up at call time).

Every root repaints synchronously (`flushSync`) on a publish of its stores, because legacy
code reads the rows straight back (Alt+N, jump to the orchestrator).
`tests/js_helpers/dashboard_dom.mjs` mounts the islands (from `src/`) for every suite that boots
the page, and returns the controller as `modules.sidebar` (`extraModules: [SIDEBAR]`); a suite
with its own jsdom boot calls `mountIslands(window)` before it imports `src/boot.ts`. Icons come
from `src/shared/icons`, a map from a meaning to a Lucide icon.

The desktop width (CMX-278) is `--sidebar-w` on the root element, set from the layout's `width`
(clamped to 264-480 px, default 288; `chela/sidebar_layout.py` clamps the same). The handle sets
it live while dragging and saves it on release. The phone drawer keeps its own 264 px.

### The pane headers

The Wall's tiles are still built by legacy `terminals.js`; their headers are React
(`src/features/panehead`, CMX-241, plan P26), the first Wall surface ported inside out. Every
header on the page is ONE root, portaled into a slot each tile's markup carries
(`<div class="island-slot" data-head-for="@N" data-head-wall="1">`, `display: contents`).
`terminals.js` calls `mountPaneHeads(scope)` right after inserting tiles, and BEFORE
Gridstack adopts them: Gridstack finds each tile's `.gs-grip` drag handle (the title) when it
inits. A slot that leaves the page is dropped on the next paint.

Paints are synchronous (`flushSync`), on a mount and on every publish of the stores a header
reads: `agents`, the share state, `orchestrator` and `pinnedPanes` (`shared/store/wall.ts`,
which the legacy grid code reads too). What a header asks of the Wall (minimize, drop a killed
tile, start a wire, leave a room, relabel the dock) goes through the host `terminals.js` hands
`configurePaneHeads()`. Three header elements are still filled by vanilla code, so they
render with no React children: the facepile `.gs-presence` and the Share row's
`.gs-share-count` (`collab/presence-owner.js`), and the room badge `.gs-room` (`wire.ts`).

The rest of a tile is React too (CMX-242, plan P27/P28/P30/P32), on the same pattern:

| Module | Draws | Mounted by terminals.js into |
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
reconcile it runs (drop and absorb panes) is still `terminals.js`'s `termTick`, handed in through
`configurePaneFrames()` with the grid it rebuilds.

The Wall's shared state is `shared/store/wall.ts`: `termMode`, `pinnedPanes`, `minimizedPanes`
and `wallLayout` (`{preset, locked, auto, focus}`; auto-arrange and Focus exclude each other in
the setters). The toolbar and the dock write it; `terminals.js` still owns the Gridstack grid
and applies each change (`_onLayoutChange`, minimize and restore). The footer reads the
`context` store, which the Wall's 4 s tick publishes.

### Markdown

`src/shared/markdown` renders the task briefs and judge reports (`markdownHtml`, `inlineHtml`).
It escapes every character of its source and links only http(s), mailto and relative URLs: a
`javascript:`/`data:` link renders as its text (CMX-228). React renders its output only through
`<Markdown>`, the app's one `dangerouslySetInnerHTML`.

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
3. For a new island: give it an element in `templates/index.html` and one `mountIsland` line
   in `src/islands.tsx`. Delete the legacy code it replaces in the same PR.
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

`pnpm dev` serves `frontend/index.html`, a page holding just the island slots, with hot
reload, and proxies `/api` to the dashboard at `CHELA_DEV_API` (default
`http://127.0.0.1:5001`, the dashboard's default port). To see an island on the real page,
`build` and reload the dashboard.

## How the judge grades TypeScript

The judge and `chela judge sweep` mutate `.ts`/`.tsx` like `.js`, line by line, plus some
TypeScript-only mutations (`chela/sweep.py`, CMX-173). A mutant that no longer parses with
the repo's own `typescript` is discarded as invalid, not counted as caught
(`chela/judge.py`, CMX-175). A mutated file selects the test files whose import graph reaches
it, transitive importers included (`chela/judge_select.py`). The Vitest suite runs inside
`uv run pytest` (`tests/test_frontend.py`), which is the judge's test command, so a
`*.test.tsx` that catches a mutant counts like any Python test. Spec files are never mutated.
For self-check experiments on `.tsx`, the `before` anchor is TypeScript source text.
