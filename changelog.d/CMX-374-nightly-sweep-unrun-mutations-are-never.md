### Fixed

- **The nightly sweep no longer forgets the mutations its budget never reached.** Each UNRUN
  mutation (file, line, kind, the PR it came from) is now owed, in
  `$CHELA_DIR/sweep-nightly/owed.json`. The next night measures every owed mutation first,
  inside the same budget, and still runs them on a night with no new merge. An owed
  mutation leaves the set only once it has run (killed or survived), or once its line is no
  longer on the base branch; each drop is logged to `owed-dropped.jsonl` with the reason.
  The inbox notice says "N owed from earlier nights", how many ran, and which were dropped
  and why. (CMX-374)
