### Changed

- **One green baseline per tree, shared by self-check, sweep and judge.** A green baseline's
  JUnit report is cached under `$CHELA_DIR/baselines`, keyed by the working tree's sha, the
  test command and its deselect set. The sweep reuses the self-check's run, and the judge's
  first round reuses the hand-in's when its head is the same tree, which saves about 7
  minutes each. A miss, an unreadable entry, a red run, a flake-rescued run or a tree that
  changed mid-run all run the suite as before. Reuse never crosses tree shas, and the judge
  still provisions its worktree first. (CMX-488)
