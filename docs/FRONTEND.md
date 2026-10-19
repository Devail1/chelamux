# The dashboard frontend

The authority on chela's frontend (the repo's `CLAUDE.md` says so). Where it disagrees with a
generic skill, including the vendored React skills in `.claude/skills/`, this file wins. The
plan for porting the rest of the dashboard is `docs/FRONTEND_PORT_PLAN.md` (CMX-212).

## The stack

The dashboard is a Flask API (`chela/dashboard/app.py`) serving one page,
`chela/dashboard/templates/index.html`. Two kinds of code run in it:

| | Where | Built? |
|---|---|---|
| **Legacy** | `chela/dashboard/static/js/*.js`, plain ES modules, entry `main.js` | No. Served as written |
| **React** | `chela/dashboard/frontend/src/`, TypeScript + React 19 | Vite, into `static/app/` |

The React side is a Vite single-page build, **not** Next.js: no server components, no
`"use client"`, no file routing, no `next/*`. Tests are Vitest + React Testing Library in
jsdom. The toolchain is dev-only and pinned exactly (`frontend/package.json`); users never
need Node, because the build output in `chela/dashboard/static/app/` is **committed**
(CMX-177). `tests/test_frontend_build.py` fails while it differs from a fresh build of `src/`,
so after any change under `src/`:

```bash
pnpm -C chela/dashboard/frontend build    # then commit static/app/ with your change
```

The bundle's name is a content hash, so a rebuild renames it: update `BUNDLE` in
`tests/test_frontend_build.py` to the new name.

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

How the page loads them: `app.py`'s `island_entry()` reads the build's
`static/app/manifest.json` once at import and finds the entry `src/main.tsx` compiled to
(`app/assets/main-<hash>.js`). `index()` renders it as a versioned
`<script type="module">` **before** legacy `main.js`. Module scripts run in document order,
so the state bridge below exists before any legacy module publishes to it. A missing or
unreadable manifest logs a warning and drops the islands; the legacy page still loads.

`src/main.tsx` installs the bridge and mounts every island:

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
same value notifies nobody). React reads it with `useStore(store)`, a one-line
`useSyncExternalStore`. That is React's own primitive for an external store: it adds no
dependency, and it renders a consistent value even during concurrent rendering. A library
(zustand) would add selectors and middleware nobody needs for two values; Redux is ruled out.

Legacy is not in the Vite graph yet (CMX-212 P01), so it reaches the stores through
`window.chelaBridge`:

```js
publish('agents', rows);                  // util.js: a no-op when the islands did not load
window.chelaBridge?.agents.get();         // read
```

In React:

```tsx
const agents = useStore(installBridge(window).agents);   // the page's one bridge
```

To add a store: add it to `Bridge` and `createBridge()` in `src/shared/bridge/index.ts`, and
call `publish('<name>', value)` wherever legacy changes it.

## Adding a component, with its test

1. Create `src/features/<name>/lib/<Name>.tsx` and export it from
   `src/features/<name>/index.ts`.
2. Test it in `src/features/<name>/tests/<Name>.test.tsx` with React Testing Library,
   importing `../index.ts`. Stub the network with `vi.stubGlobal('fetch', …)`. Cover every
   branch, including what a failed read renders: unknown must never render as a value.
3. For a new island: give it an element in `templates/index.html` and one `mountIsland` line
   in `src/main.tsx`. Delete the legacy code it replaces in the same PR.
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
