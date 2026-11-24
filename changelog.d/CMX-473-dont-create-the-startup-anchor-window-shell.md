### Changed

- **No empty "Shell" session at startup.** Chela used to create its tmux session with a
  placeholder window, `shell-1`, which showed up on the sidebar, on the Wall and as a
  Telegram topic before you had started anything. Now the session is created by the first
  spawn (the dashboard launcher, Telegram `/new`, restore, a dispatched agent, the
  orchestrator), with the window it opens as the session's first window. Closing the last
  window ends the session, and the next spawn creates it again. The terminal wall never
  creates the session: a missing one just means no windows. `CHELA_TERM_HEAL` and
  `CHELA_TERM_BACKOFF_MAX` are gone with it. An existing `shell-1` is an ordinary shell you
  can close. (CMX-473)
