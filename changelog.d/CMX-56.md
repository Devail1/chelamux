### Fixed

- **Settings → Update flags a service as stale only when code it runs changed.** Before,
  any new commit marked every running `chela-*` service stale: a dashboard-only change
  flagged `chela-daemon`, `chela-telegram` and `chela-agent-terminals`, none of which run
  that code. Now each service's code set is worked out from its import graph, launcher
  scripts and data files, and compared with what changed since the commit the service
  started on. A service whose start commit can't be read is shown as "unknown". The same
  change fixes the `repo.services_current` doctor fact and the restart-only path of
  `chela update`.
- **"Update now" no longer does nothing when there is nothing to pull.** If services are
  stale, the button now reads "Restart stale services (N)" and restarts exactly those. It
  waits while a dispatched run is in flight, like the update path does. Every click now
  shows a message: "already up to date", "restarting X, Y", or the error. The card's text
  also wraps properly on a phone. (CMX-56)
