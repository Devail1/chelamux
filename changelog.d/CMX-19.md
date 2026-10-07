### Fixed

- **A docs-only PR is parked once for a human instead of being judged four times.** When
  every file a PR changes is documentation (under `docs/` or `changelog.d/`, or a
  top-level `*.md`/`*.rst`/LICENSE-style file other than `WORKFLOW.md`, `CLAUDE.md` or
  `AGENTS.md`), the dispatcher no longer spawns a judge. It moves the run to `needs_human`
  in one write ("docs-only — nothing to verify; needs a human OK / `chela merge
  --override`") and the inbox sends one notice. A `.py` or other non-prose file under
  `docs/` still counts as code, as does any PR that changes code alongside docs. The fix
  also covers judges that do run: a `cannot_verify` that re-running the same commit cannot
  change (docs-only, no `judge.test_cmd`, a deletion-heavy diff) now parks the run the same
  way. Before, it used up the `judge_max_unknown_retries` retry budget, sending one notice
  per attempt. Flaky unknowns are still retried. A parked run is still not mergeable without
  a clean judge or a human override. (CMX-19)
