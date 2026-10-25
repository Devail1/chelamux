### Fixed

- **The Decisions dialog keeps its history when agents are busy.** Every non-hook event
  (merges, send-backs, judge verdicts, stuck notices) is also written to a small
  `events-core.jsonl`, and an event-log read that asks only for non-hook types is served
  from it. Before, the hook firehose rolled the 8 MB log every ~7 h and the dialog said
  "No decisions logged yet" after a day full of them. `chela events --type …` and the ops
  scan read the same core log. On the first daemon start, the core log is seeded from the
  rolled files. (CMX-328)

### Changed

- **Hook events store a preview, not the tool's body.** Each `tool_input` /
  `tool_response` value keeps 200 characters, and `clipped_bytes` records the original
  sizes, so a 1 MB Write is a ~1 KB line instead of a ~2.5 KB one. An `AskUserQuestion` /
  `ExitPlanMode` input is still kept whole for the Telegram gate. (CMX-328)
