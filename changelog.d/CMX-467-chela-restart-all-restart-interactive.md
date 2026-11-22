### Added

- **`chela restart`: restart sessions in place onto a new Claude Code.** After a Claude Code
  update, `chela restart @N` exits an idle session (`/exit`, waiting up to 120 s) and relaunches
  the same session with its recorded flags, name, Remote Control and Telegram topic. `--all`
  restarts every interactive session one at a time: busy ones are retried at the end, the
  orchestrator goes last and is registered again, and dispatched runs and judges are never
  touched. `--self` restarts your own session through a detached helper once it goes idle.
  The report gives the old and new window, the fallback step used, and the topic. On the
  dashboard, the update banner offers "Restart onto the new Claude Code" when sessions run an
  older version than the installed one, and each session row's menu has "Restart session".
  (CMX-467)
