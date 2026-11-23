### Fixed

- **chela only ever drives the tmux session named exactly `chela`.** tmux matches a bare
  `-t chela` by prefix, so with no `chela` session but a `chela-services` one, every chela
  command used `chela-services`. All of chela's tmux targets are now exact (`=chela`,
  `=chela:@3`), and `chela status` exits with "no session chela" when it is missing.
- **The services (daemon, dashboard, telegram, collab) no longer pick their session from the
  tmux pane they were started in.** They drive `CHELA_TMUX_SESSION`, else `chela`, and log
  which session they use and why. Interactive agents still derive it from their own pane.
- **`chela doctor` warns** when a service was started inside a tmux pane, and when a session
  whose name starts with `chela` exists next to `chela`. `docs/ORG_SETUP.md` §1 says to start
  the services outside tmux (or set `CHELA_TMUX_SESSION`) and never to name a session
  `chela-*`. (CMX-470)
