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

The bundle's name is a content hash, so a rebuild renames it: update `BUNDLE` in
`tests/test_frontend_build.py` to the new name.

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

Islands are a migration path, not the end state: each island is its own root, so context and
portals do not cross between them. `FRONTEND_PORT_PLAN.md` §2.1 ends in one app shell.

## The state bridge

Legacy code owns the fetches until the component reading their result is ported. The bridge
(`src/shared/bridge`) is how a ported component reads what legacy fetched:

| Store | Published by legacy from | Value |
|---|---|---|
| `agents` | `util.js` `setAgentsCache()` | the `/api/agents` rows |
| `sidebarLayout` | `nav.js` `_setLayout()` / `_writeLayout()` | `{order, custom, view, loaded}` (CMX-82) |

A store is a value plus listeners (`createStore`: `get`, `set`, `subscribe`; `set` with the
same value notifies nobody, so always publish a NEW array or object, never one mutated in place). React reads it with `useStore(store)`, a one-line
`useSyncExternalStore`. That is React's own primitive for an external store: it adds no
dependency, and it renders a consistent value even during concurrent rendering. A library
(zustand) would add selectors and middleware nobody needs for two values; Redux is ruled out.

Since CMX-217 the page's state is `src/shared/store/`, one file per slice (`view.ts`, the
active view; `agents.ts`, the bridge's `agents` store). Legacy imports those objects through
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
