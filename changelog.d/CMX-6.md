### Added

- **"New task" in the dashboard creates a Linear issue.** On a workflow whose tracker is
  `kind: linear`, the Work view's toolbar (and a ⌘K palette row) opens a form: title,
  markdown description, priority and optional "blocked by" (open issues). Submit creates
  the issue in the workflow's team, in its ready state, plus a `blocks` relation per
  blocker. It is never created as a sub-issue. The browser posts to
  `/api/dispatcher/linear/issue`, and only the dashboard calls Linear, so `LINEAR_API_KEY`
  never reaches the page. Share guests are refused. A Linear error is shown and the typed
  form is kept. Open Linear cards on the board now link their id to the issue
  (`CMX-N ↗`). (CMX-6)
