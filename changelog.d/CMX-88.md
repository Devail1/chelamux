### Added

- **`chela judge sweep`: a mechanical mutation pass over every conditional the branch
  changed.** For each changed line in `.py`/`.js`/`.mjs` (not tests) vs `--base` (default
  `origin/<workspace.base_branch>`), it rewrites `if X` to `if False and (X)` (plus the
  `True or (X)` twin when there is an `else`), swaps a return/`CompletedProcess` code
  `0`↔`1`, swaps `return None`↔`return <expr>` in a function that has both, and flips
  `==`↔`!=`, `<`↔`>=`, `>`↔`<=`. Each mutation goes through the judge's own
  apply/read-back/parse/per-mutation-selection mechanics and is restored in a `finally`. A
  recovery marker (`.chela-sweep-recovery.json`) means a killed sweep is undone on the next
  start. It exits 1 on any survivor. A mutation that does not parse is reported as skipped,
  never as killed.
- **`chela task-finished` runs the sweep and refuses while a survivor remains.** To accept a
  survivor, the agent names it with a reason:
  `--sweep-equivalent '<file:line>: <why no outcome can change>'`. The note is stored in
  `runs.sweep_equivalents`, shown to the judge and appended to the PR body. A note that names
  a line which did not survive is refused. The judge itself is unchanged. (CMX-88)
