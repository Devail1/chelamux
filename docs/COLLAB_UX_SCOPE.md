# chelamux collaboration UX — scoping doc

_Scope: presence-only (no E2E, no auth tokens). Portfolio-first: design budget goes to share button + presence surfacing + the phone viewer. Produced by a Fable-5 scoping pass, 2026-07-09, grounded in the dashboard code._

## 1. Current state (grounded in code)

**Enablement** — `chela/dashboard/static/collab/presence.js` self-gates on `?collab` in the *iframe's* URL. The shim is injected into every ttyd page by `_TERM_PRESENCE_SHIM` in `app.py` (~L591), with config `{relay, prefix, cols, rows}` from `CHELA_COLLAB_RELAY` / `collab.instance_id()` / `CHELA_TERM_COLS/ROWS`. Nothing in the dashboard UI ever sets `?collab=1`; the wall's iframes (`_wallTileHTML`, `frame()` in `terminals.js`) are built as `/term/<wid>/` bare. The feature is invisible unless you hand-edit a URL.

**Rooms / links** — room id = `sanitize(<instance_secret>-<wid>)` (`collab.py room_id()`, mirrored in `presence.js`). The "link" today is `/term/<wid>/?collab=1` — a raw full-page ttyd terminal with no chrome, no title, no name prompt. `require_auth` in `app.py` is a documented no-op; the security boundary is the network (loopback/tailnet), so the reachable URL *is* the invite.

**Presence** — pills + cursors are rendered *inside the iframe* by `presence.js` (fixed top-right, huge z-index), overlapping terminal content. Names are `pick(NAMES)-NN` ("Hawk-52"), regenerated on every page load — not persisted, not editable. Agent-as-peer pills (`⚙ claude · name`, ringed, status pip) are published server-side by `collab.py`'s heartbeat thread. The dashboard pane header (`paneHead()` in `terminals.js`) knows nothing about any of this.

**Shared view** — none. The wall is per-client state (Gridstack layout, order, minimized, titles all in `localStorage`). The only shared thing is per-*window*: `POST /api/term/<wid>/grid` pins one tmux window to 120×30 when the room has 2+ human peers; each client letterbox-fits via font-size re-render (`fitFont()`).

**Mobile** — wall force-collapses to single mode under 768px, with pill switcher, header-swipe, keybar v2 — all dashboard DOM. A joiner opening the raw `/term/<wid>/?collab=1` link on a phone gets none of it.

**Command palette** — `nav.js _paletteItems()` (⌘K): flat verb-first list. In-iframe shim `_TERM_PALETTE_KEY_SHIM` (app.py ~L401) calls `window.parent.openPalette()` — an established iframe→parent pattern. Font prefs flow parent→iframe via same-origin `localStorage` + `storage` events (`_TERM_FONT_PREF_SHIM`). Both patterns are directly reusable for collab.

## 2. Proposed UX per surface

