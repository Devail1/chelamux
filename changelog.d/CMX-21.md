### Fixed

- **An agent can no longer kill or silently re-create the live tmux server.** chela's
  `PreToolUse` hook (plugin 0.2.7) now denies `tmux kill-server`, and any `kill-session`
  aimed at chela's session, unless the command pins a private socket with `-L`/`-S`. A
  `TMUX_TMPDIR=` prefix does not count, because `$TMUX` overrides it inside a pane. The
  self-heal in `scripts/agent-terminals.sh` and `chela.discovery.ensure_session` now starts
  the server from a scrubbed env (no Claude session markers, proxies, `PERF_*`) and unsets
  the same set from the new server's global env. It refuses to heal when `TMUX_TMPDIR`
  names a missing dir, since tmux would fall back to the default socket. A supervisor
  whose cwd was deleted exits instead of healing. (CMX-21)
- **Agent windows no longer inherit a parent Claude session's markers.** `child_env()`
  drops `CLAUDECODE`, `CLAUDE_CODE_SESSION_ID`, `CLAUDE_CODE_CHILD_SESSION` and the other
  markers. Every launch path also scrubs them, along with proxies and `TMUX_TMPDIR`, from
  tmux's global env, so a pm2 service that carries them no longer turns agent transcripts
  off. An operator behind a real proxy can name it in `CHELA_CHILD_ENV_FORWARD`. (CMX-21)

### Added

- **`chela doctor` fact `tmux.leaked_env`.** It goes red when tmux's global env holds a
  proxy aimed at a dead localhost port, a Claude session marker, or a `TMUX_TMPDIR` whose
  dir is missing. The daemon's doctor sweep pushes it once through the notify path.
  (CMX-21)
- The perf harness (`docs/perf/2026-10-harness/`) now passes `tmux -L chela-perf` on every
  call. It refuses `kill-server` on the default socket, starts the ttyd supervisor under
  `env -i` with an allowlist inside its own process group, and `stop.sh` reaps that whole
  group. (CMX-21)
