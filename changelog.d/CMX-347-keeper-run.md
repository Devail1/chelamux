### Added

- **`chela keeper run <WORKFLOW.md>` proposes board changes from the digest, and a person
  approves them.** It builds the keeper digest, asks one headless `claude -p` call (no
  tools) for proposals, and checks each one in code: a proposal must cite evidence that is
  in the digest and quote it, and each item it cites must be about that issue (it names
  the id or quotes a word of the title), or it is dropped as `evidence_unrelated`. It must
  also use an allowed action: `in_progress` only from Todo or Backlog, `comment`,
  `new_backlog_issue`, `flag_stale` only for an In Progress issue that nothing in the
  window names, and `done` only when it cites a merged PR that names the issue. The survivors go to the inbox as ONE item.
  `chela keeper approve <batch> [--only 1,3]` applies them to the tracker, each with a
  "chela keeper" attribution comment naming the approver and the evidence;
  `chela keeper dismiss <batch>` writes nothing. Every proposal and its outcome is a row in
  `$CHELA_DIR/keeper/log.jsonl`. The Linear source can now add comments. (CMX-347)
