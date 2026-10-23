### Added

- **The nightly mutation sweep's scheduler (1 of 3).** Once a day (WORKFLOW.md
  `sweep.nightly_at`, default `02:00` local; `off`, `never`, `false` or `sweep.enabled: false`
  turns it off) the daemon spawns `chela judge nightly-sweep`: one at a time, outside the
  dispatch slots and in the judge's shared memory slice. It fetches `origin`, reads
  `origin/<workspace.base_branch>`, and sweeps the lines that still blame to a STANDARD-tier
  commit merged in the last 24 h (core, always-human and prose merges are not swept). After a
  night that could not verify (or left mutations unrun), the next one catches up from the last
  night that did, at most a week back. Each surviving guard is handed to a filer once; the filed keys live in
  `$CHELA_DIR/sweep-nightly/filed.json` and survive restarts. Until the filing slice lands,
  the filer files nothing and records what it would have filed in
  `$CHELA_DIR/sweep-nightly/would-file.jsonl`. It is skipped while
  `chela dispatch --pause --all` is active. Its budget is `sweep.budget_seconds` /
  `sweep.max_mutations`, else the judge's sweep budget. (CMX-303)
