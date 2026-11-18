### Changed

- **A frontend PR no longer needs a "merge dev" round because another one merged first.** Merge
  the base with `chela merge-base <task-id>`: a conflict only in the committed dashboard bundle
  is rebuilt from the merged source and committed, and a source conflict beside it leaves only
  the source files for you. `chela task-finished` rebuilds a bundle-only conflict itself, before
  and during its checks, instead of refusing the hand-in. (CMX-437)
