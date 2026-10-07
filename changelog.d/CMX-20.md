### Changed

- **The dashboard's polled endpoints no longer probe tmux and `pgrep` once per window.**
  `/api/agents`, `/api/agents/context` and `/api/orchestrator/status` now read one shared
  pane snapshot (`sessions.panes()`: one `tmux list-windows` plus `/proc`, cached ~1 s
  across requests and tabs) and a shared tmux epoch, instead of 2–4 spawns per window per
  poll. A window that is missing from the snapshot forces one fresh read, so new windows
  still show on the next poll. The responses are unchanged: a golden test renders 5 and 20
  real windows both ways and requires byte-identical output. On a host without `/proc`
  (macOS), one `ps -axo` (plus one `lsof` for the claude cwds) now replaces the per-process
  `pgrep`/`ps`/`lsof` calls in `sessions.panes()`. Measured with the CMX-15 harness (dashboard
  alone, one phone tab on the Wall) at 5 / 20 / 40 windows: dashboard CPU 7.0 / 23.2 / 59.9% →
  2.7 / 4.1 / 7.6% of a core, `/api/agents` p50 98 / 388 / 1,044 ms → 20 / 38 / 117 ms. (CMX-20)
