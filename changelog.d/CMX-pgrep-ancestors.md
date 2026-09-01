### Fixed

- **`chela doctor` no longer goes blind to the window it is run from.** macOS `pgrep`
  excludes the calling process *and all of its ancestors* from the match list unless `-a`
  is passed (`pgrep(1)`), and a chela CLI invoked from inside an agent window has that
  window's own `claude` as an ancestor — so `pgrep -P <pane_pid>` returned nothing and the
  window resolved to `claude_pid=None`. It then dropped out of every population keyed on
  `claude_pid`, including `doctor`'s peer-messaging check, which under-reported by exactly
  the window the operator was sitting in: run from inside a fleet window it listed one
  window without a peer socket, run from outside it listed two. The daemon was never
  affected — it descends from no agent — so the gap only ever appeared in self-diagnosis,
  which is where an operator is most likely to look.
