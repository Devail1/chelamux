### Added

- **A push when an agent is blocked by its permission classifier.** An auto-mode denial
  never shows a prompt, so the session never reaches `waiting` and the needs-input push
  stayed silent. `notify.DeniedWatch` now tails `hook.permission_denied` events and sends
  one ntfy push per window per 10 minutes, from whichever process holds the announcer
  lease (daemon or dashboard). (CMX-25, #609)
