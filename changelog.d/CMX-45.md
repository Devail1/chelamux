### Security

- **A dispatched agent or judge pane now inherits an allowlisted environment, not the
  daemon's.** It used to unset only `CHELA_NOTIFY_URL` and inherit everything else the tmux
  server held — every `chela.env` setting, pm2's bookkeeping, the operator's shell vars. Every
  inherited name (the daemon's env plus the tmux global and session tables) that is not on
  `envutil.AGENT_ENV_ALLOW` is now unset in the pane before the agent starts. Extend the list
  with `CHELA_CHILD_ENV_FORWARD`; `CHELA_NOTIFY_URL` stays dropped even when forwarded. See
  TRUST §2 and `docs/CONFIG.md`. (CMX-45, #652)
