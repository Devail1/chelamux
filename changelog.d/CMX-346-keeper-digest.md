### Added

- **`chela keeper digest <WORKFLOW.md>` prints what happened in a workflow's repo since the
  last digest.** For each live session launched in the repo or one of its worktrees it lists
  the window, its live session id, busy/idle, the latest recap, the prompts typed and the
  files edited. It also lists the commits on any branch, the PRs opened or merged, the issue
  ids mentioned anywhere, and every open issue on the workflow's tracker. It uses no LLM and
  never writes to the tracker. Each section is capped and says how many rows it dropped. A
  git, `gh` or tracker read that fails is shown as unreadable, never as empty. A cursor in
  `$CHELA_DIR/keeper/` makes the next run start where this one ended; `--since 90m`
  overrides it, and `--json` is the input for the next keeper slice. (CMX-346)
