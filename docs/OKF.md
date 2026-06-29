# Design: OKF Knowledge Layer + Viewer

**Status:** Design / not yet implemented
**Date:** 2026-06-29
**Owner:** chela

A design for exporting chela's accumulated fleet knowledge as a portable
[Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf)
(OKF v0.1) bundle, and — the centerpiece — a **viewer** that lets a human
glance at, browse, search, and navigate that knowledge.

---

## Motivation

chela already *reads* a lot of fleet knowledge but only renders it
ephemerally in the dashboard:

- transcript recaps + PR links (`chela/transcripts.py`)
- dispatcher runs (`runs` table, `chela/dispatcher.py`)
- scheduled tasks (`tasks` table, `chela/scheduler.py`)
- per-agent context usage (`~/.chela/context/<window>.json`)
- live agent/window state (`chela/discovery.py`, `chela/agent_manager.py`)

None of this is persisted in a portable, inspectable shape. When a session
ends, its working knowledge is locked inside a JSONL transcript. The
orchestrator relays context between sessions by hand. There's no way to ask
"what does the fleet collectively know right now?" and *see* the answer.

OKF gives that knowledge an on-disk shape: a directory of typed markdown
files with YAML frontmatter, vendor-neutral, readable by any tool or human.
But OKF on disk is invisible — a folder of `.md` files. **The value only
materializes with a viewer**, because the two things that make OKF more than
a folder are both latent:

- it is **graph-shaped, not just tree-shaped** (concepts cross-link via
  markdown links → untyped directed edges)
- it is built for **progressive disclosure** (`index.md` per directory)

Neither shows up when you `ls` the bundle. So this design treats the viewer
as the product and the export as the pipeline that feeds it.

---

## OKF v0.1 in one screen (what we must conform to)

The conformance bar is deliberately low:

- **Required:** every non-reserved `.md` file has parseable YAML frontmatter
  containing a non-empty **`type`** field. That's it.
- **Recommended (soft):** `title`, `description`, `resource` (a URI for the
  underlying asset), `tags` (YAML list), `timestamp` (ISO 8601).
- **Extensions:** producers may add arbitrary keys; consumers MUST preserve
  unknown keys and MUST tolerate broken links / unknown types / missing
  optional fields.
- **Reserved files:** `index.md` (directory listing, no frontmatter, entries
  as `* [Title](url) - description`) and `log.md` (date-grouped history,
  newest first, `YYYY-MM-DD` headings).
- **Links:** absolute bundle-relative (`/tables/x.md`) or relative
  (`./x.md`); relationship type is conveyed by prose, edges are untyped.
- **Citations:** sources under a `# Citations` heading.
- **Versioning:** declare `okf_version: "0.1"` in the bundle-root `index.md`
  frontmatter (the only place frontmatter is allowed in an index).

Practical consequence: emitting a conformant bundle is essentially "write
frontmatter with a `type` field." We can be liberal in what we emit and the
viewer must be liberal in what it accepts.

---

## Producer: what chela exports

chela's data model maps almost 1:1 onto OKF typed concepts:

| chela source | OKF `type:` | file |
|---|---|---|
| `runs` row (`dispatcher.py`) | `Dispatch Run` | `runs/<task-id>.md` — `resource:` = `pr_url`, `timestamp:` = `ended_at`, body = recap |
| `tasks` row (`scheduler.py`) | `Scheduled Task` | `schedules/<id>.md` |
| agent window (`discovery.py`) | `Agent` | `agents/<window>.md` — CWD, liveness, links → its runs |
| project repo | `Project` | `projects/<name>.md` |
| transcript recap + PR (`transcripts.py`) | folded into Agent/Run body; transcript path under `# Citations` |

### Bundle layout emitted

```
~/.chela/knowledge/              # default; --out to override
├── index.md                     # okf_version: "0.1" in frontmatter
├── log.md                       # fleet activity, date-grouped, newest first
├── viewer.html                  # self-contained portable viewer (see below)
├── agents/
│   ├── index.md
│   └── researcher.md            # links → its runs, its project
├── runs/
│   ├── index.md
│   └── task-0042.md             # resource: <pr_url>
├── schedules/
│   └── index.md
└── projects/
    └── index.md
```

`index.md` files reuse the progressive-disclosure listing the dashboard
already renders (`* [Title](url) - desc`). `log.md` falls out of dispatcher
run history for free (date-grouped, newest first).

### Where it plugs in

- **New module `chela/okf.py`** — pure serializer: dataclasses →
  markdown+frontmatter, using the existing `pyyaml` dep. No new daemon, no
  new external process — consistent with chela's "lean on the DB / tmux"
  pattern. Designed to pair with a future reader half in the same module.
- **CLI** — `cmd_knowledge` in `chela/main.py` (subparser block ~L286):
  `chela knowledge export [--out DIR] [--since DATE]`. Mirrors how
  `dispatch` / `schedule` are wired.
