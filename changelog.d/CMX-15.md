### Added

- **A profile of chela's services under normal load (`docs/perf/2026-10-profile.md`).**
  The dashboard spends most of its CPU starting processes. About 90% of its tmux and pgrep
  spawns come from `/api/agents` and `/api/agents/context`, which probe each window
  separately every 4 s per open Wall tab. The cost grows linearly: 7% of a core at 5
  windows, 73% at 40. The report ranks three fixes: batch the probes through the existing
  `sessions.panes()`, remove a 36-second CPU burst in `chela doctor`, and have Telegram
  capture only panes that changed. It also lists what is not worth doing, including
  swapping Werkzeug for waitress. The isolated harness that produced the numbers is in
  `docs/perf/2026-10-harness/`. No production code changed. (CMX-15)
