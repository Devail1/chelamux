### Added

- **`chela spawn <cwd> [--command CMD]` opens an agent window from the terminal.** It runs
  through the same `spawn_window` path as the dashboard launcher and Telegram `/new`. With no
  `--command` it launches `$CHELA_AGENT_CMD` (default `claude`), not the bare shell that
  `spawn_window` opens on its own. It prints the new `@N` and cwd, and adds the cwd to the
  launcher's Recent list on a best-effort basis. (CMX-13)
- **`chela close @N` kills one chela window, so nobody has to reach for raw
  `tmux kill-window`.** It refuses a window that is not live in the chela session, the
  orchestrator's own window unless `--orchestrator` is passed, and a window that an
  in-flight dispatched run still claims unless `--force` is passed. For that last case,
  `chela close <task> --reason …` is the right tool. Closing a run by id works as before.
  (CMX-13)