### 2.1 Start sharing — pane-header share button (primary)
Add a share glyph to `.gs-win-ctl` in `paneHead()`, before minimize (share · min · max · kill), on wall tiles and single view.
- **First click:** `POST /api/term/<wid>/share {on:true}` flips a server-side per-wid "shared" flag; `term_http` injects `"shared":true` into `__CHELA_COLLAB__`, and `presence.js` gates on `(cfg.shared || ?collab)`. Gives clean `/term/<wid>/` links (no magic query) and "Stop sharing" real teeth (today `?collab` is client-side, host can't revoke). Surgically reload only this pane's iframe. Copy absolute link to clipboard + toast. Button → accent + viewer-count badge.
- **Click while shared:** popover (reuse `#fav-add-menu` pattern) with link+copy, who's-here, and **Stop sharing** (→ `share {on:false}`, reload bare, explicit grid restore).
- Shared state survives reload: server flag authoritative, reported via `/api/agents` or `/api/term/shared`.

### 2.2 Start sharing — command palette
`_paletteItems()`: one `Share <label>` / `Stop sharing <label>` item per live session → same `toggleShare(wid)`. The palette IS the command modal in this codebase; no new modal.

### 2.3 Presence surfacing on the dashboard
Move presence out of the iframe overlay into the pane header when embedded:
- `presence.js`: on awareness change, `window.parent.chelaPresence(wid, peers)` (guarded, palette-shim pattern); suppress in-iframe pills when hooked (keep cursors in-iframe — coordinate-bound). Standalone joiner keeps in-iframe pills.
- `terminals.js`: `chelaPresence(wid, peers)` renders a compact facepile into `.gs-presence` in `paneHead()`: ≤3 human initial-dots, `+N` overflow, agent `⚙` pip using the same `_STATUS_COLOR` so agent presence and pane status never disagree.
- Share button badge = human viewer count. Min-dock chips + mobile switcher pills get the same count badge.

### 2.4 Shared view — recommendation: NO dedicated shared wall
**Presence overlays on the existing wall (host) + focused single-pane session view (joiner).** Reasons: (1) the shareable unit is the tmux window, not the wall — rooms/grid/agent-peer are all per-wid; a shared wall has no backing primitive. (2) Wall layout is deliberately per-client localStorage; syncing forces one layout on everyone or needs new Yjs layout-doc state. (3) Cost scales badly (N rooms × cursors × N pinned windows degrades the host's own wall). (4) Use cases don't need it — "pair on one agent" wants one big terminal; "watch the fleet" is read-mostly, served by the dashboard URL on the tailnet with presence overlays.
- Joiner canonical experience = single-pane session view (share link opens one terminal, full viewport). **P2:** minimal `/watch/<wid>` template (slim bar: chela mark, title, presence, "Powered by chelamux") — strong portfolio surface for ~50 lines.
- **Joiner first-open (P1, presence.js):** centered hello card ~5s: "You're viewing *<title>* — N here · you are **Hawk-52** [edit]".

### 2.5 Mobile (read-mostly fleet watching)
- Dashboard on phone: already single mode + pill strip. Add presence count badge on active pill; surface a slim presence strip (facepile + share) since `.gs-head` is hidden ≤768px. Share = `navigator.share` if present else copy+toast.
- Joiner on phone: fixed 120×30 + `fitFont()` is legible in landscape; portrait small but acceptable for read-mostly; "rotate for a better view" hint when portrait. No keybar for joiners (fine — read-mostly). Honesty note: ttyd socket is technically writable; true read-only is auth-scope, out.
- Adaptive-grid rule stays: 1 human = dynamic, 2+ = fixed.

### 2.6 Identity
Keep auto-names as zero-friction default; add optional persistent display name via `localStorage.chela_collab_name` (+ color), same mechanism as `_TERM_FONT_PREF_SHIM` (live via `storage` event). Set it in Settings drawer (`renderSettings()`, new "Collaboration" section, also show relay URL read-only) and the joiner hello card. **Also: persist the auto-name once picked** (today it rerolls per load — presence continuity is broken even without the feature). Color: stable name-hash, not random; initials give the colorblind-safe non-hue cue.

## 3. Component-level changes
| Change | Files / functions |
|---|---|
| Share button + `.gs-presence` slot | `terminals.js paneHead()`; `style.css` near `.gs-win-ctl` |
| Share toggle + copy + popover | new `toggleShare()/sharePopover()` in `terminals.js` (popover from `launcher.js openFavAdd`; toast from `kanban.js _kanbanMergeToast`) |
| Server shared-flag | `app.py`: `POST /api/term/<wid>/share`, per-wid shim inject, report via `/api/agents` or `/api/term/shared`, explicit grid restore on un-share |
| Collab-aware iframe src | `terminals.js`: `_termSrc(wid)` helper across `frame()`/`_wallTileHTML`/`_swapToFrame`; surgical single-pane reload |
| Presence → parent | `presence.js`: `window.parent.chelaPresence()`; suppress in-iframe pills when hooked; hello card standalone; name/color from localStorage |
| Facepile + badges | `terminals.js chelaPresence()`; badges in `renderMinDock()` + `renderMobileSwitcher()` |
| Palette action | `nav.js _paletteItems()` per-session Share/Stop |
| Identity setting | `nav.js renderSettings()`; `presence.js` reads `chela_collab_name` + storage listener |
| Joiner chrome (P2) | `app.py` `/watch/<wid>` minimal template |
| Productionization | vendor Yjs (presence.js imports from esm.sh → dies offline); reconnect backoff |

## 4. Phased priority
**P1 — demo-critical:** (1) share button + `toggleShare` + copy-link toast + accent/badge; (2) server shared-flag + clean `/term/<wid>/` links; (3) presence facepile in pane header via iframe→parent (humans + `⚙` agent + count); (4) persistent display name + stop rerolling auto-names; (5) palette Share action.

**P2 — polish:** (6) share popover (who's-here, copy, Stop sharing); (7) joiner hello card + `/watch/<wid>` chrome; (8) mobile presence strip, `navigator.share`, switcher/dock badges; (9) vendor Yjs, reconnect backoff, explicit grid-restore on stop.

**P3 — nice-to-have:** (10) stable name-hash colors + colorblind-safe presence palette; (11) cursor-label fade; (12) sidebar Sessions shared/eye glyph; (13) deferred: shared wall / synced layout (rejected §2.4), read-only enforcement + revocable invite tokens (auth scope).

## 5. Added requirements (from Liav, post-scope — fold into P1/P2)
- **Custom window styling for the shared view** — give the shared pane the wall's window chrome (title bar + presence) and **center the letterboxed terminal on a styled backdrop** so the letterbox margins read as intentional framing. Fixes the visible "content hugs left, empty right on a wide screen" gap. (P1-adjacent — it's the thing currently looking broken.)
- **Initiator sets the shared grid size** — replace the hardcoded 120×30 with the *initiator's* current pane dimensions captured at share-time and propagated to joiners (room param / Yjs awareness); everyone letterboxes to that ("presenter" model). Define fallback if the initiator leaves. (P2.)
