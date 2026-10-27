### Added

- **A notification digest with interrupt rules.** Set `digest.enabled` in
  `~/.chela/config.json` and only needs-you events (a run that needs a human, an override or
  release waiting for approval, a failed run/merge/release, CI still red on known flakes
  after its re-runs, a low-memory kill, a refused permission) are pushed to the orchestrator
  at once. Everything else is held for a digest sent at `digest.times` (default 09:00 and
  16:00) and on demand with `chela digest`: needs you (at most 3), waiting on others, done
  since last, changed — at most 150 words, a link on every line. Held events are still in
  `chela events` and the dashboard. Off by default; `digest.interrupt` replaces the default
  list of kinds that interrupt. (CMX-320)
