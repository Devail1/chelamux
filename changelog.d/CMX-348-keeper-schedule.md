### Added

- **Keeper mode runs on its own, and a workflow can be keeper-only.** A WORKFLOW.md with
  `keeper: {enabled: true, every: 45m}` gets one `chela keeper run` every `every` from the
  daemon. A run is skipped while the queue is held, while the previous one is still going,
  and when no session in the repo typed a prompt or edited a file since the last run.
  `dispatch: {enabled: false}` (or `dispatch: false`) makes the workflow keeper-only: the
  dispatcher claims, reconciles and judges nothing for it, auto-merge and the nightly sweep
  skip it, and it is removed from the merge gate's registry, so your own sessions can push
  to its base. A bad value for either key fails the parse, so the last good config stays
  in force. `chela keeper stats [--since 7d] [--json]` reports proposals made, accepted,
  rejected and dropped, the acceptance rate, which is flagged under the 70% kill line, and
  a by-action breakdown. (CMX-348)
