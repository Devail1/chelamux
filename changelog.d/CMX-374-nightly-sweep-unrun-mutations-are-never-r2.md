### Fixed

- **The nightly sweep no longer forgets the mutations its budget never reached.** Each UNRUN
  mutation (file, line, kind, the PR its line came from) is now owed, in
  `$CHELA_DIR/sweep-nightly/owed.json`. The next night measures the owed ones first, inside
  the same budget, and still runs them on a night with no new merge. An owed mutation leaves
  the set only once it has run (killed or survived; a survivor whose filing failed stays
  owed) or its line is gone from the base branch. The inbox notice says "N owed from earlier
  nights", how many ran, which were dropped because their line is gone, and how many are
  owed to the next night. Since nothing UNRUN is lost any more, a night whose budget ran out
  now moves the covered window on. (CMX-374)
