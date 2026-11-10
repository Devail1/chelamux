### Fixed

- **A restored session comes back with the launch flags and env it ran with.** chela now
  records each live session's extra CLI flags (an allowlist: `--channels`, `--model`,
  `--permission-mode`, `--add-dir`, `--plugin-dir`, `--agent`, the permission-skip switches),
  allowlisted env vars (`DISCORD_STATE_DIR`, plus any names in the new `CHELA_RESTORE_ENV`)
  and its added PATH entries in `$CHELA_DIR/launch-settings.json`, keyed by session id. Both
  `chela restore --resume` and the boot pass after a tmux restart replay them. A
  secret-shaped env name is never recorded. If the replayed launch does not come up (a flag
  Claude Code no longer knows, or a channel plugin that is no longer installed), the session
  comes back with a plain `--resume` and the reason is logged. (CMX-372)
