---
name: handoff
description: Generate a structured handoff document so a future Claude session can pick up the current workstream cold. Saves to memory/handoff_<topic>_<date>.md.
arguments: [topic]
argument-hint: "[optional: topic slug to scope the handoff, or an output path]"
---

You are producing a handoff document for a future Claude session that will pick up the current workstream **cold** — no shared conversation context. After reading the document it must be able to continue the work directly, without asking again. Capture everything worth carrying forward, and nothing that isn't.

## Topic

If `$topic` is provided, scope the handoff to that workstream. If not, ask the user one targeted question to disambiguate (don't guess; a wrong scope produces a useless handoff).

## Information source: this session, distilled

The document is a persisted distillation of the **current session context** — a compact that survives to disk. Writing it is bookkeeping, not a new task:

- **Default to no investigation.** Do not read files, re-run tests, or explore code to fill gaps. Information the session doesn't contain is written as "not covered in this session", not looked up. Re-investigating risks writing confident-but-stale "current state" that was never actually established this session.
- **Verification status comes from the session record.** For every test / lint / build actually run this session, report the **latest** result seen. Anything not run is "not verified" — never "should be fine", and never re-run now to upgrade it.
- **Three fenced live-state probes are allowed** — only these, only to ground the header and the `State at handoff` section, because this is orchestrator ops-state the conversation genuinely doesn't hold:
  1. `date +%F` for the filename — never the date from memory.
  2. `git log --oneline -3 && git status --porcelain` for the HEAD/branch/tree line — a stale hash written from memory misleads the successor.
  3. Your service manager's process list and scheduled jobs, plus one-shot samples of a service's DB/log (row counts, mtimes), for `State at handoff` — running services are load-bearing state and are not in the transcript.

  Everything else stays session-only. If you can't ground a section in the session or one of these three probes, ask the user rather than guess.

## Where to save

- Default: your memory directory — wherever this project keeps its memory files (e.g. `.claude/memory/`) — as `handoff_<topic-slug>_<YYYY-MM-DD>.md` (slug = lowercase, dashes for spaces; `<YYYY-MM-DD>` from `date +%F`). Keep handoffs where your memory recall looks; don't scatter them into project dirs unless the arguments give an explicit output path (a path in the arguments wins).
- Then add a one-line pointer entry to `MEMORY.md` under the appropriate section (or under a "## Handoffs" heading if one exists). A handoff not registered in `MEMORY.md` is invisible to recall.
- **If the file already exists: read it first.** Same task continuing → update the file in place, stating only the **latest** state — no revision narration ("previously", "compared to last time"). Different task that collides on the slug → append `-HHMM` to the filename.

## Required structure

Use this section order. Trim to what actually exists — delete an empty section entirely, never pad.

### Frontmatter

```yaml
---
name: <One-line title>
description: <Used by future Claude to decide if this handoff is relevant. Be specific about what's in flight and what's NOT in scope.>
type: project
---
```

### Task and goal

What is being done, the user's original request, and what counts as done (acceptance criteria).

### Current progress

- **Done** — each item with file:line or commit hash, plus verification status (tests passing / not verified). Future Claude must not re-do this.
- **In progress** — how far it got, where it stopped, and the next concrete action.
- **Not started** — remaining scope.

### Key decisions

Each entry: the decision, its rationale, and the alternatives rejected and why. For genuine trade-offs keep the full shape — what was weighed against what, which constraint tipped it, and what would have to change for the rejected option to win. Mark decisions the user explicitly made as **"(user decision)"** — the successor must not overturn them unilaterally.

### Approaches: proven and ruled out

- **Worked** — approaches/techniques verified this session and worth reusing, with where they were applied.
- **Ruled out** — what was tried and why it failed, each with the concrete evidence (error message, failing output). "Didn't work" without the why gets retried anyway. Note conditions under which a ruled-out option would be worth revisiting.

### Files to read first

Pre-curate the entry points so the next Claude avoids bouncing around. List the closest-analog existing files ("`Roster.tsx` is the closest pattern to model the new page on") with a one-sentence "what to look for" each.

### What the data looks like

If the workstream consumes structured data (an API response, a SQLite schema, a state file), include a sample or a one-shot command to fetch one. Don't assume the next session knows the shape. (Omit for pure refactors.)

### Background and constraints

What the successor cannot learn from the code or git history: business context, external dependencies, and — each in its own entry — the corrections and preferences the user gave during the session. Include hidden constraints (env vars that must be set, services that must be restarted), patterns that look duplicable but aren't, timing concerns, and things that look like bugs but aren't. In-session corrections are exactly what a fresh agent will get wrong again.

### Next steps

A priority-ordered action list. Item 1 is a specific opening move — not "explore the code" but the file to read, the function to write, or the command to run (reduce session-start decision-paralysis). Each item carries what's needed to start immediately: file paths, commands, expected outcome.

### State at handoff

A snapshot for orientation (grounded in the three fenced probes above):
- Repo HEAD commit + working-tree status (clean / what's uncommitted)
- Running services + uptimes (service manager, scheduled jobs)
- Persistent data of note (DB row counts, file mtimes if relevant)

### What this handoff does NOT do

Explicitly mark out-of-scope items so the next session doesn't drift into them ("improve the citation verifier — minor, can wait"; "user-tuning tasks, not engineering").

### Key references

Core file paths, related tickets / PRs / wiki links, and common commands (how to run tests, start the environment, reproduce the problem).

## Writing discipline

- **Self-contained**: no "as mentioned above", "the file we discussed", or any reference that needs the conversation — replace each with a concrete path, commit hash, or absolute date. Reread the finished doc: any passage that needs the conversation to make sense is a defect.
- **Pointers over pasting**: locate code with file:line and commit hashes; inline only short, decision-critical fragments (error messages, key diffs).
- **Report faithfully**: failing tests get the failure reason and an output excerpt; skipped steps are stated as skipped; nothing unverified is dressed up as done.
- **Select ruthlessly — but compress narrative, not decisions**: the test for every line is "does this change the successor's next action?" — if not, cut it. Cutting applies to process narrative (what was tried in what order, tool-call play-by-play); it never applies to decision substance — trade-off rationale, rejected alternatives, constraint details, and exact parameter/threshold values survive at full fidelity, because they are precisely what the successor cannot reconstruct.
- **Match the memory-file style**: tight, specific, no preamble. Code blocks for commands, tables for option grids, dashes for lists. Overspecify file paths; underspecify narrative.

## After writing

1. Print a 1-line confirmation with the file path.
2. Provide a paste-ready one-line resume prompt, e.g.: `Read handoff_<slug>_<YYYY-MM-DD>.md first, then continue with item 1 under "Next steps".`
3. If `MEMORY.md` was updated with the pointer, mention that.
4. Suggest the user run `/clear` and start a fresh session pointing at the handoff.
