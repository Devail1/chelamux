### Added

- **`--pause --all` can also stop your own PM2 services.** Name them under
  `pause_all.extra_services` in `~/.chela/config.json`, with an optional
  `pause_all.resume_order`. The pause runs `pm2 stop` on each one that is up, and every
  resume (CLI, dashboard, Telegram `/resumeall`, or the hold's TTL running out) starts them
  again one at a time in that order. A service that was already down stays down, and
  `--hold-status` and the dashboard list each service's state. (CMX-319)
