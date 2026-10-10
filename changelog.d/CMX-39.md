### Changed

- **The Remote Control name in claude.ai / the Claude desktop now equals the chela window
  name, at launch and after every rename.** Every launcher (dashboard, Telegram `/new`,
  `chela spawn`, resume) names a new window after its folder — the cwd basename, or the
  login for the home dir, collision-safe `-N` — instead of a `shell-N` placeholder, and
  starts Claude Code with `--remote-control <window name>` (the auto-launched orchestrator
  passes `orchestrator`). A dashboard rename or a duplicate's `-N` suffix queues
  `/rename <new name>`, typed into the session whenever its prompt is empty, busy or idle (a
  ghost suggestion counts as empty; a typed draft, a permission dialog or an unreadable
  status holds it); the daemon retries a held rename every tick. A window launched before
  this, whose live claude runs `--remote-control` or has Remote Control on, gets renamed
  too. A rename made in the desktop app does not sync back. (CMX-39, CMX-70)
