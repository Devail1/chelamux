### Fixed

- **A dispatched run can no longer launch another Claude Code agent.** Agents and judges run
  unsandboxed, and a dispatched agent routed around a classifier denial by relaunching
  `claude` in another permission mode on a private tmux server. The dispatcher now exports
  `CHELA_RUN_ID` / `CHELA_RUN_ROLE` into every pane it spawns, and the plugin's `Bash` hook
  denies a `claude` launch there, however wrapped (`tmux new-session "…"`, `env`, `nohup`,
  `timeout`, `bash -c`, `npx @anthropic-ai/claude-code`, …). `claude --version`,
  `claude plugin test|validate` and `claude mcp list` stay allowed; human sessions and the
  orchestrator are unaffected. Plugin 0.2.11. (CMX-190)