- **Auto-refresh (optional)** — a `WORKFLOW.md` `hooks.after_done` entry
  re-exports on PR merge, so the bundle stays live without a cron.

---

## Viewer: the centerpiece

Two deployment shapes, same conceptual UI. Build the data model once; render
it twice.

### A. Embedded (live) viewer — in the chela dashboard

Reads `~/.chela/knowledge/` (or regenerates on demand from the DB). Lives in
the existing Flask + Lit/HTMX SPA (`chela/dashboard/`).

- **Flask routes** (read-only, loopback-guarded like the rest of the
  dashboard):
  - `GET /api/knowledge/tree` — directory + index structure for browse pane
  - `GET /api/knowledge/concept?path=...` — one concept: parsed frontmatter
    + rendered body + computed backlinks
  - `GET /api/knowledge/search?q=...&type=...&tag=...` — search results
  - `GET /api/knowledge/graph` — nodes (concepts) + edges (links)
- **Lit components** in `index.html` — a new "Knowledge" view alongside the
  agent wall / kanban.

### B. Portable viewer — shipped inside the bundle

A single self-contained `viewer.html` written into the bundle on export. It
reads its sibling `.md` files (via `fetch` when served, or a small inlined
manifest for `file://`) and provides the same browse/search/graph UI with
**zero chela install**. This matches OKF's no-runtime / "just files,
shippable as a tarball" ethos: export a bundle, open `viewer.html` anywhere,
see and search the knowledge. Strong portability + portfolio story.

> Build order: do the parsing/index/search/graph logic as plain JS that runs
> in both contexts, so A and B share code rather than diverging.

### The four UI surfaces

1. **Glance** — "what does the fleet know" overview: counts by `type`,
   recent activity from `log.md`, freshest concepts by `timestamp`. The
   one-screen answer to "what's in my memory right now."
2. **Browse** — progressive disclosure per OKF intent: root `index.md` →
   drill into directories → open a concept rendered as markdown, with a
   **frontmatter header card** (type badge, `resource` link, tags,
   timestamp) and a **backlinks panel** (what links *to* this concept).
   Backlinks are the single thing raw files can never show — highest-value,
   cheapest to compute (invert the link graph).
3. **Search** — full-text over frontmatter (`title` / `description` / `tags`
   / `type`) + body, filterable by type / tag / date. All local → SQLite FTS
   (embedded) or a small in-memory index (portable).
4. **Graph** — concepts as nodes, markdown links as edges; click a node to
   open it. Where "graph-shaped, not just tree-shaped" becomes visible.

### Consumer robustness (per spec)

The viewer is an OKF *consumer*, so it MUST: tolerate broken links (render as
dangling, never crash), tolerate unknown `type` values (show the badge as-is),
tolerate missing optional fields (derive `title` from filename), and preserve
unknown frontmatter keys (show them in a raw-fields section).

---

## Beyond chela's own data (future)

Because the viewer consumes *any* OKF bundle, it generalizes:

- Point it at the orchestrator's `~/.claude/.../memory/` (already markdown +
  frontmatter + `[[wikilinks]]`) — a near-OKF source. A thin adapter
  (`[[name]]` → markdown link, `metadata.type` → `type`) would make the whole
  personal memory system browsable/searchable in the same viewer.
- Mount external OKF bundles (e.g. Google's `ga4` / `stackoverflow` samples)
  to validate the consumer against third-party producers.

This is the interop payoff: one viewer, any OKF source.

---

## Scope / phasing

1. **This doc** ✅ — design captured.
2. **MVP export** — `chela/okf.py` serializer + `chela knowledge export`
   emitting a conformant bundle from `scheduler.db`. One module, one command,
   no new deps.
3. **Embedded viewer** — Flask read-only routes + Knowledge view in the SPA
   (glance + browse + backlinks first; search; then graph).
4. **Portable viewer** — `viewer.html` shipped in the bundle, sharing the JS
   logic from phase 3.
5. **Adapters** — `~/.claude` memory source; external bundle mount.

## Open questions

- Embedded viewer: read a pre-exported bundle, or regenerate live from the DB
  each request? (Live = always fresh, no stale export; pre-exported = simpler,
  matches the portable path.)
- Search backend: SQLite FTS (we already ship SQLite) vs. in-memory index
  shared with the portable viewer.
- Do we want typed edges eventually? OKF edges are untyped by spec; we could
  encode relationship hints in link prose and parse them, staying conformant.

## Portfolio framing

"chela exports its fleet's working knowledge as a Google Open Knowledge
Format bundle — vendor-neutral, self-viewing, mountable by any agent or
tool." Standards-aligned, current, and it pre-positions the *agent personas*
roadmap item (a persona is just another OKF `type`).
