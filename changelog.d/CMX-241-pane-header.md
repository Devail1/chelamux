### Changed

- **A plain shell pane says it is a shell.** A shell opened from the dashboard showed its tmux
  window name (`liavedunix-2`) and an `idle` chip, so it looked like an idle Claude session.
  Until Claude starts in it, its pane header now reads `Shell · <cwd>` with a `shell` chip;
  once `claude` runs there, the header shows the session's name and state as before. The tmux
  window is never renamed (its name feeds the Telegram topic and `--remote-control`).
  `/api/agents` carries the pane's cwd as `shell_cwd` (home written `~`) while no Claude runs
  in it. (CMX-241)
- **The Wall's pane header is React** (CMX-212 P26): the title and rename, the state pill and
  status dot, the web chip and UNSANDBOXED banner, the ⋯ menu (Wire, Share, Orchestrator, Pin,
  now keyboard-reachable), minimize, maximize, the phone's fill-screen button and kill with its
  confirm. Its share-mode marks are icons instead of emoji. (CMX-241)
