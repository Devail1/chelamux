### Fixed

- **After a headless reboot, sessions now come back without anyone opening a terminal.** PM2
  started every chela service at boot, but nothing started a tmux server, so the restore boot
  pass had no tmux epoch to act on and waited until someone opened a terminal. The daemon now
  starts an empty tmux server on every tick that finds none. It uses the pinned session and no
  `TMUX`/`TMUX_PANE`, and sets `exit-empty off` so the server stays up with no session. The
  restore then runs as it does after a tmux restart, and its first relaunched window creates the
  `chela` session. No placeholder shell is created, and a server that is already running is left
  alone. (CMX-510)
- **The restore report lists a session that came back as resumed.** A session that was relaunched
  and verified alive was listed under "not resumed: … row moved on before it could be removed"
  whenever its old `session-ids.json` row had already been rewritten. It is now listed as resumed,
  with that note. "Not resumed" now means the session really did not come back. (CMX-510)
