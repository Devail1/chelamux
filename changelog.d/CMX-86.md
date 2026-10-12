### Changed

- **The orchestrator's inbox stops echoing it and stops announcing "awaiting review".** A
  `chela reopen`/`retry`/`close`/`merge` or `chela dispatch --resume` run by the orchestrator
  itself no longer comes back to it as a notice; the same action by a human (terminal,
  dashboard, Telegram) still does — a human's reopen is now `run_reopened`, and a close by
  anyone else (or a PR closed on GitHub) is `run_was_closed`. The plain "awaiting review"
  notice is silent by default (still logged for the Feed and `chela wait`); set
  `CHELA_INBOX_NOTIFY_AWAITING_REVIEW=true` to bring it back. (CMX-86)

### Added

- **The orchestrator now hears what it used to find by hand.** One inbox notice each, never
  repeated on a later tick: a run waiting in review with no judge round started past
  `CHELA_JUDGE_NOT_STARTED_ALERT_S` (default 20m); a dispatch hold that lapses by its TTL or
  is released by someone else; CI red on the PR's head, naming the failing job, the first
  failing line and the `gh run rerun … --failed` command (the rework the CI loop sends is no
  longer a second notice); a PR that turns CONFLICTING with its base (once per head); a
  running agent with no commit and no transcript activity past `CHELA_STUCK_AGENT_ALERT_S`
  (default 20m); and a task-finished / push-request marker left unapplied past
  `CHELA_MARKER_UNREAD_ALERT_S` (default 5m), with the daemon's uptime. (CMX-86)
