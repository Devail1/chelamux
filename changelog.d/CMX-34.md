### Changed

- **Sessions chela opens for a human now launch with a bare `--remote-control`.** chela
  passed a name, frozen at launch: the `shell-N` placeholder for a home-dir window, the folder
  name for a project. The claude.ai / desktop sidebar showed several sessions as "shell-3",
  and later tmux renames never reached it. With no name, the sidebar shows Claude's own
  generated session title; it already groups sessions by project folder. Applies to the
  dashboard launcher, Telegram `/new`, `chela spawn` and the orchestrator persona. Dispatched
  agents and judges still get no Remote Control. Running sessions keep their name until
  relaunched. (CMX-34)
