### Fixed

- **A window's context bar and live cost show that window's own session.** The statusLine
  hook names the cache file after the window resolved from `$TMUX_PANE`, which every process
  launched from a pane inherits. A Claude Code background session therefore overwrote the
  window's cache, and the bar showed its numbers (69%, $43) instead of the interactive
  session's (9%, $0.8). The hook now also writes `context/by-session/<session_id>.json`.
  `/api/agents/context` and `/api/cost?window=live` read the session the window's own claude
  process is running. The transcript fallback reads that window's own transcript, not the
  newest one in its working directory. Stale per-session files are pruned on the
  snapshot-history schedule. (CMX-29)
