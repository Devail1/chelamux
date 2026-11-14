### Fixed

- **Custom sidebar groups now keep their sessions through a reboot or tmux restart.** A
  session's group was saved under its tmux window id, and a restore brings every session
  back under a new window id, so the groups came back empty. A session is now remembered
  by its Claude session id, which a restore keeps, so a resumed session lands back in its
  group on the first render. Assignments you already have are moved to the new key the
  first time their session is seen live. A `/clear`, which starts a new session in the
  same window, keeps the window's group. A session that is briefly missing (a restore
  still running) keeps its group. It is forgotten only after it has been neither running
  nor archived for 30 days. A row with no Claude session (a plain shell) is still keyed
  by its window and dropped when that window closes, as before. (CMX-378)
