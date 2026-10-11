### Changed

- **`chela merge --override` no longer waits — it requests, and the approval merges.** The
  command (or its request-only spelling, `chela override request cmx-N --reason "…"`) runs the
  gate minus the judge, records one request bound to the PR's head SHA, prints the request id
  and exits 0. When the operator approves — a one-tap **Approve** button in Telegram, the
  dashboard's `/override/<id>`, or `chela merge-approve <id>` in a plain terminal — the merge
  completes by itself through the normal merge path, after re-checking that the head is
  unchanged, CI is green and the PR is mergeable. Any miss ⇒ no merge, the request is marked
  failed/expired with the reason, and the operator is told. A new push expires a pending
  request; `CHELA_OVERRIDE_WAIT_S` is now its safety TTL (default 24 h, was a 300 s wait).
  (CMX-79)

### Added

- **Telegram Approve / Deny for override requests.** Each request is posted once to the
  orchestrator's topic (task, PR link, judge state on the head, reason) with inline buttons,
  and the message is edited to the outcome: merged `<sha>` / denied / expired / failed.
  `chela override list [--all]` shows requests. (CMX-79)

### Security

- An override approval stays human-only: `chela merge-approve` refuses in a process carrying
  `$CHELA_ACTOR` or a Claude Code session marker, and the merge-gate hook (plugin 0.2.8) also
  denies a session's `python` call into `chela.overrides.decide`. (CMX-79)
