### Fixed

- **A window whose agent moved to a Claude Code background session no longer reads
  "unknown" / "Done".** The pane's own `claude` has no `claude agents --json` entry after the
  move; chela now follows the entry that descends from it (background first, then newest) for
  status, cwd and transcript across the Wall, sidebar, `chela peek`, collab and needs-input
  pushes. `chela peek` and `chela status --sessions` show the background session's name — the
  one ListAgents/SendMessage need. (CMX-28, #612)
