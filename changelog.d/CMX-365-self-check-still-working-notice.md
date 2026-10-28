### Fixed

- **The "self-check still working" notice no longer fires on every normal hand-in.** Its
  ceiling was a fixed 1h, but a normal self-check with a 2400 s budget routinely runs past
  that. Unless `CHELA_SELFCHECK_ALERT_S` is set, the ceiling now comes from the run's own
  WORKFLOW.md: the larger of `judge.self_check_budget_seconds` and
  `judge.sweep_budget_seconds`, plus `judge.suite_timeout_seconds`, times 1.5. That is 2h with
  the current budgets. When the workflow cannot be read, the ceiling is 90 minutes. The
  re-notify interval is unchanged. (CMX-365)
