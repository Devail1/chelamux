### Fixed

- **Restore no longer resumes a session that is already running in another window.** A window
  that switched to a session with `/resume` inside a running Claude was not seen as running it,
  because restore read only each process's launch command (`--resume <id>`). Restore now asks
  every live window which session it is running right before each launch, from the hook record
  or Claude Code's own session registry, and skips a session that is live anywhere ("already live
  at @N"). It asks again once the launch comes up. If another window switched to the session
  during the launch, restore closes the window it just opened and keeps the other one. (CMX-511)
