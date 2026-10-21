### Added

- **A nightly mutation sweep over what the standard tier merged unjudged.** Once a day
  (WORKFLOW.md `sweep.nightly_at`, default `02:00` local; `off`, `never`, `false` or
  `sweep.enabled: false` turns it off) the daemon spawns `chela judge nightly-sweep`: one battery, one at a time,
  outside the dispatch slots and in the judge's shared memory slice. It sweeps the lines on
  `origin/<base>` that still blame to a STANDARD-tier commit from the last 24 h (a core-tier
  PR was judged already) and files ONE tracker issue per surviving guard, labelled
  `risk:normal` and `sweep`, with every surviving mutation and every same-shape site
  (`git grep -F`). A survivor already filed is never filed again: its key lives in
  `$CHELA_DIR/sweep-nightly/filed.json`. Each run sends one inbox notice (filed / clean /
  skipped and why). It is skipped while `chela dispatch --pause --all` is active. Its
  budget is `sweep.budget_seconds` / `sweep.max_mutations`, else the judge's sweep budget.
  The Linear tracker's `create_issue` takes label names. (CMX-299)
