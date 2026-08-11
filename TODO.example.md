---
# This is the TEMPLATE for a chela dispatch tracker. Copy it to `TODO.md`
# (which is gitignored — your queue is per-install, not shipped) and add your
# own work items. `WORKFLOW.md` points at `TODO.md` as its tracker; the
# dispatcher reads it from disk, so it never has to be committed.
#
#   cp TODO.example.md TODO.md
#
# Then dispatch:  chela dispatch ./WORKFLOW.md --dry-run   (preview)
---

# TODO

The chela dispatcher claims every unchecked `- [ ]` item **anywhere in this file** —
headings like **Open** below are organizational only; the parser does not look at what
section a line sits under, so moving a task under a different heading does not hold it
back. To park a task unclaimed, mark it `<!-- blocked: ... -->` (or make it wait on
another task with `<!-- depends: "..." -->`) — see `examples/TODO.md` for both markers.
Each claimed task runs as an isolated git-worktree agent (adversarially reviewed by the
judge), and strikes `- [x]` once its PR merges. This file is your **live queue** — it is
per-install and **gitignored**, so it never ships to anyone who clones the repo and never
churns their tree.

Each item is a four-field brief the judge can enforce mechanically:

- **OBJECTIVE** — what to build and why.
- **BOUNDARIES** — files/scope it may touch; what not to regress.
- **GUARDS** — tests that must go **RED** when their invariant is corrupted (a guard that
  survives its own corruption is decoration).
- **VERIFY** — how to confirm the result, including anything that needs a manual check.

## Open — CI drives the loop

- [ ] **EXAMPLE — replace me with a real task (delete this line once you have your own).** A one-paragraph statement of what to build and why the change is worth making.

  **OBJECTIVE.** The concrete change: which function/file/behavior, and the end state.

  **BOUNDARIES.** The files/scope the agent may touch; what it must NOT regress. `PR → dev` (or your base branch).

  **GUARDS (test framework; corrupt→RED).**
    - An assertion that fails when the invariant is broken. Describe the corruption that must turn it RED.

  **VERIFY.** How a human confirms it works end-to-end, including anything a source-parse can't check.
