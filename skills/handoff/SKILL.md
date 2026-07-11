---
description: Generate a structured handoff document so a future Claude session can pick up the current workstream cold. Saves to memory/handoff_<topic>_<date>.md.
arguments: [topic]
---

You are producing a handoff document for a future Claude session that will pick up the current workstream **cold** — no shared conversation context. Your job is to capture everything they need to be productive immediately.

## Topic

If `$topic` is provided, scope the handoff to that workstream. If not, ask the user one targeted question to disambiguate (don't guess; a wrong scope produces a useless handoff).

## Where to save

Write to your memory directory: `.claude/memory/handoff_<topic-slug>_<YYYY-MM-DD>.md` (adjust the path to wherever your project keeps its memory files).

Use a slug derived from the topic (lowercase, dashes for spaces). Then add a one-line pointer entry to `MEMORY.md` under the appropriate section (or under a "## Handoffs & Recent Sessions" heading if one exists).

## Required structure

Use this exact section order. Skip a section only if it genuinely doesn't apply (e.g., omit "What the data looks like" for a pure refactor).

### Frontmatter

```yaml
---
name: <One-line title>
description: <Used by future Claude to decide if this handoff is relevant. Be specific about what's in flight and what's NOT in scope.>
type: project
---
```

### What's done (don't redo)

List shipped commits + what each delivered. Reference the project's main memory file. State the current "v1 functional surface" if applicable — i.e., what the user can already do. Future Claude must not re-do this work.

### Scope / goal

What this handoff is asking the next session to build or decide. Be concrete: file paths, endpoint names, expected behavior.

### Files to read first

Pre-curate the entry points so the next Claude can avoid bouncing around the codebase. List the closest-analog existing files (e.g., "Roster.tsx is the closest pattern to model the new page on"). Include a one-sentence "what to look for" for each.

### What the data looks like

If the workstream involves consuming structured data (an API response, a SQLite schema, a state file), include a sample or a one-shot command to fetch one. Don't assume the next session will know the shape.

### Pitfalls

Things that aren't obvious from the code:
- Hidden constraints (env vars that must be set, services that must be restarted)
- Patterns that look duplicable but aren't (e.g., "Don't re-implement X; the endpoint already does Y")
- Latency/timing concerns (e.g., "this call takes 3 min — UI must show progress")
- Things that look like bugs but aren't (e.g., known cosmetic issues that are tracked elsewhere)

### Suggested first action

A specific opening move. Not "explore the code" — name the file to read, the function to write, or the command to run. Reduce decision-paralysis on session start.

### State at handoff

A snapshot for orientation:
- Repo HEAD commit
- Working tree status (clean / what's uncommitted)
- Running services + their uptimes (PM2, cron entries, etc.)
- Persistent data of note (DB row counts, file mtimes if relevant)

### What this skill does NOT do

Explicitly mark out-of-scope items so the next session doesn't drift into them. Examples: "improve the citation verifier (minor, can wait)", "user-tuning tasks, not engineering."

## How to gather this content

Don't fabricate. Source everything from:
1. **Current conversation** — what's been built/decided in this session
2. **Memory files** — read the project's main memory + any related sub-files
3. **Live repo state** — `git log --oneline -20`, `git status`, `pm2 list`, `crontab -l`
4. **Live service state** — if a backend service has logs or a DB, sample them

If you can't ground a section in real evidence, ask the user rather than guess.

## After writing

1. Print a 1-line confirmation with the file path
2. Suggest the user run `/clear` and start a fresh session pointing at this handoff
3. If `MEMORY.md` was updated with the pointer, mention that too

## Output style

Match the project's existing memory-file style: tight, specific, no preamble or filler. Code blocks for commands, tables for option grids, dashes for lists. Lean toward overspecifying file paths and undersp ecifying narrative.
