# Porting the whole dashboard to React + TypeScript (CMX-212)

Status: **plan**, not a design record. The orchestrator files one Linear issue per block in
§4. Measured on `dev` at `fcf1d45` (2026-10-17); `app.py` line numbers re-checked after CMX-206 merged.

Liav, 2026-10-17: the whole dashboard view moves to React + TypeScript (Vite) before the next
release; "the launch is less important than maintainability". CMX-171 (and its slices, ending
with CMX-178) builds the foundation and CMX-172 ports the sidebar. This plan covers
everything else, sliced so three agents can port it in parallel.

Contents:

0. [What this plan assumes from the foundation](#0-what-this-plan-assumes-from-the-foundation)
1. [Inventory](#1-inventory)
2. [Target architecture](#2-target-architecture)
3. [What stays vanilla, and where the React/iframe boundary is](#3-what-stays-vanilla-and-where-the-reactiframe-boundary-is)
4. [Slices](#4-slices)
5. [Interplay with queued UI tasks](#5-interplay-with-queued-ui-tasks)
6. [Definition of done](#6-definition-of-done)
7. [Estimate](#7-estimate)

---

## 0. What this plan assumes from the foundation

CMX-171 had not landed when this was written (no Vite config, no `docs/FRONTEND.md` on
`dev`), so the slices below depend on a contract rather than on files. If the foundation
chose differently, `docs/FRONTEND.md` wins (the repo's `CLAUDE.md` says so) and a slice
adapts its paths, not its scope.

| Assumed after CMX-178 | Used by |
|---|---|
| A Vite + React + TypeScript project whose build output Flask serves (hashed assets, read through a manifest by `index()` in `chela/dashboard/app.py:197-208`) | every slice |
| Vitest + React Testing Library running inside `uv run pytest` the same way `tests/test_js_suites.py` runs `*.test.mjs` today (discovered, loud skip, `CHELA_REQUIRE_JS_TESTS=1` fails closed) | every slice |
| An island mount: a React root rendered into an element of the current `index.html` | P05 onward |
| A state bridge between the legacy modules and the islands | P03 extends it |
| The judge sweeps and self-checks `.ts`/`.tsx` (CMX-173, CMX-174, CMX-175) | every slice |

Paths below use `chela/dashboard/frontend/` for TypeScript sources as a placeholder. Read it
as "wherever CMX-171 put them".

**Counting the 600.** The cap is on **added lines of non-test code** (tests and deletions of
legacy code do not count, Liav 10-17). `task-finished` measures differently (added + deleted,
everything but docs and media, `chela/sizegate.py:32-51`), so every port slice hands in with
`--oversize-reason "CMX-212 port slice, ≤600 per plan"`. To check a slice against this plan:

```bash
git diff --numstat origin/dev...HEAD \
  | awk '$3 !~ /^(tests\/|docs\/)|\.test\.(mjs|ts|tsx)$|\.md$/ { a += $1 } END { print a }'
```

**The 600 cap is an experiment (Liav, 10-17).** After the first three port slices merge,
compare their rounds-to-clean with the 14-day baseline: 66% clean on round 1, from
`~/.chela/judge-heldout/*.jsonl`. If they need clearly more rounds, shrink the remaining
slices back toward 300 (each block in §4 names where it splits). Every other `risk:high` task
stays at ≤300.

---

## 1. Inventory

**Size today.** 43 modules, 15,186 lines under `chela/dashboard/static/js/`, plus
`static/style.css` (4,888) and `templates/index.html` (879). 124 `tests/*.test.mjs` suites
run under `node --test`, plus 13 Playwright suites in `tests/browser/`.

**How it boots.** `index.html` loads one module, `js/main.js` (`templates/index.html:877`),
after three inline globals (`CHELA_ASSET_VERSION`, `TERMINALS_ENABLED`,
`WALL_TILE_DISPATCHED`, lines 869-871) and the vendored Gridstack bundle (line 872).
`main.js` renders the nav, selects the first view, starts a 30 s `setInterval(refresh)`, the
dispatcher poll and SSE, all at import time (`main.js:74-83`).

**How modules talk.** Three ways, and the port has to replace all three:

1. ES imports, with cycles (`main` ⇄ `nav`, `main` ⇄ `agents`, `nav` ⇄ `terminals`,
   `nav` ⇄ `launcher`, `work` ⇄ `kanban` ⇄ `dispatcher`).
2. Live `let` exports from `util.js` that 25 modules read: `currentTab` and `_agentsCache`,
   with setters (`util.js:78-80`, `304-306`).
3. A global `window.chela` namespace of **162 functions** across 17 modules, called from
   **111 inline `on*=` attributes** in `index.html` and from `onclick="chela.…"` inside
   JavaScript template strings (73 lines across 8 modules). Some modules reach each other
   only through it (`decisions.js:384` calls `window.chela.openTaskModal`).

**Columns:** *Area* is the slice group in §4. *Side effects* lists what the module does to
the page beyond returning values: element ids it reads or writes, global listeners, timers,
storage keys, `window.chela` entries, endpoints. "Pure" means none.

### Shell, data layer, shared helpers

| Module | Lines | Area | Imports | Imported by | Side effects |
|---|---|---|---|---|---|
| `main.js` | 86 | Data layer (P03) | accessreq, agents, approvals, decisions, header, launcher, nav, resources, sse, statushealth, util, version, viewreg, views, work, `newtask` (side-effect import, line 8) | agents, nav | Boot at import (`main.js:74-83`); `setInterval(refresh, 30 s)`; one shared `/api/agents/context` read per tick for three readers (`main.js:37`, the CMX-166 boot contract) |
| `sse.js` | 201 | Data layer (P03) | approvals, decisions, orchestrator, runtoast, terminals, util, work | main | One `EventSource` on `/api/events` (`sse.js:152-195`), events `hello`, `windows`, `runs`, `log`, `term-ready`, `orchestrator`, `overrides`; creates `#run-toast-stack`; reads `chela_mute_run_toasts`; reaches into terminals internals (`_absorbFreshTerminals`, `_swapToFrame`, `_termReady`) |
| `util.js` | 313 | Data layer + primitives (P03, P04, P35) | none | 25 modules | Live `currentTab`/`_agentsCache`; reads the three inline globals (`util.js:87`, `92`, `238`); `api()` with `BASE_PATH` (`233-249`); tab-title and favicon badge (`187-231`); a global `document` click that closes `.menu` (`262`); the inline Lucide map `_LUCIDE` + `lucideIcon()` (`7-73`); `window.chela` {closeModal, toggleMenu} |
| `views.js` | 69 | Shell (P34) | nav, terminals, work | main, nav | The view registry: Wall, Work, agent-detail (virtual) |
| `viewreg.js` | 58 | Shell (P34) | none | main, nav | Pure registry helpers |
| `header.js` | 35 | Menus (P20) | util | main, nav | Writes `#hdr-agents`, `#hdr-next`, `#hdr-schedules`, `#hdr-updated` inside `#primary-menu`; `/api/summary` |
| `orchestrator.js` | 80 | Decisions (P07) | util | decisions, nav, sse, terminals | Module state + change listeners; `/api/orchestrator/{status,subscribe,release}` |
| `windowid.js` | 31 | Wall models (P22) | none | nav, terminals | Pure |

### Safety float, header readouts, update flow

| Module | Lines | Area | Imports | Imported by | Side effects |
|---|---|---|---|---|---|
| `approvals.js` | 246 | P05 | util | main, sse | `#approvals-backdrop`; capture-phase `keydown` while open (`approvals.js:212`); `chela_approval_notify`; `window.chela` ×3; `/api/overrides`, `/api/override/*` |
| `accessreq.js` | 151 | P05 | util | main | `#access-req-backdrop`, `#btn-access-req`; capture-phase `keydown` (`accessreq.js:133`); `window.chela` ×2; `/api/share-requests` |
| `statushealth.js` | 43 | P06 | util | main | `#status-health-warn`; `/api/agents/status_health` |
| `resources.js` | 67 | P06 | resourcesmodel, util | main | `#resources-strip`; `/api/resources` |
| `resourcesmodel.js` | 39 | P06 | none | concurrencymodel, resources | Pure |
| `runtoast.js` | 38 | P06 | none | sse | Pure (the toast DOM lives in `sse.js:106-132`) |
| `version.js` | 159 | P06 | updateflow, util | main | Creates `#update-banner` on demand (`version.js:34-38`); `/api/version`, `/api/update/*` |
| `updateflow.js` | 78 | P06 | util | nav, version | Polls `/api/update/status` after an apply |

### Decisions inbox

| Module | Lines | Area | Imports | Imported by | Side effects |
|---|---|---|---|---|---|
| `decisions.js` | 546 | P08 | decisionsmodel, feedmodel, orchestrator, util | main, sse | `#decisions-*` (7 ids); a global `keydown` (`decisions.js:530`); `chela.decisions.lastSeen`; `window.chela` ×5; `/api/log`, `/api/agents`, `/api/dispatcher` |
| `decisionsmodel.js` | 204 | P07 | none | decisions | Pure |
| `feedmodel.js` | 375 | P07 | none | decisions | Pure |

### Work view (board, runs, schedules, new task, task modal)

| Module | Lines | Area | Imports | Imported by | Side effects |
|---|---|---|---|---|---|
| `work.js` | 230 | P14 | dispatcher, kanban, schedules, util | dispatcher, kanban, main, newtask, sse, views | The app's ONE `/api/dispatcher` poll (`setInterval`, 30 s, `work.js:199`); `#dispatch-hold-*`, `#new-task-btn`, sidebar badges `#side-runs-count`/`#side-pr-count`; `chela_work_segment`; `window.chela` ×2 |
| `kanban.js` | 1,007 | P12, P13 | dispatcher, kanbanhiddenmodel, kanbanlanemodel, kanbanlinearmodel, knowledge, runstate, taskmodal, taskmodalmodel, util, work | work | `#kanban-*`, `#work-board`; `chela_kanban_collapsed`, `chela_kanban_mlayout`; `matchMedia` (`kanban.js:798`); `window.chela` ×17; `/api/dispatcher/{backlog/promote,merge-all,runs/*}` |
| `kanbanhiddenmodel.js` | 53 | P12 | none | kanban | Pure; owns the `chela_kanban_colprefs` key name |
| `kanbanlanemodel.js` | 77 | P12 | none | kanban | Pure |
| `kanbanlinearmodel.js` | 129 | P12 | none | kanban | Pure |
| `dispatcher.js` | 352 | P10 | launcher, runstate, taskmodalmodel, util, work | kanban, taskmodal, work | `#dispatcher-*`, `#init-*`; `window.chela` ×4; `/api/dispatcher/init` |
| `runstate.js` | 97 | P10 | none | dispatcher, kanban, taskmodal | Pure |
| `taskmodal.js` | 302 | P09 | dispatcher, knowledge, runstate, taskmodalmodel, util | kanban | `#modal-task`; capture-phase `keydown` (`taskmodal.js:284`); `window.chela` ×5 |
| `taskmodalmodel.js` | 126 | P09 | knowledge | dispatcher, kanban, taskmodal | Pure |
| `knowledge.js` | 83 | P09 | util | kanban, taskmodal, taskmodalmodel | Markdown to HTML string (see the security note in P09) |
| `newtask.js` | 124 | P11 | util, work | main (side-effect import) | `#newtask-*`; `window.chela` ×3; `POST /api/dispatcher/linear/issue` |
| `schedules.js` | 132 | P11 | util | nav, work | `#sched-*`, `#cron-*`; `window.chela` ×5; `/api/schedules`, `/api/cron` |

### Settings, usage and cost

| Module | Lines | Area | Imports | Imported by | Side effects |
|---|---|---|---|---|---|
| `nav.js` §Settings | 1,343 (`nav.js:1795-3137`) | P15-P18 | (part of nav.js) | | `#settings-*`, `#drawer-*`, 20+ row ids; `window.addEventListener('resize')` while open (`nav.js:2003`); theme and terminal-font keys (`chela_theme`, `chela_term_*`); pushes theme/font into every pane iframe (`nav.js:3071-3106`, `3129-3137`); `/api/config*`, `/api/settings`, `/api/update/apply` |
| `usage.js` | 234 | P18 | cost, util | nav | `#usage-*`, `#cost-pane`; `chela_cost_view`, `chela_usage_window`; `window.chela` ×2; `/api/usage` |
| `cost.js` | 140 | P18 | util | usage | `#cost-table`; `chela_cost_window`; `window.chela` ×1; `/api/cost` |
| `concurrencymodel.js` | 24 | P17 | resourcesmodel | nav | Pure |

The Settings tabs inside `renderSettings()` (`nav.js:1992-2365`): General `2015-2125`,
Timing `2126-2142`, Dispatch `2143-2170`, Notifications `2171-2205`, Cost `2206-2258`,
Appearance `2259-2304`, Collaboration `2305-2365`.

### Palette, menus, launcher, agent detail

| Module | Lines | Area | Imports | Imported by | Side effects |
|---|---|---|---|---|---|
| `nav.js` §Palette + shortcuts | 297 (`nav.js:3355-3651`) | P19 | | | `#palette*`, `#shortcuts-overlay`; a global `keydown` for ⌘K / Ctrl+K (`nav.js:3592`) |
| `nav.js` §Popovers | 217 (`nav.js:3138-3354`) | P04 (placement), P20 | | | `#new-menu`, `#primary-menu`; one-shot `document` clicks (`3216`, `3246`); `resize` (`3190`); touch tooltips (`3339-3352`) |
| `launcher.js` | 203 | P20 | nav, terminals, util | dispatcher, main, nav | `#btn-new`, `#fav-add-menu`, `#new-menu-launch`; `window.chela` ×7; `/api/launcher*`, `/api/agents/spawn` |
| `nav.js` §Agent detail | 82 (`nav.js:1713-1794`) | P21 | | | `#agent-detail` |
| `agents.js` | 190 | P20 (`checkContext`), P21 (actions) | main, util | main, nav | `#modal-msg*`; `window.chela` ×6; `/api/agents/{msg,restart,start,stop,trigger,context}` |

### Sidebar (CMX-172, not sliced here)

| Module | Lines | Notes |
|---|---|---|
| `nav.js` §Sidebar | ≈1,470 (`nav.js:41-135`, `188-1712`) | Toggle, nav rows, Alt fast switch, rows, per-device state, server layout, archive, group and VIEW menus, recent sessions, dismiss |
| `sidebarmodel.js` | 561 | Pure; imports `wallmodel` |
| `index.html` | `42-185` | The `<aside class="sidebar">` |

That is about 2,000 lines of legacy for CMX-172. If it has not been sliced yet, it is three
600-line slices by itself (rows + model; menus + archive; recent sessions + nav rows).

### Wall

| Module / region | Lines | Slice | Side effects |
|---|---|---|---|
| `terminals.js` | 4,081 | P24-P33 | 19 fixed ids plus every tile; 5 `window` and 10 `document` listeners; 3 `setInterval` (ready polls `terminals.js:186`, focus poll `2194`, term tick `2250`, 4 s); 15 `pc_*` storage keys; `window.chela` ×33; Gridstack; iframes; a dynamic `import()` of `collab/presence-owner.js` (`terminals.js:568`) |
| `wallmodel.js` | 280 | P22 | Pure |
| `wire.js` | 102 | P22 | Pure (the room gesture's geometry) |
| `diffpanel.js` | 127 | P23 | `#modal-diff`; capture-phase `keydown` (`diffpanel.js:105`); `window.chela` ×2; loaded only as a side-effect import from `terminals.js:10` |
| `diffpanelmodel.js` | 56 | P23 | Pure |

`terminals.js` by region (its own section headers):

| Lines | Region | Slice |
|---|---|---|
| 1-111 | State, storage keys, recap toggle, `resize` re-fit | P33 |
| 112-381 | Readiness placeholders + polls, paste, scroll | P31 |
| 382-496 | Display labels, rename | P26 |
| 497-958 | Collab sharing state, owner presence, active-shares pill + sheet | P25 |
| 959-1261 | Share dialog and toast (P24); `chelaPresence` (P25); facepile, share/orchestrator buttons (P26) | P24-P26 |
| 1262-1486 | `paneHead`, overflow menu, kill confirm | P26 |
| 1487-1958 | Phone full-pane toggle (P26); dock, pins, lazy dock, taskbar order and drag (P28); status dots (P26); context (P27) | P26-P28 |
| 1959-2018 | Keybar state, last-viewed pane | P29, P33 |
| 2019-2132 | `renderTerminals` | P33 |
| 2133-2473 | Focus ring, background-tab teardown, ignore-size watch, `termTick` | P31 |
| 2474-2598 | Alt fast switch, iframe shortcuts | P31 |
| 2599-2825 | Tile HTML (P33); recap line, action bar, context bar, copy window id (P27) | P27, P33 |
| 2826-3027 | `buildWall`, `addWallTiles`, fill | P33 |
| 3028-3364 | Layout toolbar and toggles (P30, to 3282); swap-on-drop (P33) | P30, P33 |
| 3365-3710 | The wire (rooms) | P32 |
| 3711-3805 | Focus layout | P33 |
| 3806-3942 | Phone session pills | CMX-202 deletes it (§5) |
| 3943-4075 | Phone keybar v2 | P29 |

### Markup and style

`templates/index.html` (879 lines) by owner: sidebar `42-185` (CMX-172); agent detail
`190-194`; Work `195-331`; Wall `334-482`; phone FABs `484-503`; safety float `504-547`;
palette `548-558`; shortcuts `559-600`; settings `604-641`; new menu `642-665`; primary menu
`666-720`; decisions `721-757`; favorites picker `758-760`; schedule modal `761-783`; new task
`784-813`; send message `814-825`; task modal `826-837`; diff modal `839-849`; init repo
`851-868`.

`static/style.css` (4,888 lines): tokens in `:root` (`style.css:34-97`), seven more palettes as
`body[data-theme="…"]` (`104-146`), then feature sections. There is no light theme today: the
"light/dark tokens" the port keeps are these eight palettes. `tests/test_on_accent_contrast.py`
and `tests/btn_danger_css.test.mjs` hold every palette to WCAG AA. 27 `@media` rules, 3
`@container` (the Settings modal, `style.css:4006`, `4089`).

---

## 2. Target architecture

### 2.1 One app shell at the end, islands on the way

**End state: one React root.** `index.html` keeps its `<head>` (meta, the pre-paint sidebar
script at `templates/index.html:16`, a JSON boot block for what lines 869-871 inject today)
and its body becomes `<div id="root">`. `<App>` renders the sidebar, the canvas, the safety
float and every overlay.

**Migration path: islands (CMX-178), then the shell last (P34).** Islands forever was
rejected: every island is a separate root, so providers, context and portals do not cross
between them, modals stack under each other's `z-index`, and cross-root state needs a bridge
that becomes permanent. Shell-first was rejected too: legacy modules find their containers by
id at import time (`terminals.js:95`, `nav.js:272-281`), so React would have to render
legacy-owned markup before legacy runs, which inverts ownership and couples every slice to
the shell. Islands-first keeps legacy in charge of what it still owns and lets each slice
take one region.

Two enablers make islands cheap:

- **P01: the legacy modules join the Vite graph.** Then a legacy module can `import` a ported
  `.ts` module directly, and the `window.chela` bridge shrinks instead of growing.
- **P02: the dashboard's `node --test` suites run under Vitest.** Node 20 (the pinned
  version) cannot import `.ts`, so without P02 every legacy module that imports a ported one
  breaks its own tests. With P02, a pure model converts to TypeScript in place, and legacy
  importers keep working.

The conversion is mechanical: 124 files import only `test`, `before`, `beforeEach`, `after`
and `describe` from `node:test` (one also uses `mock.timers`, one `t.after`), and `node:assert`
works unchanged under Vitest.

### 2.2 Routing

No router library. Three views (Wall, Work, the virtual agent drill-in, `views.js:35-69`)
with no nested routes do not need one. The active view becomes a `view` field in the store,
replacing `util.js`'s `currentTab`. Two rules carry over:

- **The Wall never unmounts.** It holds live ttyd iframes; unmounting reloads every pane.
  Switching to Work hides it (`hidden`), exactly as the `.panel.active` toggle does today
  (`nav.js:143-145`), and the view's `exit` hook still stops the 4 s tick (`views.js:45-46`).
- **Per-view enter/exit/tick stay declarative.** The registry (`views.js`) becomes a typed
  array the shell maps over; the scheduler calls the active view's `tick`.

Deep links (`#/work`, `#/agent/@N`) are a feature, not a port, so they are a follow-up.

### 2.3 Data layer: one store, one scheduler, one event stream

```
           +-------------------- store (useSyncExternalStore) --------------------+
 SSE  ---> | agents · context · dispatcher · decisions · approvals · accessreq ·   | ---> hooks:
/api/events| resources · statusHealth · summary · launcher · version · orchestrator | useAgents(),
           | view · wall (mode, preset, lock, auto, focus, minimized, pinned, …)  |  useRun(id) …
 polls --->+----------------------------------------------------------------------+
 (scheduler)          ^ legacy modules read the same store through util.js (P03)
```

- **Store.** A small typed external store (`subscribe`/`getSnapshot`) read with React's
  built-in `useSyncExternalStore` and selector hooks, one file per slice of state
  (`store/agents.ts`, `store/dispatcher.ts`, …) so parallel slices add files instead of
  editing a shared one. No Redux, Zustand or TanStack Query: the cadence rules below are
  custom and already tested, and a query cache would either be bypassed or would change
  them. If CMX-178's bridge already picked a library, keep it.
- **Scheduler.** Reproduces `refresh()` exactly (`main.js:32-72`): independent lanes under
  `Promise.allSettled`, ONE `/api/agents/context` read shared by its three readers, the active
  view's tick starts as soon as the pane list is known, and the update check waits for the
  first view paint on boot. Lanes register from their feature's own module (Vite
  `import.meta.glob`), the same way views register today, so a new lane is a new file.
- **Events.** One `EventSource` (`sse.js:152-195`) becomes a typed dispatcher; each event
  name maps to store actions owned by the feature (`windows` to agents, `runs` to dispatcher
  and run toasts, `log` to decisions, `term-ready` to the wall, `orchestrator`, `overrides`).
  That removes `sse.js`'s reach into `terminals.js` internals.
- **Legacy compatibility.** Until the last consumer is ported, `util.js` keeps exporting
  `currentTab` and `_agentsCache` as a facade fed from the store, so P03 does not have to
  edit 25 importers.

### 2.4 Styling

**Keep `style.css` global, keep its class names, add CSS Modules only for new
component-private styles. No Tailwind.**

- **Why keep class names.** They are a tested contract: 34 node suites that read
  `style.css` and 13 Playwright suites select by them, and the contrast tests parse the palettes.
  A React component that renders `className="gs-head"` inherits years of tuned rules for
  free.
- **Why not CSS Modules for everything.** Renaming every class means rewriting those tests
  and moving ~4,000 lines of CSS, which counts against the 600 cap for zero user value.
- **Why not Tailwind.** It would rewrite the look (the opposite of "keep the existing look"),
  add a build dependency, and drop the comments that record why each rule exists (the CMX
  numbers in `style.css`).
- **Behaviour from Radix Primitives, look from `style.css`** (Liav, 10-19). Hand-rolled
  dialogs, menus, popovers and form controls drift apart surface by surface. Radix Primitives
  (`@radix-ui/react-*`) are unstyled and accessible (focus management, keyboard, ARIA,
  collision-aware placement), so they bring the behaviour without touching the look: our kit
  in `ui/` wraps them and applies the existing class names and tokens. Not shadcn/ui and not
  Radix Themes: both are styled systems (shadcn on Tailwind) and would rewrite the look.
- **Tokens.** `:root` + the seven `body[data-theme]` palettes (`style.css:34-146`) move
  verbatim into `tokens.css`, imported first by the entry. The theme switch keeps writing
  `body[data-theme]` and `chela_theme` (the iframe theme shim reads that key,
  `app.py:954`).
- **Dead CSS** is deleted by the slice that deletes the last markup using it, and P35 sweeps
  what is left.

### 2.5 Icons

`lucide-react`, behind one semantic map (`icons.ts`: `share`, `wall`, `kill`, … to Lucide
components), so a meaning is chosen once. It replaces `util.js`'s hand-vendored `_LUCIDE`
paths (`util.js:7-73`) once the last legacy caller of `lucideIcon()` is gone (P35). Icons are
`aria-hidden` unless they are the only label, in which case the button carries `aria-label`.
See §5 for CMX-199/200.

### 2.6 Accessibility

The port keeps what is there, and makes it uniform:

- **One `<Dialog>` primitive (P04)** on Radix Dialog: it gives the focus trap, `Escape`, an
  inert background and focus return to the opener; the wrapper requires a label. It replaces six separate capture-phase `keydown` handlers
  (`accessreq.js:133`, `approvals.js:212`, `diffpanel.js:105`, `taskmodal.js:284`,
  `terminals.js:906`, `decisions.js:530`) and the `.modal-overlay.active` toggles
  (`util.js:251-252`), keeping their classes for the look.
- **Status by shape, not hue** (`style.css:1151`), **targets ≥24×24** (WCAG 2.5.8, CMX-130)
  and **on-accent contrast** stay as they are; the components reuse the same classes.
- Real `<button>`s instead of `onclick` on `<div class="popover-item">`
  (`templates/index.html:652-663`, `677-697`), so menus become keyboard-reachable.

### 2.7 Phone layout

- **Pane-sized boxes use `@container`, not `@media`.** A wall tile, the task modal and the
  Settings dialog answer to their own width wherever they are drawn; the Settings modal
  already does this (`style.css:4006`). Each port slice converts the `@media (max-width:
  768px)` rules that style the inside of its own box.
- **Viewport questions stay `@media`.** The sidebar drawer, the FABs, the safety float and
  where a fixed dialog sits are window questions (`style.css:4073-4074` says so). In
  TypeScript they become one `useMediaQuery` hook in place of the four `matchMedia` calls
  (`kanban.js:798`, `nav.js:53`, `terminals.js:2032`, `3819`).

---

## 3. What stays vanilla, and where the React/iframe boundary is

**Stays vanilla, with reasons:**

| What | Where | Why |
|---|---|---|
| The ttyd page shims: paste, paste-key, contextmenu, OSC 52 clipboard, palette key, scroll, scrollbar CSS, touch CSS, font CSS + prefs, theme | `app.py:599-1079`, injected by `term_http` at `app.py:1158-1165` | They run inside ttyd's page, not the dashboard. They are strings Flask splices into a third-party document. |
| `static/term-reconnect.js`, `static/term-upload.js`, `static/term-touch.css` | injected at `app.py:1094-1105` | Same: they live in the ttyd page. |
| `static/collab/` (`e2e.js`, `presence-core.js`, `presence-shim.js`, `presence-owner.js`, `fit.js`) | | The E2E crypto is byte-identical with `chela/e2e.py` and the joiner page and is tested as such. `presence-shim.js` runs in the iframe. `presence-owner.js` runs in the dashboard but stays a plain ES module that the React share code `import()`s with a `.d.ts` beside it. |
| Gridstack | `static/vendor/gridstack-all.js` | Kept as the layout engine, wrapped by one hook (P33). |
| The terminal iframe | `_termFrameSrc` (`terminals.js:515`) | Stays an `<iframe src="/term/<wid>/…">`. |

**The boundary, exactly.** React renders the `<iframe>` element and owns its `src`; it never
renders anything inside it. Everything that crosses is listed here, and P31/P33/P34 each carry
a test that names it:

| Direction | What crosses | Today |
|---|---|---|
| iframe → parent | `window.parent.chela.openPalette()` and `.toggleSettings()` (⌘K and Ctrl+, inside a pane) | `app.py:777-790` |
| iframe → parent | `postMessage` of normalized pointer coordinates; the parent answers with peers | `collab/presence-shim.js:24-27`, `presence-owner.js` |
| iframe reads parent | `frameElement`'s `data-suspended-src` attribute and `src === 'about:blank'` (suspended by background-tab teardown) | `term-reconnect.js:42-47`, set at `terminals.js:2290-2333` |
| iframe reads storage | `chela_theme`, `chela_term_latin`, `chela_term_font`, `chela_term_fontsize` | `app.py:954`, `1032-1034` |
| parent → iframe | `contentWindow.term` (font, theme, refit, focus) | `nav.js:3074`, `3132`; `terminals.js:930`, `2237`, `4057` |
| parent → iframe | `contentDocument` (focus tracking, shortcut wiring) | `terminals.js:2162`, `2512` |
| parent → iframe | `contentWindow.location.reload()` | `terminals.js:918` |

So after P34 `window.chela` still exists, holding exactly `openPalette` and `toggleSettings`,
registered by one module (`iframeBridge.ts`) with a test that fails if either is missing or if
anything else is added.

**One dead entry to drop, not port.** `terminals.js:1133` defines `chelaPresence` for
`collab/presence.js:124-141` (`window.parent.chelaPresence`), but nothing assigns it to
`window` (it is not in the `window.chela` list at `terminals.js:4081` either), and `term_http`
injects `presence-shim.js`, not `presence.js` (`app.py:1108-1117`). P25 confirms that and
deletes it.

---

## 4. Slices

**Rules for every slice.**

1. **Vertical.** A slice ports one surface (its view, its pure models, its markup and its
   tests) and ends with the legacy code for it **deleted**: the module or region, its
   `index.html` markup, its `window.chela` entries and its inline handlers. No permanent
   dual implementation, and no flag that keeps one alive.
2. **≤600 added lines of non-test code**, measured with the command in §0. Each block names
   where it splits if the cap shrinks to 300.
3. **Calls between ported and legacy code are plain imports** (after P01/P02). A legacy caller
   of a ported function imports it from the `.ts` module. `window.chela` is used only by
   markup that is still legacy, and the slice that ports the markup removes the entry.
4. **Tests.** Every `*.test.mjs` the slice's code made pass is ported to Vitest (+ RTL for
   rendering) or retired with a one-line reason in the PR body. Tests that grep legacy source
   are rewritten against the component, not deleted. The Playwright suites in `tests/browser/`
   stay green on every slice; when one breaks because markup moved, the slice fixes the
   suite's selector, never the assertion.
5. **Shared files** (`index.html`, `style.css`, `util.js`, `terminals.js`, `nav.js`) are edited
   only inside the slice's own regions as listed. "Parallel" below means the slices'
   regions in every shared file are disjoint and not adjacent, so git merges them without a
   conflict. Agents merge `origin/dev` before `task-finished` as usual.
6. Risk uses the tracker's labels (`docs/RISK_LEVELS.md`): `high`, `normal`, `low`.
7. **Interactive UI uses the P04 kit** (`Dialog`, `Popover`, `Menu`, `Tooltip`, `Tabs`, `Toast`,
   `FieldRow`, `TextInput`, `NumberInput`, `Select`, `Toggle`, `Button`, `Badge`,
   `SectionHeader`), built on Radix Primitives (§2.4). A slice that needs a control the kit
   lacks adds it to `ui/` on the matching Radix primitive, not inline. A ported component renders no raw `<input>`,
   `<select>`, `<textarea>` or `<button>` outside `ui/`, so every label, control and unit
   lines up the same way on every surface. P35 enforces it.

**Order and lanes.** Three agent slots. The Wall lane is serial, because those slices all
edit `terminals.js`; the other two lanes take everything else.

```
Phase 0  P01 ─┬─ P03 ── P04
         P02 ─┘
Phase 1  lane A: P05 · P06 · P15 · P16 · P17 · P18 · P19 · P20 · P21
         lane B: P07 · P08 · P09 · P10 · P11 · P12 · P13 · P14
         lane W: P22 · P23 · P29 · P24 · P25 · P26 · P27 · {P28 ∥ P30 ∥ P32} · P31 · P33
Phase 2  P34 ── P35
```

Lane A and lane B can each borrow a slot from the other; the Wall lane is the critical path
and should never wait for a slot. Within lane W, the slices marked "parallel with" in their
block may run beside another W slice in a spare slot.

### Phase 0: enablers

```text
P01 · CMX-212 port: the legacy dashboard JS joins the Vite bundle
Scope: index.html's module script (templates/index.html:877) becomes the Vite entry's import of
  static/js/main.js, so legacy modules are bundled and can import .ts. Flask serves the built
  entry via the manifest. The browser fixture and the jsdom bootstrap load what Flask serves.
  If CMX-178 already bundles main.js, this slice is only the fixture + test part.
Files: the Vite config; templates/index.html (lines 7-9, 869-877); chela/dashboard/app.py
  (index(), asset_path); tests/browser/fixture.mjs; tests/js_helpers/dashboard_dom.mjs;
  tests/test_asset_version.py; tests/test_dashboard_first_paint.py
Deletes: the raw, unbundled <script type="module" src=js/main.js> path
Blocked by: CMX-178
Tests: browser suites green against the built page; dashboard_first_paint (node + py);
  test_asset_version (hashed names still revalidate, CMX-426's stale-page reload still fires)
Risk: high (every page load). ≈150 lines. Parallel with: P02 (if the Vitest config is not in
  the same file as the Vite build config; otherwise P01 first)
```

```text
P02 · CMX-212 port: the dashboard's node suites run under Vitest
Scope: convert tests/*.test.mjs from node:test to Vitest (before→beforeAll, after→afterAll,
  mock.timers→vi.useFakeTimers; node:assert unchanged), one file per Vitest file, pool 'forks'
  so each suite gets a fresh module graph as `node --test` gives it today. tests/test_js_suites.py
  discovers and runs them through Vitest with the same fences (discovered-or-fail, loud skip,
  CHELA_REQUIRE_JS_TESTS=1 fails closed, CMX-151 containment: no process outlives the run).
  static/collab/*.test.mjs and tests/browser/ stay on node --test.
Files: tests/*.test.mjs, tests/js_helpers/*, tests/test_js_suites.py,
  tests/test_collected_js_suites.py, the Vitest config's include list, package.json scripts
Deletes: the node --test runner for tests/*.test.mjs
Blocked by: CMX-171 (Vitest present)
Tests: the same 124 suites, same assertions, all green; a fence that fails if Vitest finds zero
Risk: normal. ≈40 lines (the rest is test code). Parallel with: P01 (see P01)
```

```text
P03 · CMX-212 port: store, API client, SSE and refresh scheduler in TypeScript
Scope: store/ (one file per state slice), api.ts (api(), BASE_PATH, staticUrl, the boot JSON
  replacing the three inline globals), events.ts (the EventSource and its 7 events), scheduler.ts
  (refresh()'s lanes, the CMX-166 boot contract, feature-registered lanes and view ticks).
  sse.js's handlers move to their owners as store subscriptions. util.js keeps currentTab /
  _agentsCache / setters as a facade over the store. Also rewrites every legacy relative import
  to extensionless form ("./x" not "./x.js") so later model renames touch no importer; the
  rewrite is one sed and is re-run, not hand-merged, after merging dev.
Files: frontend store/, api.ts, events.ts, scheduler.ts; static/js/main.js, sse.js (deleted);
  util.js (api/BASE_PATH/state block, lines 75-92, 233-249, 304-306); import lines of every
  static/js module
Deletes: main.js, sse.js
Blocked by: P01, P02. Do not run while CMX-172 has an open PR touching nav.js's import block, or
  re-run the import rewrite after merging it.
Tests: wall_boot_fetches, decisions_sse, decisions_seed, orchestrator_seed, dashboard_default_view,
  update_banner_wiring, approvals_wiring, access_requests_wiring (all boot main.js through
  dashboard_dom), plus new Vitest units for the scheduler's lane rules and the event map
Risk: high (every poll and push). ≈500 lines. Runs alone. At 300: split store+api+events from
  the scheduler.
```

```text
P04 · CMX-212 port: UI kit on Radix Primitives (Dialog, Popover, Menu, Tooltip, Tabs, Toast,
  form controls) and lucide-react
Scope: ui/ wraps Radix Primitives with our class names and tokens (§2.4): Dialog (required
  label), Popover, Menu (DropdownMenu), Tooltip, Tabs, Toast, Select, Toggle (Switch); plus
  FieldRow, one grid label | control | unit with fixed tracks so every control in a section
  starts at the same x; TextInput, NumberInput (placeholder shows the default;
  disabled-by-env state), Button variants, Badge tones, SectionHeader. icons.ts semantic map
  over lucide-react, useMediaQuery. Legacy popovers keep nav.js placement, moved verbatim to a
  pure placePopover() in .ts, until their slices port them onto <Popover>.
Files: frontend ui/; nav.js 3138-3196 (moved to .ts); package.json (lucide-react,
  @radix-ui/react-* for the primitives above)
Deletes: nav.js's popover placement
Blocked by: P03
Tests: popover_placement → Vitest; RTL: Dialog needs a label, returns focus, closes on Escape;
  each wrapper renders our class names; two FieldRows with very different label lengths put
  their controls in the same column (negative control: drop the fixed track, RED)
Risk: normal. ≈500 lines (Radix replaces hand-rolled focus and placement code). At 300:
  overlays (Dialog, Popover, Menu, Tooltip, Toast), then form controls and FieldRow.
```

### Phase 1, lane A: safety float, readouts, Settings, menus

```text
P05 · CMX-212 port: approvals and access-request pills and sheets in React
Scope: the ⚖️ approvals pill + sheet and the 🙋 access-request pill + sheet in .safety-float,
  as islands, on the store (approvals refresh on the SSE `overrides` event).
Files: approvals.js, accessreq.js (deleted); index.html 532-547; their CSS stays
Deletes: approvals.js, accessreq.js, 5 window.chela entries
Blocked by: P04
Tests: approvals, approvals_categories, approvals_wiring, access_requests, access_requests_wiring
Risk: normal. ≈380 lines. At 300: approvals and access requests are separate slices.
```

```text
P06 · CMX-212 port: status health, resources strip, run toasts and the update banner
Scope: #status-health-warn, #resources-strip (+ resourcesmodel), the run-state toasts
  (runtoast + the toast DOM P03 moved out of sse.js), the "chela was updated, Reload" banner
  (version.js) and the post-apply flow (updateflow.ts, imported by legacy Settings until P16).
Files: statushealth.js, resources.js, resourcesmodel.js, runtoast.js, version.js, updateflow.js
  (deleted); index.html 513-522
Blocked by: P04
Tests: resources_model, runtoast, status_health_mobile, update_banner, update_banner_close_size,
  update_banner_stale, settings_update (the updateflow half)
Risk: normal. ≈450 lines. At 300: readouts and update banner split.
```

```text
P15 · CMX-212 port: Settings modal shell, search, Appearance and Collaboration tabs
Scope: the modal (on <Dialog>), the tab rail, VS Code-style search across every tab (still over
  rendered text and data-keywords, so legacy tab bodies are searchable), Appearance (theme,
  terminal fonts and size, run-toast mute, pushing theme/fonts into pane iframes) and
  Collaboration (collab name, share typing). Tabs not yet ported render through a small
  <LegacyTab> adapter that mounts that tab's existing HTML and calls its existing loader; P16-P18
  each delete one adapter use, and the last deletes the adapter.
Files: nav.js 1795-2014 (shell, search), 2259-2365 (two tab templates), 2547-2596, 2789-2806,
  3058-3137 (deleted); index.html 601-641
Blocked by: P04
Tests: settings_search, settings_modal_precedence, settings_shortcut, settings_term_size,
  settings_share_typing, term_theme_live, palette_settings (the toggleSettings half); browser
  settings_search, settings_phone_overflow
Risk: normal. ≈550 lines. At 300: shell+search, then Appearance+Collaboration.
```

```text
P16 · CMX-212 port: Settings General tab
Scope: connections & status, Update card (uses updateflow.ts), projects folder, agent permission
  mode and model, Remote Control, file drop, the Tailscale note.
Files: nav.js 2015-2125, 2366-2546, 2597-2788, 2807-2852 (deleted)
Blocked by: P15, P06
Tests: settings_update (the card), settings_remote_control, settings_file_drop
Risk: normal. ≈500 lines. At 300: status+update, then the agent/feature rows.
```

```text
P17 · CMX-212 port: Settings Timing, Dispatch and Notifications tabs (+ CMX-204's row)
Scope: the timing knobs, agents/judges at once (+ concurrencymodel), dispatch knobs, the
  notifications section, and CMX-204's Telegram row (§5).
Files: nav.js 2126-2205, 2853-3057 (deleted); concurrencymodel.js (→ .ts)
Blocked by: P15
Tests: settings_timing, settings_dispatch, settings_concurrency, concurrency_model
Risk: normal. ≈450 lines.
```

```text
P18 · CMX-212 port: Settings Cost tab (cost + usage)
Scope: the Cost tab's two views: $ by session and tokens by window, with their windows and roots.
Files: usage.js, cost.js (deleted); nav.js 2206-2258 (deleted); the <LegacyTab> adapter if this
  is the last of P16-P18 to merge
Blocked by: P15
Tests: settings_cost, settings_usage
Risk: low. ≈400 lines.
```

```text
P19 · CMX-212 port: command palette and keyboard shortcuts sheet
Scope: ⌘K / Ctrl+K palette (agents, views, projects, settings), the shortcuts cheatsheet, the
  global key handler. Registers window.chela.openPalette in iframeBridge.ts (the iframe contract,
  §3) with a guard test.
Files: nav.js 3355-3651 (deleted); index.html 548-600
Blocked by: P04
Tests: shortcuts, palette_settings, the palette half of window_id
Risk: normal. ≈400 lines.
```

```text
P20 · CMX-212 port: "+ new" launcher, primary menu, header summary and the phone FABs
Scope: the launch popover (favorites, recent, sandbox/web/shell items, init repo, schedule), the
  favorites picker, the primary (gear) menu with the summary and 5h/7d rate-limit readouts
  (header.js, agents.js checkContext), the phone menu and + FABs.
Files: nav.js 3197-3354 (deleted); launcher.js, header.js (deleted); agents.js checkContext;
  index.html 484-503, 642-720, 758-760
Blocked by: P04
Tests: topbarmenu, new_menu_sandbox, mobile_new_fab
Risk: normal. ≈550 lines. At 300: launcher popover, then primary menu + FABs.
```

```text
P21 · CMX-212 port: agent detail drill-in, send message and agent actions
Scope: the virtual agent-detail view, start/stop/restart/trigger, the send-message modal.
Files: nav.js 1713-1794 (deleted); agents.js (deleted); index.html 190-194, 814-825
Blocked by: P20 (agents.js is split between them)
Tests: sidebar_agent_detail_orchestrator_wiring, em_dash_rewrite_branches (its agents half)
Risk: low. ≈300 lines.
```

### Phase 1, lane B: decisions and Work

```text
P07 · CMX-212 port: decisions models and orchestrator state in TypeScript
Scope: decisionsmodel, feedmodel and orchestrator converted in place (git mv + types); the
  orchestrator's module state becomes store/orchestrator.ts. Legacy importers unchanged (P03).
Files: decisionsmodel.js, feedmodel.js, orchestrator.js (→ .ts)
Blocked by: P03
Tests: decisions_badge, decisions_search, feed, orchestrator
Risk: low. ≈250 lines (types on renamed files).
```

```text
P08 · CMX-212 port: the Decisions inbox
Scope: the inbox sheet (search, gap rows, re-register control, click-through to the task modal),
  the unread dot/badge and its chip. If CMX-172 has landed the chip renders inside the React
  sidebar head; otherwise it mounts at #btn-decisions.
Files: decisions.js (deleted); index.html 721-757 (+ the chip, 55-77, if CMX-172 has not
  taken it)
Blocked by: P07, P04
Tests: decisions, decisions_modal_css, decisions_seed, decisions_sse
Risk: normal. ≈450 lines.
```

```text
P09 · CMX-212 port: task modal and the markdown renderer
Scope: the task-detail modal (Brief | Review tabs, rounds, side rows, risk, CI, judge chips),
  taskmodalmodel → .ts, knowledge.js → markdown.ts. SECURITY: knLink (knowledge.js:19-22) emits
  any href, so a brief containing [x](javascript:…) renders a live javascript: link. The port
  allows only http(s), mailto and relative URLs, with a test for each rejected scheme. Rendered
  through one <Markdown> component, the only dangerouslySetInnerHTML in the app.
Files: taskmodal.js, taskmodalmodel.js, knowledge.js (deleted / → .ts); index.html 826-837
Blocked by: P04
Tests: taskmodal_model, taskmodal_render, taskmodal_review_tabs, taskmodal_judge_badge,
  risk_chip, kanban_task_modal_wiring
Risk: normal. ≈480 lines. At 300: models + markdown, then the modal.
```

```text
P10 · CMX-212 port: Runs tables and Init a repo
Scope: per-workflow run tables (status, PR, progress chip, notes, delete confirm), Linear open
  tasks, the Init-a-repo modal. runstate → .ts.
Files: dispatcher.js, runstate.js (deleted / → .ts); index.html 264-284, 851-868
Blocked by: P03, P04
Tests: runstate, runs_linear, task_handoff, task_progress_chip
Risk: normal. ≈420 lines.
```

```text
P11 · CMX-212 port: New task and Schedules
Scope: the New-task form (Linear issue create, workflows, blockers), the schedules + system
  cron tables, the add-schedule modal.
Files: newtask.js, schedules.js (deleted); index.html 285-330, 761-813
Blocked by: P04
Tests: new_task
Risk: low. ≈300 lines.
```

```text
P12 · CMX-212 port: Work board columns and cards
Scope: the kanban render: lanes, Linear columns, hidden-columns panel, cards (id chip, tracker
  link, status/CI/progress chips, handoff note). Card actions still call legacy kanban.js
  functions by import until P13. The three kanban models → .ts.
Files: kanban.js render half (_kCard 171-346, _kCol 582-610, flatten 611-730, renderKanban
  845-926, hidden panel 927-968) (deleted); kanbanhiddenmodel/lanemodel/linearmodel (→ .ts);
  index.html 228-262
Blocked by: P09 (a card opens the task modal), P03
Tests: kanban_flatten, kanban_hidden_columns, kanban_lane_model, kanban_linear_model,
  kanban_open_tracker_state, kanban_tracker_link, kanban_tracker_render
Risk: normal. ≈600 lines. At 300: models + columns, then cards.
```

```text
P13 · CMX-212 port: Work board actions, filters and phone layout (+ CMX-194)
Scope: delete confirm, merge PR, close run, promote backlog, drag-to-move, toasts; the project
  filter pills as CMX-194's multi-select (§5); swipe/rows layout and the quick-nav strip.
Files: the rest of kanban.js (deleted)
Blocked by: P12; CMX-207 merged first (§5)
Tests: whatever P12 left; RTL for the multi-select
Risk: normal. ≈450 lines.
```

```text
P14 · CMX-212 port: the Work view shell
Scope: Board | Runs | Schedules segments, dispatch hold, New task button, sidebar badges; the
  dispatcher poll becomes a scheduler lane.
Files: work.js (deleted); index.html 195-227, 331
Blocked by: P10, P11, P13
Tests: dispatch_hold
Risk: normal. ≈300 lines.
```

### Phase 1, lane W: the Wall

The Wall goes **inside out**. P26-P31 render React into tiles that legacy `terminals.js` still
builds (one root, `createPortal` into a mount node per tile, registered and released by the
legacy tile code). P33 then takes the grid itself, and the portals become plain children. The
iframe is never re-parented: Gridstack moves tiles by CSS position, and React keys every tile by
window id, so no slice may reload a pane.

```text
P22 · CMX-212 port: wall models in TypeScript
Scope: wallmodel, wire (the gesture's pure geometry) and windowid converted in place.
Files: wallmodel.js, wire.js, windowid.js (→ .ts)
Blocked by: P03
Tests: wall_tile, wall_grid_toolbar (model half), wire, window_id (model half)
Risk: low. ≈150 lines. Parallel with: anything (no importer edits after P03)
```

```text
P23 · CMX-212 port: the per-session diff modal
Scope: the Files chip's changed-files + patch modal, on <Dialog>.
Files: diffpanel.js, diffpanelmodel.js (deleted / → .ts); terminals.js:10 (one import line);
  index.html 839-849
Blocked by: P04
Tests: diffpanel_model, diff_modal_wiring
Risk: low. ≈200 lines. Parallel with: P22, P29, P24, P25
```

```text
P29 · CMX-212 port: the phone keybar
Scope: the keybar accessory row (sticky Ctrl, keys, D-pad, collapse, pinning above the soft
  keyboard, refitting the focused pane).
Files: terminals.js 1959-1981, 3943-4075 (deleted); index.html 434-482
Blocked by: P04
Tests: keybar_pane_fit, keybar_paste, keys; browser keybar_pane_fit
Risk: normal. ≈300 lines. Parallel with: P22, P23, P24, P25 (not P28: adjacent region)
```

```text
P24 · CMX-212 port: the share dialog
Scope: the share dialog (mode, duration, unsafe override, mint, copy rows) and the share toast.
Files: terminals.js 959-1130 (deleted)
Blocked by: P04
Tests: share_dialog
Risk: high (it arms sharing). ≈350 lines.
```

```text
P25 · CMX-212 port: active shares: the pill, the sheet and owner presence
Scope: the global active-shares pill + kill switch, the shares sheet with per-share mode
  control, owner presence (dynamic import of collab/presence-owner.js, typed by a .d.ts). Drops
  the unreachable chelaPresence (§3).
Files: terminals.js 497-958, 1131-1138 (deleted); index.html 523-531, 374-389 (the in-row pill)
Blocked by: P24
Tests: share_sheet, share_mode_sheet, presence_owner_rotation; browser wall_shares_pill
Risk: high (the kill switch is a safety control). ≈450 lines.
```

```text
P26 · CMX-212 port: the pane header
Scope: title + rename, Claude's auto title, state pill + status dot, facepile, the web/unsafe
  chips, the ⋯ menu (wire, share, orchestrator, pin), minimize/maximize/phone-full, kill +
  confirm. Portaled into each legacy tile.
Files: terminals.js 382-496, 1139-1507, 1740-1812 (deleted); the tile HTML's head call sites
Blocked by: P25, P22
Tests: judge_battery_badge, orchestrator_ui, share_web_chip, wall_proxy_status_title,
  sidebar_wall_indicator (header half)
Risk: high. ≈600 lines. At 300: title/state/chips, then the menu + kill.
```

```text
P27 · CMX-212 port: the tile footer, action bar and recap
Scope: context bar (window id + copy, branch, context %, model/PR/cost chips, Files chip), the
  amber action bar, the recap line + pin-open. Portaled.
Files: terminals.js 1813-1892, 2631-2825 (deleted)
Blocked by: P26
Tests: window_id (footer half), gs_files_pointer_events_css; browser window_id_footer
Risk: normal. ≈400 lines.
```

```text
P28 · CMX-212 port: dock and taskbar
Scope: minimize/restore, the always-on taskbar (MRU order, drag-reorder), pins, lazy dock for
  dispatched workers.
Files: terminals.js 1508-1739, 1893-1958 (deleted)
Blocked by: P27
Tests: walldock; browser wall_dock_toolbar
Risk: normal. ≈450 lines. Parallel with: P30, P32
```

```text
P30 · CMX-212 port: the wall layout toolbar
Scope: grid presets, lock (swap on drop), auto-arrange, focus layout toggles, collapse. Writes
  the store's wall slice; legacy grid code reads it.
Files: terminals.js 3028-3282 (deleted); index.html 339-373
Blocked by: P27
Tests: wall_grid_toolbar, wall_grid_toolbar_stored; browser wall_toolbar_row
Risk: normal. ≈350 lines. Parallel with: P28, P32
```

```text
P32 · CMX-212 port: the wire (rooms)
Scope: drag a line between two tiles to put their agents in a room; the live SVG overlay, drop
  targets, room accents.
Files: terminals.js 3365-3710 (deleted)
Blocked by: P26 (the port lives in the header)
Tests: wire_live_css; RTL for drag, Escape and blur cleanup
Risk: normal. ≈350 lines. Parallel with: P28, P30
```

```text
P31 · CMX-212 port: the pane frame
Scope: <PaneFrame wid> owns the iframe: src, readiness placeholder + polls + retry + swap on
  `term-ready`, paste (text, HTML, images), scroll toggle, focus tracking + ring, iframe
  shortcuts, background-tab teardown (keeps data-suspended-src, §3), ignore-size watch
  reporting, the 4 s tick. Portaled into the legacy tile body.
Files: terminals.js 112-381, 2133-2598 (deleted)
Blocked by: P28, P30, P32
Tests: wall_focus_tracking, wall_focus_ring_overlay_css, wall_focus_ring_single_meaning,
  term_ignore_size, plus a boundary test naming every row of §3's table; browser first_view_fit
Risk: high. ≈600 lines. At 300: readiness+paste, then focus+teardown+watch.
```

```text
P33 · CMX-212 port: the wall grid
Scope: useGridstack (init, makeWidget/removeWidget keyed by window id, destroy), wall vs single
  mode, layout persistence (pc_wall_*), presets/auto/focus/lock application, maximize, last
  viewed pane, the `windows` event. Portals become children. terminals.js is deleted.
Files: terminals.js (the rest, deleted); index.html 334-482 (what remains)
Blocked by: P31
Tests: wall, wallnav, term_last_pane, term_last_pane_boot, term_last_pane_tap, walldock (grid
  half); browser dashboard
Risk: high. ≈600 lines. At 300: grid + tiles, then layout modes.
```

### Phase 2: shell and done

```text
P34 · CMX-212 port: the app shell
Scope: index.html's body becomes <div id="root">; <App> renders sidebar, canvas, safety float
  and overlays; view state and switching (nav.js 136-187, views.js, viewreg.js); islands become
  children of one root; window.chela shrinks to iframeBridge.ts (openPalette, toggleSettings);
  util.js's facade and remaining helpers are deleted or moved to TS; tab title + favicon badge.
Files: templates/index.html; nav.js (whatever CMX-172 left); views.js, viewreg.js, util.js
  (deleted); style.css imported by the entry
Blocked by: P05, P06, P08, P14, P18, P19, P21, P33, CMX-172
Tests: views, dashboard_scale_nav_a11y, dashboard_shell_grid, restyle_real_shell_css,
  ios_focus_zoom, dashboard_default_view; every browser suite
Risk: high. ≈450 lines.
```

```text
P35 · CMX-212 port: definition-of-done guards and the last sweep
Scope: the guards in §6 as tests; delete dead style.css rules (selectors no component renders);
  retire _LUCIDE; the no-emoji guard's legacy allowlist reaches zero (§5); docs/DASHBOARD.md
  and docs/FRONTEND.md updated.
Files: style.css; tests (guards)
Blocked by: P34
Tests: the §6 guards
Risk: normal. ≈150 lines.
```

**Count:** 35 slices (P01-P35), plus CMX-172's sidebar slices.

---

## 5. Interplay with queued UI tasks

| Task | What it does | Recommendation | Why |
|---|---|---|---|
| CMX-199 (emoji → Lucide, slice A: one icon map, a no-emoji guard test, first conversions) | | **Legacy first, narrowed.** Land the semantic icon map and the guard now. Shape the map as names → Lucide icon names, so P04's `icons.ts` reuses it unchanged. The guard carries a per-file allowlist of legacy modules not yet converted; each port slice removes its files from it. | The expensive part is deciding which icon means what; that transfers to React untouched. The guard ratchets the port, so nothing regresses while it runs. |
| CMX-200 (emoji → Lucide, the rest of the conversions) | | **Fold into the port slices.** Each slice renders its icons through `icons.ts` and drops its files from the allowlist. P35 asserts the list is empty. | Converting emoji in a template string that a port slice deletes a week later is work done twice. |
| CMX-202 (phone: remove the session pill strip above the terminal) | `terminals.js:3806-3942`, `index.html:390-433` | **Legacy first.** | Pure deletion: it shrinks P33 and takes `mobile_pills_sidebar_order` with it instead of porting it. |
| CMX-194 (Work board project pills become a multi-select) | `kanban.js:731-770` | **Fold into P13.** | The filter is rewritten in P13 anyway; doing it twice buys nothing. If CMX-194 is wanted before P13 can start, do it in legacy as a pure model function so P13 moves it as-is. |
| CMX-207 (Work board toolbar cleanup: remove merge-all-mergeable and its route) | `kanban.js:464-522`, the `merge-all` route | **Legacy first, before P12.** | Deletion again: it removes code P13 would otherwise have to port, and the route removal is backend work no port slice owns. |
| CMX-204 (Telegram auto topics reopen a folder's old topic; adds a Settings row) | backend + one row | **Split: backend now, the row in P17.** If it must ship before P17 starts, add the row in legacy (about 20 lines) and P17 ports it. | The row is the only UI; the backend change is independent of the port. |
| CMX-121 (sidebar rows: title first, metadata, Lucide icons) | | **Already folded into CMX-172.** Keep it there. | Same reason as CMX-194. |

---

## 6. Definition of done

"The entire dashboard view is React" means all of these hold on `dev`, each enforced by a test
P35 adds:

1. **No legacy view module is left.** None of the 43 module names in §1 exists under
   `chela/dashboard/static/js/` (the directory is gone or empty). `static/collab/`,
   `static/term-*.js`, `static/term-touch.css` and `static/vendor/` remain, by §3.
2. **`templates/index.html` is the Vite entry**: a `<head>`, the pre-paint sidebar script, one
   boot JSON block and `<div id="root">`. No inline `on*=` attribute and no `chela.` call in it.
3. **`window.chela` holds exactly the iframe contract** (`openPalette`, `toggleSettings`) and
   nothing else; ⌘K and Ctrl+, from inside a pane still work (a browser test presses them in a
   pane).
4. **Every former node suite is ported to Vitest/RTL or retired with a reason.** The reasons are
   collected in a table in `docs/FRONTEND.md`. All 13 browser suites are green against the built
   page.
5. **The look is unchanged.** The eight palettes and their contrast tests pass; the browser
   suites' layout assertions pass.
6. **No dual implementation.** No `.js` and `.ts` module with the same base name; no `LegacyTab`
   adapter; no portal into legacy-built nodes.
7. **The boundary in §3 is tested row by row.**
8. **One UI kit.** No component outside `ui/` renders a raw `<input>`, `<select>`,
   `<textarea>`, `<button>` or `<dialog>`, or imports `@radix-ui/*` directly; exemptions sit in
   an allowlist with a reason each (rule 7, §4).
9. **Every decision in §7 holds** on the built page; P35 checks each row and names its test.

## 7. UX decisions made during the port

Liav's calls from using the dashboard. Each lives in its slice's issue too; this table is the
copy that survives a re-split. A slice that touches a row's surface keeps the row true, and P35
verifies all of them before the release. *Held by* names each row's test (`path` "title"):
P35 (CMX-249) checked each, and `tests/test_port_done.py` fails on a row that names none or names
a test that does not exist.

| Date | Surface | Decision | Slice | Held by |
|---|---|---|---|---|
| 10-19 | UI kit | Behaviour from Radix Primitives, look from `style.css`; no shadcn/ui, no Radix Themes, no Tailwind (§2.4) | P04 · CMX-218 | `chela/dashboard/frontend/src/uikit.test.ts` "no shadcn/ui, Radix Themes or Tailwind"; `chela/dashboard/frontend/src/uikit.test.ts` "nothing outside shared/ui renders a raw control or imports Radix" |
| 10-19 | Forms | One `FieldRow` grid (label, control, unit): every control in a section starts at the same x | P04 · CMX-218 | `chela/dashboard/frontend/src/shared/ui/tests/form.test.tsx` "two FieldRows with very different labels start their controls at the same x" |
| 10-19 | Work board | A column has a normal max width (about 360-420px); one visible column does not stretch across the screen; many columns scroll sideways | P12 · CMX-233 | `chela/dashboard/frontend/src/features/work/tests/Board.test.tsx` "a column carries a max width, and the board scrolls sideways rather than squeezing" |
| 10-19 | Pane header | A plain shell pane reads `Shell · <cwd>` with a `shell` chip until Claude starts in it; the tmux window name is never changed | P26 · CMX-241 | `chela/dashboard/frontend/src/features/panehead/tests/PaneHead.test.tsx` "a shell pane reads"; `chela/dashboard/frontend/src/features/panehead/tests/model.test.ts` "headLabel: a shell reads Shell and its cwd" |
| 10-19 | Tile footer | The footer (window id, model, context %) shows in every pane mode: normal, maximized, phone-full | P27 · CMX-242 | `tests/browser/wall_footer_tray.test.mjs` "the footer shows on a wall tile and on a MAXIMIZED one"; `tests/browser/wall_footer_tray.test.mjs` "the single pane filling the screen" |
| 10-19 | Tile context bar | The bottom-edge context-% bar stays visible on the focused pane: the highlight border never covers it | P27 · CMX-242 | `tests/browser/wall_footer_tray.test.mjs` "the focus ring never covers the context fill" |
| 10-19 | Minimized tray | One row with a fixed height that scrolls sideways; label shows the count; fade on the hidden side; wheel scrolls it | P28 · CMX-243 | `tests/browser/wall_footer_tray.test.mjs` "ten minimized panes keep the tray ONE row that scrolls sideways"; `tests/browser/wall_footer_tray.test.mjs` "a fade marks the hidden end, and a vertical wheel scrolls the row sideways" |
| 10-19 | Phone wall | No session pill strip above the terminal; the sidebar is the session switcher | CMX-202 (deleted it; no slice re-adds it) | `tests/browser/wall_toolbar_row.test.mjs` "no pill strip is rendered above the pane" |
| 10-18 | Sidebar | One Archived control | CMX-172 | `chela/dashboard/frontend/src/features/sidebar/tests/Sidebar.test.tsx` "ONE archived control" |
| 10-19 | Sidebar | The Dispatched group is always last, after every human folder group | CMX-172 | `chela/dashboard/frontend/src/features/sidebar/tests/Sidebar.test.tsx` "Dispatched renders after EVERY folder group" |
| 10-19 | Sidebar | Recent sessions never lists a live session (match by session id, not window address); judge and dispatched sessions sit under the dispatcher toggle with no Resume | CMX-172 | `chela/dashboard/frontend/src/features/sidebar/tests/Recent.test.tsx` "a recent row whose SESSION is live now is omitted"; `chela/dashboard/frontend/src/features/sidebar/tests/Recent.test.tsx` "a judge session sits under the dispatcher toggle, and has no Resume there either" |
| 10-16 | Icons | Lucide via `icons.ts`, never emoji; no em dash in UI strings | all (§5) | `tests/no_emoji.test.mjs` "the real tree is green against the allowlist"; `tests/no_emoji.test.mjs` "the legacy allowlist is empty"; `tests/test_no_em_dashes.py` "def test_dashboard_copy_has_no_em_dash" |
| 10-20 | Sidebar footer | The build/version string lives in the Settings modal, not the sidebar footer | P15 · CMX-221 | `tests/browser/sidebar_foot_version.test.mjs` "the build string is in Settings, not the sidebar" |

---

## 7. Estimate

| | |
|---|---|
| Slices | 35 (plus CMX-172's) |
| Critical path | P01 → P03 → P04 → P24 → P25 → P26 → P27 → P30 → P31 → P33 → P34 → P35: **12 slices** |
| Per slice, expected | ≈2 h agent work + ≈1.25 h per judge round × ≈1.5 rounds (at the 66% clean-on-round-1 baseline) + ≈0.5 h rework ≈ **4-4.5 h** |
| Throughput bound | 35 slices × 4.25 h / 3 slots ≈ **50 slot-hours** |
| Critical-path bound | 12 × 4.25 h ≈ **51 h** |
| Expected wall-clock | **≈55-65 h of continuous dispatch**, i.e. 3-4 calendar days with merge latency and the serial phase 0, if the slots stay full |

The two bounds are about equal, so neither more slots nor fewer slices alone shortens it much.
Two things move it: a 4th slot during phase 1 helps only if the Wall lane is never starved, and
the slice-size check in §0. If the first three port slices need clearly more rounds than the
baseline and the remaining slices shrink to 300, the count grows to about 55 and the estimate to
5-7 calendar days.
