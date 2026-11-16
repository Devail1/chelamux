### Fixed

- **A classifier that gave no verdict no longer reads as a denial.** When Claude Code's auto
  mode classifier fails to answer ("gave no verdict (error) … a transient failure of the
  check"), chela used to send the orchestrator the 🔒 "blocked by its permission classifier"
  notice and a phone push, both telling it to hand the human a command. That is an outage, and
  the right response is to wait and retry. A no-verdict now records a silent
  `classifier_unavailable` event in the log instead ("classifier unavailable — agents
  retrying"), with no notice and no push. A real denial still gets the 🔒 notice. If an agent's
  turn stopped on the no-verdicts and it sits idle, chela re-prompts it to resume as soon as
  another tool call shows the classifier answering again (logged as `classifier_resumed`),
  instead of leaving it for the stuck alarm. (CMX-384)
