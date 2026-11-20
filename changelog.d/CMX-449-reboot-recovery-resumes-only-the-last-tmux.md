### Fixed

- **A reboot that restarts tmux twice brings back the whole fleet, not just the last few
  windows.** The boot restore pass used to resume only the epoch right before the current
  one. When that epoch lived only seconds (tmux restarted, came up with two windows, and
  restarted again), the real working set in the epoch before it was skipped. The pass now
  walks back past every ended epoch the roster saw live for less than
  `CHELA_RESTORE_SHORT_EPOCH_S` seconds (default 900; `0` turns the walk off). It resumes
  the union of those epochs, one launch per session from its newest epoch, and skips any
  session that is already live. Its inbox notice names the epochs it walked past and why,
  and the sessions it skipped because they were already live. A restore row with a session
  but no cwd on record now takes the cwd from that session's own transcript, so `chela
  restore` no longer lists it as "no cwd/session on record". (CMX-449)
