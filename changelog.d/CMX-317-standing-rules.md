### Added

- **Standing agent rules.** A workflow can name a short rules file with
  `agent.standing_rules: <path relative to the repo>`, and the prompt body renders it
  wherever `{{standing_rules}}` appears; the built-in rework prompt carries it too. A missing or
  unreadable file renders as nothing and logs a warning instead of failing the dispatch.
  chelamux seeds `docs/AGENT_STANDING_RULES.md` with the rules the orchestrator used to
  repeat to each agent by hand. (CMX-317)
