### Added

- **chela records which session launched which.** `chela spawn` run from inside a session
  records that session as the new one's parent; a session with no launch record whose first
  cross-session message arrives within 10 minutes of its start (`CHELA_PARENT_WINDOW_MINUTES`)
  gets that message's sender. Each parent is set once, never changed, cycles are refused and
  nesting stops at `CHELA_PARENT_MAX_DEPTH` (3). Dispatched runs stay out. Parents are keyed
  by session id in `$CHELA_DIR/session-parents.json`, so they survive a tmux restore, and
  `/api/agents` rows carry `parent_session_id` and `parent_source`. `chela parents backfill`
  derives parents for existing sessions from the event log (a dry run that prints the tree;
  `--apply` writes it). (CMX-389)
