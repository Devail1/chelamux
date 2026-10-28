### Fixed

- **A merged or closed run no longer leaves its judge behind.** When a PR merged (an
  operator-approved `chela merge --override` included) or closed while its judge was still
  running, the daemon left that judge alone as long as its tmux window existed. A window
  holding only a bare shell counted too, so it stayed open long after the run was done and
  was mirrored into every webterm session. Now the tick that reconciles the run to `done`,
  `closed` or `failed` stops its judge the same way a send-back does: the round is
  cancelled, the detached run is sent SIGTERM, the judge window is killed and its worktree
  removed. A judge on a merged PR therefore no longer gets to record a blocked-race verdict
  after the merge. (CMX-336)
