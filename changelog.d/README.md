# Changelog fragments

`CHANGELOG.md`'s `## [Unreleased]` section used to be the one place every PR appended its
own entry — and every concurrent `cmx-N` branch collided on that exact same spot, the same
shape `docs/DEFEAT_SHAPES.md` hit and fixed the same way (CMX-284/293, one file per shape
instead of one shared append-only list). `.gitattributes`' `merge=union` driver (CMX-241)
only smooths over a *local* git merge — GitHub's own PR-mergeability check does not run
custom merge drivers, so two open PRs each touching `## [Unreleased]` still show
`CONFLICTING` there, get **no CI checks at all** (GitHub can't compute a merge commit), and
every round either branch spends is unverifiable until a human drops one side's entry.
Measured on CMX-308 and CMX-309 (2026-08-18) — CMX-309 alone spent five rounds stuck there.

**Never edit `CHANGELOG.md` directly in a PR.** Instead, add one new file here:

```
changelog.d/CMX-<task-id>.md
```

— named after your own CMX task number (e.g. task `CMX-312` → `changelog.d/CMX-312.md`),
**not** a guess at "the next free number": two files with different names never collide, so
there is nothing to guess. Its content is exactly what used to go under `## [Unreleased]`:
one or more Keep a Changelog category headings, each with one or more bullets.

```markdown
### Added

- **A one-line, bold summary.** The rest of the entry: what changed, why, and what a
  reader should do differently. (CMX-312, #123)
```

At release time, `python -m chela.release_notes --release X.Y.Z` collects every fragment
here (in filename order), merges same-category headings the same way concurrent
`## [Unreleased]` entries always were (`chela.release_notes._merge_duplicate_subheadings`),
promotes the combined body into a new `## [X.Y.Z] — YYYY-MM-DD` section in `CHANGELOG.md`,
resets `## [Unreleased]` to empty, and deletes the fragment files it consumed — see
"Releasing" in [CONTRIBUTING.md](../CONTRIBUTING.md). This file (`README.md`) is never
collected as a fragment.
